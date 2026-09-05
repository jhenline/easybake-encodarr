#!/bin/sh
# Run as root on Ubuntu 22.04 (jammy). Adds Jellyfin's apt repo and installs
# jellyfin-ffmpeg only — not jellyfin-server.
set -e

apt-get install -y curl gnupg software-properties-common
add-apt-repository -y universe

mkdir -p /etc/apt/keyrings
curl -fsSL https://repo.jellyfin.org/jellyfin_team.gpg.key \
  | gpg --dearmor -o /etc/apt/keyrings/jellyfin.gpg
chmod a+r /etc/apt/keyrings/jellyfin.gpg

VERSION_OS="$(awk -F= '/^ID=/{ print $NF }' /etc/os-release)"
VERSION_CODENAME="$(awk -F= '/^VERSION_CODENAME=/{ print $NF }' /etc/os-release)"
DPKG_ARCHITECTURE="$(dpkg --print-architecture)"

cat >/etc/apt/sources.list.d/jellyfin.sources <<EOF
Types: deb
URIs: https://repo.jellyfin.org/${VERSION_OS}
Suites: ${VERSION_CODENAME}
Components: main
Architectures: ${DPKG_ARCHITECTURE}
Signed-By: /etc/apt/keyrings/jellyfin.gpg
EOF

apt-get update
apt-get install -y jellyfin-ffmpeg7

echo "Installed:"
/usr/lib/jellyfin-ffmpeg/ffmpeg -hide_banner -version | head -n 1
/usr/lib/jellyfin-ffmpeg/ffmpeg -hide_banner -encoders 2>/dev/null | grep -E 'hevc_vaapi|hevc_qsv|libx265' || true
echo "Point EasyBake at:"
echo "  ffmpeg: /usr/lib/jellyfin-ffmpeg/ffmpeg"
echo "  ffprobe: /usr/lib/jellyfin-ffmpeg/ffprobe"
