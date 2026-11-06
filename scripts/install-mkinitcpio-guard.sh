#!/usr/bin/bash
# Install the package-owned mkinitcpio hook and retain it across pacman updates.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
config=/etc/pacman.conf
hook=/etc/pacman.d/hooks/90-mkinitcpio-install.hook
template=/usr/share/p16-platform/90-mkinitcpio-install.hook
[[ -s $template && -x /usr/lib/p16-platform/mkinitcpio ]] || {
    echo 'Install p16-platform-policy before configuring its update guard.' >&2
    exit 1
}
backup=/var/lib/p16-platform/boot-hook-backup
install -d -m 0700 "$backup"
[[ -e $backup/pacman.conf ]] || cp -a "$config" "$backup/pacman.conf"
if [[ -e $hook && ! -e $backup/90-mkinitcpio-install.hook ]]; then
    cp -a "$hook" "$backup/90-mkinitcpio-install.hook"
fi
if ! pacman-conf NoUpgrade | grep -Fxq 'etc/pacman.d/hooks/90-mkinitcpio-install.hook'; then
    sed -i '/^\[options\]$/a NoUpgrade = etc/pacman.d/hooks/90-mkinitcpio-install.hook' "$config"
fi
install -Dm644 "$template" "$hook"
