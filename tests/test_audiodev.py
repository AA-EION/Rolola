"""Automatic sound-card selection survives cards being renumbered/renamed between reboots."""

from rolola import audiodev

CARDS_BOOT_A = """\
 0 [Generic        ]: HDA-Intel - HD-Audio Generic
                      HD-Audio Generic at 0xfc980000 irq 88
 1 [Generic_1      ]: HDA-Intel - HD-Audio Generic
                      HD-Audio Generic at 0xfc900000 irq 89
"""
PCM_BOOT_A = """\
00-03: HDMI 0 : HDMI 0 : playback 1
01-00: ALC887 Analog : ALC887 Analog : playback 1 : capture 1
01-01: ALC887 Digital : ALC887 Digital : playback 1
"""
# next boot: probe order flipped, IRQs differ -> "Generic_1" is now the HDMI card
CARDS_BOOT_B = """\
 0 [Generic        ]: HDA-Intel - HD-Audio Generic
                      HD-Audio Generic at 0xfc900000 irq 91
 1 [Generic_1      ]: HDA-Intel - HD-Audio Generic
                      HD-Audio Generic at 0xfc980000 irq 90
"""
PCM_BOOT_B = """\
00-00: ALC887 Analog : ALC887 Analog : playback 1 : capture 1
00-01: ALC887 Digital : ALC887 Digital : playback 1
01-03: HDMI 0 : HDMI 0 : playback 1
"""


def _root(tmp_path, cards, pcm):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "cards").write_text(cards)
    (tmp_path / "pcm").write_text(pcm)
    return tmp_path


def test_best_guess_prefers_analog_over_hdmi(tmp_path):
    a = audiodev.resolve(None, "plughw:CARD=Generic_1,DEV=0", _root(tmp_path / "a", CARDS_BOOT_A, PCM_BOOT_A))
    assert a["device"] == "plughw:CARD=Generic_1,DEV=0"
    b = audiodev.resolve(None, "plughw:CARD=Generic_1,DEV=0", _root(tmp_path / "b", CARDS_BOOT_B, PCM_BOOT_B))
    assert b["device"] == "plughw:CARD=Generic,DEV=0"  # the stale manual name no longer wins


def test_remembered_card_is_found_after_renumbering(tmp_path):
    root_a = _root(tmp_path / "a", CARDS_BOOT_A, PCM_BOOT_A)
    chosen = audiodev.find("plughw:CARD=Generic_1,DEV=1", audiodev.candidates(root_a))  # S/PDIF picked by hand
    pref = audiodev.fingerprint(chosen)
    root_b = _root(tmp_path / "b", CARDS_BOOT_B, PCM_BOOT_B)
    assert audiodev.resolve(pref, None, root_b)["device"] == "plughw:CARD=Generic,DEV=1"


def test_no_cards(tmp_path):
    assert audiodev.resolve(None, "default", tmp_path)["device"] is None


def test_usb_dac_and_numeric_lookup(tmp_path):
    root = _root(tmp_path, CARDS_BOOT_A + """\
 2 [Device         ]: USB-Audio - USB Audio Device
                      C-Media USB Audio Device at usb-0000:00:14.0-2, full speed
""", PCM_BOOT_A + "02-00: USB Audio : USB Audio : playback 1 : capture 1\n")
    assert audiodev.resolve(None, None, root)["device"] == "plughw:CARD=Device,DEV=0"
    assert audiodev.find("hw:2,0", audiodev.candidates(root))["card_id"] == "Device"
