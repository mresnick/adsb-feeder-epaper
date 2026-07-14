#!/usr/bin/env bash
# Install the ADS-B e-paper display service on a Raspberry Pi (run with sudo).
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "run as root: sudo ./install.sh" >&2
    exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST=/opt/adsb-epaper

echo "==> enabling SPI"
if command -v raspi-config >/dev/null 2>&1; then
    raspi-config nonint do_spi 0
else
    # DietPi and other minimal images: set the overlay directly
    BOOTCFG=/boot/config.txt
    [[ -f /boot/firmware/config.txt ]] && BOOTCFG=/boot/firmware/config.txt
    if ! grep -Eq "^dtparam=spi=on" "$BOOTCFG"; then
        echo "dtparam=spi=on" >> "$BOOTCFG"
        echo "    SPI enabled in $BOOTCFG (takes effect after reboot)"
    fi
fi

echo "==> installing packages"
apt-get update -qq
apt-get install -y -qq python3-pil python3-spidev python3-gpiozero python3-lgpio

echo "==> copying to $DEST"
mkdir -p "$DEST"
cp -r "$SRC_DIR/adsb_epaper" "$SRC_DIR/lib" "$SRC_DIR/assets" "$DEST/"

echo "==> installing config (kept if already present)"
mkdir -p /etc/adsb-epaper
if [[ ! -f /etc/adsb-epaper/config.toml ]]; then
    cp "$SRC_DIR/config.example.toml" /etc/adsb-epaper/config.toml
fi

echo "==> installing systemd service"
cp "$SRC_DIR/adsb-epaper.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now adsb-epaper.service

echo
echo "done. check status with:  systemctl status adsb-epaper"
echo "logs:                     journalctl -u adsb-epaper -f"
echo "config:                   /etc/adsb-epaper/config.toml"
