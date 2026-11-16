# Installing on TrueNAS SCALE

Tested layout: TrueNAS SCALE 24.10 "Electric Eel" or newer (Docker-based apps). On 24.04 and older
(Kubernetes apps), use *Launch Docker Image* with the same values from the table in the main README.

## 1. Sound card and audio group

In **System → Shell** (as root):

```bash
getent group audio               # audio:x:29:   -> use 29 in group_add
cat /proc/asound/cards           # optional: see which cards the NAS has
#  0 [Generic  ]: HDA-Intel - HD-Audio Generic      <- GPU (HDMI)
#  1 [Generic_1]: HDA-Intel - HD-Audio Generic      <- motherboard (analog out)
```

### Automatic sound card (default)

You don't have to pick the card. Rolola finds it on every start, and checks again every minute:

* It recognises the card by its **hardware address** (PCI/USB slot), not by its ALSA name. Names like
  `Generic` and `Generic_1` swap places when the kernel detects the cards in a different order after a
  reboot, which is why a fixed `AUDIO_DEVICE` sometimes lost the sound.
* With nothing remembered yet, it prefers **analog or USB** outputs, then S/PDIF, and uses **HDMI** last.
* If the card disappears or the sound fails, it looks again and switches by itself, for example when the
  driver loads late or a USB DAC is plugged back in.

After installing, open **Admin → Audio & diagnostics**:

* **Sound card selection: Automatic** (default). The line *Using …* shows the chosen output and why.
* If it picked the wrong output, choose the right one under **Preferred output** and play the test
  tone. That card is remembered by its hardware address, so it is found again after every reboot.
* **Sound card selection: Manual** works the old way: Rolola always uses exactly the device you type, for
  example `plughw:CARD=Generic_1,DEV=0`, and never changes it.

In automatic mode `AUDIO_DEVICE` in the YAML is only a tie-breaker, so you can leave it as it is.
`AUDIO_DEVICE: "null"` still means no sound at all. To start in manual mode, add
`ROLOLA_DEFAULT_AUDIO_DEVICE_MODE: "manual"` to `environment`. The switch in the admin panel overrides it.

### Sound card volume (muted after every reboot)

TrueNAS doesn't save the sound card's own volume (the ALSA mixer). After a reboot, Realtek chips such as
the ALC887 often come back with **`Master` muted at 0 %**: Rolola plays, but nothing comes out.

Rolola fixes this by itself on every start. It unmutes the card's playback controls (`Master`, `Front`,
`PCM`, `Headphone`…), sets them to **90 %**, and turns off Realtek *Auto-Mute*, which silences the rear
jack when it thinks headphones are plugged in. Change the level in *Admin → Audio & diagnostics →
Sound card volume (hardware)*. *Unmute & apply now* does it immediately, and `0` means Rolola never
touches the card's mixer. Rolola's own volume slider still controls the actual loudness.

To check it by hand (`Master` must say `[on]`, not `[0%] [off]`):

```bash
sudo docker exec ix-rolola-rolola-1 amixer -c Generic_1 sget Master
sudo docker exec ix-rolola-rolola-1 amixer -c Generic_1 sset Master 90% unmute   # manual fix
```

Optional quick check from the host (manual mode, or to find the right card by ear):
`speaker-test -D plughw:CARD=Generic_1,DEV=0 -c 2 -t sine -l 1`.

## 2. Create the datasets

* `tank/apps/rolola`: app data. **Datasets → Add Dataset** (preset *Apps*), then **Edit Permissions**:
  owner user `apps` (568), group `apps` (568). Or in the shell: `chown 568:568 /mnt/tank/apps/rolola`.
* Your music dataset (for example `tank/media/music`) only needs to be **readable** by user 568.

## 3. Install

**Apps → Discover Apps → ⋮ (top right) → Install via YAML**. Name it `rolola`, paste
[`compose.yaml`](compose.yaml), edit the `# <-- EDIT` lines and save. TrueNAS pulls the image and starts it.

Open `http://<truenas-ip>:8080`. If you didn't set `ADMIN_PASSWORD`, read the generated one:

```bash
cat /mnt/tank/apps/rolola/admin-password.txt
```

Then *avatar → Admin login → Admin → Play test tone*.

## 4. Update

The YAML uses the `edge` image with `pull_policy: always`. Every push to the repository builds a new
`edge` image on GitHub (*Actions → ci*; wait until the run is green, which takes a few minutes).

1. **Apps → Installed → rolola → Edit** → change nothing (or adjust the YAML) → **Save**. TrueNAS
   recreates the container and, because of `pull_policy: always`, downloads the newest `edge` image.
   If the app page shows an **Update** button instead, that does the same.
2. If it is still the old version (for example, *Admin → Audio & diagnostics* has no *Sound card selection*), pull by hand
   in **System → Shell** and restart the app:

   ```bash
   docker pull ghcr.io/aa-eion/rolola:edge
   ```

   then **Apps → rolola → Stop**, then **Start**.

Queue, history, settings and logins are in the `rolola_data` volume and survive updates. yt-dlp also
updates itself daily inside `/data/ytdlp`, so YouTube fixes don't wait for a new image.
To pin a fixed release instead of `edge`, use a version tag such as `ghcr.io/aa-eion/rolola:1.0.0`
and change it here when you want to update.

## Notes

* No host networking needed: Rolola only needs one TCP port.
* If port 8080 is taken, change the left side: `"8090:8080"`.
* ZFS snapshots of `tank/apps/rolola` are a complete backup (see the main README).
* Diagnostics: *Admin → Audio & diagnostics* shows what the container sees (`/dev/snd`, groups, cards) and
  every output Rolola found, with the one in use.
