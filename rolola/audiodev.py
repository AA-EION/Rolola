"""Find the right ALSA output automatically.

Why: ALSA card *names* are not stable either.  Two cards with the same driver
name (typically the motherboard's analog "HD-Audio Generic" and the GPU's HDMI
"HD-Audio Generic") become ``Generic`` and ``Generic_1`` in whatever order the
kernel probes them, so after a reboot ``plughw:CARD=Generic_1`` can point at
the HDMI card (silence) or not exist at all.

Auto mode therefore picks the output from what the hardware *is*:

1. the remembered card (its long name, e.g. "HD-Audio Generic at 0xfcd00000",
   which contains the PCI/USB address and survives reboots) and PCM name,
2. otherwise the best-looking playback PCM: analog / USB beats S/PDIF beats
   HDMI/DisplayPort; loopback/dummy cards are last,
3. the manually configured card name only breaks ties.

Everything reads ``/proc/asound`` (the host's, also inside a container).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

ASOUND = Path("/proc/asound")
_IRQ = re.compile(r"\s+irq\s+\d+\s*$", re.I)
_CARD_IN_DEVICE = re.compile(r"CARD=([^,\s]+)", re.I)
_DEV_IN_DEVICE = re.compile(r"DEV=(\d+)", re.I)
_HW_NUMERIC = re.compile(r"^(?:plug)?hw:(\d+)(?:,(\d+))?$", re.I)


def _stable(long_name: str) -> str:
    """Long name without the IRQ number (MSI IRQs may change between boots)."""
    return _IRQ.sub("", long_name or "").strip()


def read_cards(root: Path = ASOUND) -> list[dict[str, Any]]:
    """Cards from /proc/asound/cards: index, id, driver, name, long_name."""
    path = root / "cards"
    if not path.exists():
        return []
    cards = []
    lines = path.read_text(errors="replace").splitlines()
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not (line and line[0].isdigit() and "[" in line and "]" in line):
            continue
        ident = line[line.index("[") + 1 : line.index("]")].strip()
        rest = line.split("]:", 1)[1].strip() if "]:" in line else ""
        driver, _, name = rest.partition(" - ")
        long = lines[i + 1].strip() if i + 1 < len(lines) else ""
        cards.append({"index": int(line.split()[0]), "id": ident, "driver": driver.strip(),
                      "name": (name or rest).strip(), "long_name": long})
    return cards


def read_playback_pcms(root: Path = ASOUND) -> list[dict[str, Any]]:
    """Playback PCMs from /proc/asound/pcm: card index, device number, name."""
    path = root / "pcm"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(errors="replace").splitlines():
        m = re.match(r"^(\d+)-(\d+):\s*(.*)$", line.strip())
        if not m:
            continue
        parts = [p.strip() for p in m.group(3).split(" : ")]
        if not any(p.startswith("playback") for p in parts[2:]):
            continue
        out.append({"card": int(m.group(1)), "dev": int(m.group(2)), "pcm": parts[0] if parts else ""})
    return out


def _kind(card: dict, pcm: str) -> str:
    text = f"{pcm} {card['name']} {card['driver']} {card['id']}".lower()
    if card["driver"].lower() in ("loopback", "dummy", "aloop") or "loopback" in text or "dummy" in text:
        return "virtual"
    if re.search(r"hdmi|displayport|\bdp\b", pcm.lower()) or (not pcm and "hdmi" in text):
        return "hdmi"
    if re.search(r"iec958|s/?pdif|digital", pcm.lower()):
        return "digital"
    if "usb" in card["driver"].lower():
        return "usb"
    if "analog" in pcm.lower():
        return "analog"
    return "other"


_KIND_SCORE = {"analog": 40, "usb": 45, "other": 25, "digital": 10, "hdmi": -20, "virtual": -60}


def candidates(root: Path = ASOUND) -> list[dict[str, Any]]:
    """Every playback output, with the device string mpv should use."""
    cards = {c["index"]: c for c in read_cards(root)}
    pcms = read_playback_pcms(root)
    if not pcms:  # /proc/asound/pcm missing (unusual): assume device 0 of each card
        pcms = [{"card": i, "dev": 0, "pcm": ""} for i in cards]
    out = []
    for p in pcms:
        card = cards.get(p["card"])
        if not card:
            continue
        out.append({
            "device": f"plughw:CARD={card['id']},DEV={p['dev']}",
            "card_index": card["index"], "card_id": card["id"], "card_name": card["name"],
            "driver": card["driver"], "long_name": card["long_name"], "dev": p["dev"], "pcm": p["pcm"],
            "kind": _kind(card, p["pcm"]),
            "label": f"{card['name']} — {p['pcm'] or 'device ' + str(p['dev'])} ({card['id']})",
        })
    return out


def fingerprint(cand: dict[str, Any]) -> dict[str, Any]:
    """What we remember about a chosen output: survives renumbering and renaming."""
    return {"long_name": _stable(cand["long_name"]), "card_name": cand["card_name"],
            "driver": cand["driver"], "pcm": cand["pcm"], "dev": cand["dev"]}


def find(device: str | None, cands: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The candidate a device string (``plughw:CARD=X,DEV=n``, ``hw:1,0``…) refers to right now."""
    if not device:
        return None
    dev_m = _DEV_IN_DEVICE.search(device)
    card_m = _CARD_IN_DEVICE.search(device)
    num_m = _HW_NUMERIC.match(device.strip().removeprefix("alsa/"))
    for c in cands:
        if card_m and c["card_id"] == card_m.group(1) and c["dev"] == int(dev_m.group(1) if dev_m else 0):
            return c
        if num_m and c["card_index"] == int(num_m.group(1)) and c["dev"] == int(num_m.group(2) or 0):
            return c
    return None


def score(cand: dict[str, Any], pref: dict[str, Any] | None, manual: str | None) -> tuple[int, list[str]]:
    pts, why = _KIND_SCORE.get(cand["kind"], 0), [cand["kind"] + " output"]
    if pref:
        if pref.get("long_name") and _stable(cand["long_name"]) == pref["long_name"]:
            pts += 200
            why.append("remembered card")
        elif pref.get("card_name") == cand["card_name"] and pref.get("driver") == cand["driver"]:
            pts += 60
            why.append("same model as the remembered card")
        if pref.get("pcm") and pref["pcm"] == cand["pcm"]:
            pts += 80
            why.append("remembered output")
        elif pref.get("dev") == cand["dev"]:
            pts += 5
    if manual:
        card_m = _CARD_IN_DEVICE.search(manual)
        if card_m and card_m.group(1) == cand["card_id"]:
            pts += 3
            why.append("matches the manual device name")
    if cand["dev"] == 0:
        pts += 2
    return pts, why


def resolve(pref: dict[str, Any] | None, manual: str | None, root: Path = ASOUND) -> dict[str, Any]:
    """Pick the output.  Returns {device, reason, chosen, candidates}; device is None if no card was found."""
    cands = candidates(root)
    ranked = []
    for c in cands:
        pts, why = score(c, pref, manual)
        ranked.append({**c, "score": pts, "why": why})
    ranked.sort(key=lambda c: (-c["score"], c["card_index"], c["dev"]))
    if not ranked:
        return {"device": None, "reason": "no sound card found in /proc/asound", "chosen": None, "candidates": []}
    best = ranked[0]
    return {"device": best["device"], "reason": ", ".join(best["why"]), "chosen": best, "candidates": ranked}


# Hardware mixer controls that carry stereo playback to the jacks.  TrueNAS doesn't save the
# ALSA mixer state, so after a reboot e.g. 'Master' on Realtek ALC887 comes back muted at 0 %.
MIXER_CONTROLS = ("Master", "PCM", "Front", "Line Out", "Speaker", "Headphone")


def card_of(device: str | None, root: Path = ASOUND) -> str | None:
    """The ALSA card id a device string uses (``plughw:CARD=X,DEV=0`` -> X, ``hw:1,0`` -> its id)."""
    if not device:
        return None
    card_m = _CARD_IN_DEVICE.search(device)
    if card_m:
        return card_m.group(1)
    num_m = _HW_NUMERIC.match(device.strip().removeprefix("alsa/"))
    if num_m:
        for c in read_cards(root):
            if c["index"] == int(num_m.group(1)):
                return c["id"]
    return None


def fix_mixer(card: str, level: int) -> list[str]:
    """Unmute the playback controls of ``card`` and set them to ``level`` %; turn off
    Realtek "Auto-Mute" (it silences the rear jack when it thinks headphones are in).
    Returns what was changed; controls the card doesn't have are skipped."""
    amixer = shutil.which("amixer")
    if not amixer or not card:
        return []
    level = max(1, min(int(level), 100))
    done = []

    def run(*args: str) -> bool:
        try:
            return subprocess.run([amixer, "-q", "-c", card, *args], capture_output=True, timeout=5).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    for ctl in MIXER_CONTROLS:
        if run("sset", ctl, f"{level}%", "unmute"):
            done.append(f"{ctl} {level}% on")
    if run("sset", "Auto-Mute Mode", "Disabled"):
        done.append("Auto-Mute off")
    return done
