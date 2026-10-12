# Installing on TrueNAS SCALE

Tested layout: TrueNAS SCALE 24.10 "Electric Eel" or newer (Docker-based apps). On 24.04 and older
(Kubernetes apps), use *Launch Docker Image* with the same values from the table in the main README.

## 1. Find the sound card and the audio group

In **System → Shell** (as root):

```bash
cat /proc/asound/cards
#  0 [HDMI     ]: HDA-Intel - HDA ATI HDMI
#  1 [Generic_1]: HD-Audio Generic - HD-Audio Generic      <- the analog output
aplay -l | grep Generic_1        # shows the device numbers (usually DEV=0)
getent group audio               # audio:x:29:   -> use 29
```

Your device string is `plughw:CARD=<name in brackets>,DEV=<device>`, e.g. `plughw:CARD=Generic_1,DEV=0`.
Use the **name**, never the number: card numbers can change between reboots.

Optional quick check from the host: `speaker-test -D plughw:CARD=Generic_1,DEV=0 -c 2 -t sine -l 1`.

## 2. Create the datasets

* `tank/apps/jukebox`: app data. **Datasets → Add Dataset** (preset *Apps*), then **Edit Permissions**:
  owner user `apps` (568), group `apps` (568). Or in the shell: `chown 568:568 /mnt/tank/apps/jukebox`.
* Your music dataset (for example `tank/media/music`) only needs to be **readable** by user 568.

## 3. Install

**Apps → Discover Apps → ⋮ (top right) → Install via YAML**. Name it `jukebox`, paste
[`compose.yaml`](compose.yaml), edit the `# <-- EDIT` lines and save. TrueNAS pulls the image and starts it.

Open `http://<truenas-ip>:8080`. If you didn't set `ADMIN_PASSWORD`, read the generated one:

```bash
cat /mnt/tank/apps/jukebox/admin-password.txt
```

Then *avatar → Admin login → Admin → Play test tone*.

## 4. Update

**Apps → jukebox → Edit**, change the image tag (e.g. `1.0.0` → `1.1.0`), save. Queue, history,
settings and logins are in `/data` and survive. yt-dlp also updates itself daily inside `/data/ytdlp`,
so YouTube fixes don't wait for a new image.

## Notes

* No host networking needed: the jukebox only needs one TCP port.
* If port 8080 is taken, change the left side: `"8090:8080"`.
* ZFS snapshots of `tank/apps/jukebox` are a complete backup (see the main README).
* Diagnostics: *Admin → Audio & diagnostics* shows what the container sees (`/dev/snd`, groups, cards).
