# EasyBake Encodarr

Scan configured folders, skip files that are already efficient HEVC, and re-encode the rest to H.265. No Tdarr graphs, no FileFlows. Set it and walk away.

**macOS** defaults to FFmpeg `libx265` (Plex-friendly). Set `encode.encoder: videotoolbox` for faster Apple GPU encoding; those files often fail Intel hardware transcode. **Linux** with an Intel iGPU defaults to `hevc_vaapi` (Quick Sync via VA-API). Ubuntu 22.04’s `hevc_qsv` often cannot open Media SDK; VA-API uses the same GPU. Frames are padded to a multiple of 32 before encode (odd sizes like 1918×802 otherwise come out as garbage). Hardware encode falls back to `libx265`. Originals are replaced only after a finished, validated encode. Set `encode.replace_original: false` to keep the original and write `Movie.hevc.mkv` beside it while testing. Set `encode.test_mode: true` to encode **one** file then stop (skips already-processed titles first). Ctrl+C deletes the hidden temp file and never promotes a partial encode.

CLI: `easybake` (`hevc-encoder` still works as an alias).

## Requirements

- Python 3.12+
- [FFmpeg](https://ffmpeg.org) with `ffprobe`
  - macOS (Homebrew): `brew install ffmpeg`
  - Linux + Intel iGPU: distro `ffmpeg` is enough if `ffmpeg -encoders | grep hevc_vaapi` prints a line (Ubuntu 22.04’s package does). jellyfin-ffmpeg is optional, not required.

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

Ubuntu 22.04: stock `ffmpeg` includes `hevc_vaapi` (and `hevc_qsv`, which often fails to open MFX). Install `git`, Python 3.12 (deadsnakes if needed), `vainfo`, and `intel-media-va-driver-non-free`. Confirm:

```bash
ffmpeg -encoders | grep hevc_vaapi
vainfo
```

Clone as user `plex`:

```bash
sudo -u plex git clone https://github.com/jhenline/easybake-encodarr.git /opt/easybake-encodarr
cd /opt/easybake-encodarr
sudo -u plex python3.12 -m venv .venv
sudo -u plex .venv/bin/pip install -e .
sudo -u plex cp config.example.yml config.yml
```

Point `libraries` at the same paths Plex uses (under `/plex`). Keep encode temps on `/plex`, not the container root disk.

Install the timer (edit `User=` if Plex does not run as `plex`):

```bash
cp deploy/hevc-encoder.service deploy/hevc-encoder.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hevc-encoder.timer
```

First run: `test_mode: true` and `replace_original: false`, then:

```bash
sudo -u plex /opt/easybake-encodarr/.venv/bin/easybake scan -c /opt/easybake-encodarr/config.yml
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
