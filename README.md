# EasyBake Encodarr

Scan configured folders, skip files that are already efficient HEVC, and re-encode the rest to H.265. No Tdarr graphs, no FileFlows. Set it and walk away.

**macOS** defaults to FFmpeg `libx265` (Plex-friendly). Set `encode.encoder: videotoolbox` for faster Apple GPU encoding; those files often fail Intel hardware transcode. **Linux** also defaults to `libx265`. Ubuntu 22.04’s stock `hevc_qsv` often cannot open Media SDK, and stock `hevc_vaapi` can write HEVC that looks like garbage. For iGPU encodes, install [jellyfin-ffmpeg](https://github.com/jellyfin/jellyfin-ffmpeg) from Jellyfin’s apt repo, point `ffmpeg:` / `ffprobe:` at `/usr/lib/jellyfin-ffmpeg/`, and set `encode.encoder: vaapi` only after a short test clip looks right in IINA. Originals are replaced only after a finished, validated encode. Set `encode.replace_original: false` to keep the original and write `Movie.hevc.mkv` beside it while testing. Set `encode.test_mode: true` to encode **one** file then stop (skips already-processed titles first). Ctrl+C deletes the hidden temp file and never promotes a partial encode.

CLI: `easybake` (`hevc-encoder` still works as an alias).

## Requirements

- Python 3.12+
- [FFmpeg](https://ffmpeg.org) with `ffprobe`
  - macOS (Homebrew): `brew install ffmpeg`
  - Linux + Intel iGPU: Ubuntu 22.04 stock FFmpeg 4.4 is fine for **libx265**. For iGPU HEVC install **jellyfin-ffmpeg** from Jellyfin’s apt repo (not the Jellyfin server) and point `ffmpeg:` / `ffprobe:` at `/usr/lib/jellyfin-ffmpeg/`. See [Linux (Plex LXC)](#linux-plex-lxc).

## Setup (macOS / any machine)

```bash
cd easybake-encodarr
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp config.example.yml config.yml   # or config.yaml
```

Edit `config.yml` and set your library paths.

The `easybake` command lives in the virtualenv. Either activate it (`source .venv/bin/activate`) or run the launcher in this folder:

```bash
./easybake status
```

## Linux (Plex LXC)

Ubuntu 22.04: stock `ffmpeg` is enough for **libx265**. For the iGPU, add Jellyfin’s apt repo and install **only** `jellyfin-ffmpeg7` (do not install the `jellyfin` metapackage unless you want a second media server). `vainfo` and `intel-media-va-driver-non-free` should already be present.

```bash
bash /opt/easybake-encodarr/deploy/install-jellyfin-ffmpeg-ubuntu.sh
```

Or the same steps by hand (official [Jellyfin repo](https://jellyfin.org/docs/general/installation/advanced/manual/)):

```bash
apt install -y curl gnupg software-properties-common
add-apt-repository -y universe
mkdir -p /etc/apt/keyrings
curl -fsSL https://repo.jellyfin.org/jellyfin_team.gpg.key | gpg --dearmor -o /etc/apt/keyrings/jellyfin.gpg
chmod a+r /etc/apt/keyrings/jellyfin.gpg
cat >/etc/apt/sources.list.d/jellyfin.sources <<EOF
Types: deb
URIs: https://repo.jellyfin.org/ubuntu
Suites: jammy
Components: main
Architectures: amd64
Signed-By: /etc/apt/keyrings/jellyfin.gpg
EOF
apt update
apt install -y jellyfin-ffmpeg7
```

If `jellyfin-ffmpeg7` is not found, `apt-cache search jellyfin-ffmpeg` and install the newest `jellyfin-ffmpegN` package.

In `/opt/easybake-encodarr/config.yml`:

```yaml
encode:
  encoder: libx265          # switch to vaapi after a 10s test clip looks good
ffmpeg: /usr/lib/jellyfin-ffmpeg/ffmpeg
ffprobe: /usr/lib/jellyfin-ffmpeg/ffprobe
```

Test before a library scan (`nas` can run this binary):

```bash
/usr/lib/jellyfin-ffmpeg/ffmpeg -hide_banner -encoders | grep -E 'hevc_vaapi|hevc_qsv|libx265'
runuser -u nas -- /usr/lib/jellyfin-ffmpeg/ffmpeg -hide_banner -y \
  -init_hw_device vaapi=va:/dev/dri/renderD128 -filter_hw_device va \
  -i "/plex/film/Mayday (2026)/Mayday (2026).mkv" -t 10 \
  -vf 'format=yuv420p,pad=ceil(iw/32)*32:ceil(ih/32)*32,format=nv12,hwupload=extra_hw_frames=64' \
  -c:v hevc_vaapi -qp 22 -bf 0 -aud 1 -an \
  "/plex/film/Mayday (2026)/mayday-jf-ffmpeg-test.mkv"
```

Play that file in IINA. If it looks normal, set `encode.encoder: vaapi`, delete the test file, forget Mayday in the dashboard, and `easybake scan`. If QSV works on this build (`hevc_qsv` no longer errors), you can try `encoder: qsv` the same way. If both still look like garbage, stay on `libx265` with jellyfin-ffmpeg (often a better x265 than Ubuntu 4.4).

Work as **root** for git (`git pull` in `/opt/easybake-encodarr`), same as other repos. Use **HTTPS** and a GitHub personal access token (not your GitHub password), stored once with `git config --global credential.helper store`.

NFS `/plex` is owned by UID 1000 and squashes container root, so encodes still run as user `nas` (UID 1000). Install `deploy/easybake-lxc` as `/usr/local/bin/easybake` so you type `easybake scan` without `sudo -u`.

```bash
git clone https://github.com/jhenline/easybake-encodarr.git /opt/easybake-encodarr
cd /opt/easybake-encodarr
python3.12 -m venv .venv
.venv/bin/pip install -e .
cp config.example.yml config.yml
chown -R root:nas /opt/easybake-encodarr
chmod -R g+rwX /opt/easybake-encodarr
install -m 755 deploy/easybake-lxc /usr/local/bin/easybake
```

Point `libraries` at the same paths Plex uses (under `/plex`). Keep encode temps on `/plex`, not the container root disk. Put `state_db` somewhere `nas` can write (for example `/opt/easybake-encodarr/encoder.db` after the `chown` above).

Install the timer (`User=nas` in the unit):

```bash
cp deploy/hevc-encoder.service deploy/hevc-encoder.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hevc-encoder.timer
```

First run: `test_mode: true` and `replace_original: false`, then:

```bash
easybake scan -c /opt/easybake-encodarr/config.yml
```

Stop the Mac encoder before a full Linux scan so two machines do not process the same libraries.

## Usage

```bash
source .venv/bin/activate         # once per terminal session
easybake scan                     # one-shot scan
easybake scan --dry-run           # evaluate only
easybake watch                    # watch folders + dashboard
easybake status                   # processed library + space saved
easybake serve                    # dashboard (blocks this terminal)
easybake serve --background       # dashboard, keep using this terminal
easybake serve --stop             # stop a background dashboard
easybake retry                    # re-queue failed files
easybake reset -y                 # forget history (does not delete videos)
easybake clean --dry-run          # list leftover .encoding.mkv / .bak files
easybake clean --sidecars -y      # also delete *.hevc.mkv test outputs
```

Without activating the venv, prefix with `./` from this directory (`./easybake status`).

Edit library paths and encode settings in `config.yml`, or use the dashboard **Settings** tab. Forget a processed file or reset history from the dashboard (this does not delete videos).

Dashboard default: http://127.0.0.1:8745

## How a file is handled

1. Ignore incomplete/sidecar names (`._*` AppleDouble forks, `*.partial`, `*.!qB`, `*.encoding.mkv`, sample folders).
2. Wait until the file size is stable (watch mode).
3. Skip if SQLite already recorded that path + size + mtime (the processed library keeps original codec, bitrate, size, and the new size so you can see space saved over time).
4. Probe with `ffprobe`.
5. **Rules evaluator:** skip HDR (v1), skip low bits-per-pixel, skip efficient HEVC (unless `reconvert_hevc` and over the bitrate cap), encode H.264 / MPEG-2 / etc.
6. Encode to a hidden `.{name}.encoding.mkv` on the same volume, then remux with `-max_interleave_delta 0` so DTS-HD + PGS subtitle tracks stay seekable in VLC/IINA.
7. Validate that FFmpeg reported completion, duration matches (~1–2% or ±1s), and the new file is ≤ `max_size_percent` of the original.
8. If `replace_original` is true: atomic replace (original → `.bak`, then new file in, delete `.bak`). If false: write `Movie.hevc.mkv` beside the original. Interruptions restore the original and delete the temp file.

v1 skips HDR/Dolby Vision on purpose; VideoToolbox HDR metadata is a common source of washed-out encodes. A later VMAF evaluator can plug into the same `Decision` interface (`evaluate.mode: vmaf` is reserved).

## Tests

```bash
pytest
```
