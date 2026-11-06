# Update-safe P16 installation on H7607BA.300

This machine keeps the currently working P16/Omarchy kernel and the experimental
NPU kernel in separate boot entries. The latter must only be replaced after a
kernel, module and hardware qualification. A successful DKMS build is not a boot
test.

## Owned local state

`packages/build-local.py` snapshots the currently installed, audited files and
builds three local Arch packages in `~/.cache/p16-local-packages/`:

- `p16-platform-policy`: ACPI tables and hook, P16 mkinitcpio/keyboard/boot
  policy, touchpad quirk, services and tools.
- `p16-speaker-firmware`: the six Cirrus files for this machine. The three real
  files are checked against `config/speaker-firmware.json`; the other three are
  symlinks. Vendor firmware is read from the installed machine, not committed
  to Git.
- `p16-mayflower`: Plymouth theme, pinned logo source, refresh script and
  Pacman hook. The selected theme remains in Plymouth's own config and the
  refresh hook reapplies it after Plymouth/Omarchy settings upgrades.

After changing a local policy file, rebuild these packages and reinstall the
affected package with `pacman -U`. Keep the resulting package archives in a
separate backup: the build script uses this machine's ACPI and vendor assets.
The installed package archives are copied to `/var/lib/p16-platform/packages/`
on this machine.
`pacman -Qk p16-platform-policy p16-speaker-firmware p16-mayflower` verifies
their payload after an update.

## Boot-image policy

The upstream `90-mkinitcpio-install.hook` was installed in `/etc` by
`limine-mkinitcpio-hook`. Its broad firmware trigger would rebuild *every*
installed kernel, including the NPU experiment. `scripts/install-mkinitcpio-guard.sh`
replaces this one hook with `config/90-mkinitcpio-install.hook` and adds a
`NoUpgrade` entry to `/etc/pacman.conf`. The original files are saved in
`/var/lib/p16-platform/boot-hook-backup/`. The replacement calls
`p16-mkinitcpio-managed`, which rebuilds only package-managed Limine entries.
Standalone experimental UKIs are frozen during routine updates.

`NoUpgrade` causes a future upstream change to appear as a `.pacnew`; review it
and adapt the local hook if its trigger set changes. Run
`python3 scripts/check-update-state.py` after Omarchy updates. The user-owned
Omarchy post-update hook runs that check automatically, but a post-transaction
check cannot undo an already finished transaction.

Omarchy channel changes replace `/etc/pacman.conf`. The installed
`pre-refresh-pacman` hook reapplies this policy immediately after the refresh.
The package-owned `00-p16-guard-preflight.hook` blocks a Pacman transaction if
the policy is still missing. This prevents a broad initcpio rebuild from
silently rewriting the experimental UKIs.

## NPU and out-of-tree modules

The audited NPU bootstrap module is packaged separately in `n1x-lab/npu`.
NVIDIA 615.78.08, SPBM `0.3.0.rtxspark.5`, and CPUID 0.8.2 are installed via
DKMS for the current NPU kernel. Their `srcversion` and `vermagic` matched the
manual test modules before migration; the originals are backed up under
`/var/lib/p16-platform/boot-hook-backup/npu-test-modules/`. Do not remove a
loaded module or rewrite the current NPU UKI while proving the replacement.
A new kernel needs its own
versioned package, DKMS builds, staged UKI, explicit boot selection, and a
hardware test before being made the default. Keep `p16-known-working` intact
as recovery.

## Native platform driver persistence (November 2026)

The production path uses C kernel modules, DKMS, mkinitcpio, pacman hooks and
systemd. Python in this repository is for development, source preparation and
manual diagnostics; it is not installed as a new boot/update framework.

`n1x-sroot-provider-dkms` owns `n1x-platform-npu.service` and a small Bash helper.
Enable it with `systemctl enable n1x-platform-npu.service` and disable the old
`n1x-npu.service` without stopping a running experiment. The helper checks BIOS
300 and both isolated SMMU groups before selecting DMA domains. The C driver
validates firmware, resources and coherency. Startup has no kernel-release
allowlist. Readiness failures stay visible in `systemctl status`/the journal;
there is no forced reset, retry or DSP boot during normal startup.

`kernel/PKGBUILD` builds `linux-p16-platform` and matching headers, with the
ARM64 ACPI allocation fix, NPU noncoherent-DMA fix and native SPI sensor fixes.
The headers are a required dependency so DKMS cannot silently miss a kernel
update. For the next package release, set `P16_KERNEL_RELEASE=25` for **all**
makepkg invocations. The stable kernel/header package names upgrade normally;
the three generation-23 recovery packages remain installed. Updating the
upstream base also requires rebasing/testing the patch set.
This recipe is currently based on Linux 7.2.8, not the unqualified stock 7.3
kernel. Stock kernels lacking the strong `arch_alloc_acpi_tables` implementation
do not receive P16 ACPI overrides.

The normal pacman order is `70-dkms-install`, `90-mkinitcpio-install`, then
`95-p16-platform-update-check`. New P16 packages are discovered from `pkgbase`
even before their first boot entry exists. The three generation-23 bring-up
kernels remain frozen recovery images. The postcheck verifies target-kernel
module ABIs/dependencies and DKMS overrides, the UKI kernel payload, the ASUS
SSDT, absence of a static DSDT, and the exact embedded SPI-HID modules. A failed post-transaction hook
reports an incomplete update; it does not roll back pacman. Keep the recovery
kernels and entries until the candidate boots and passes hardware checks.

After an update:

```sh
sudo p16-platform-update-check --all
systemctl status n1x-platform-npu.service
journalctl -b -u n1x-platform-npu.service
modinfo -k "$(uname -r)" n1x_npu_bootstrap
```

Use Limine's supported one-shot entry selection for the first candidate boot:
`sudo bootctl set-oneshot Omarchy.linux-p16-platform`. The existing default
remains available on the following boot. Unlock the encrypted root disk locally.
Only promote a candidate to the default after observing the new `uname -r`,
service startup, ALS/SCP/Pluton enumeration and a correct numerical NPU workload.
DSP firmware boot remains an explicit experiment: published DMA is pinned until
reboot and verified DSP shutdown/audio/compute service integration is unfinished.

## Missing USB after a firmware memory-layout change (November 5, 2026)

The frozen DSDT override declares `GNVS` at physical address `0x86f50000`.
On the affected boot the firmware's unmodified `CLAudio` SSDT occupies
`0x86f30018..0x86f56c64`, so that fixed GNVS address falls inside another
ACPI table. All six USB controller `_STA` values were zero, no USB buses or
camera nodes appeared, and the NPU device was absent. This is below browser
camera permissions. Restoring devices by forcing `_STA` values or writing to
the stale memory region has not been qualified and must not be used.

The same stale GNVS bytes also affect CPU enumeration. On that boot
`C0CE/C0BE/C1CE/C1BE` read `01 12 26 02` from the audio table. Their low five
bits predict precisely the registered CPU devices `0,6,9,11,12,16`, although
all 20 CPUs remain online. Missing CPU devices break topology sysfs creation;
kind nodes inherit the incomplete host sysfs, and their kubelets abort with
`could not detect number of cpus`. Validate all 20 CPU devices and topology
attributes after the native-DSDT test boot before changing cluster resources.

`scripts/stage-native-dsdt.py` builds an isolated boot test from an existing
hashed ARM64 UKI. It renames only the early CPIO DSDT override outside the
kernel's ACPI-override directory, retains the ASUS SSDT, and verifies that the
kernel, other sections, CPIO payloads and compressed main initramfs did not
change. The source must contain the pinned overrides and exactly one
`acpi_osi="!Windows 2022"` option: that option already makes the original
firmware SSPM `_STA` predicate take the same branch as the DSDT patch.
Installation requires `n1x-platform-npu.service` to already be disabled or
masked, keeping the failed experimental NPU startup out of the USB/display
qualification. The existing module blacklists remain necessary. The kernel
command line is kept byte-identical, including on tightly packed UKIs.

Build a candidate without installing it:

```sh
sudo python3 scripts/stage-native-dsdt.py \
  --source /boot/EFI/Linux/omarchy_linux-rtx-spark-npu.efi \
  --output /path/to/new/native-dsdt-test.efi
```

Use a fresh output path and add `--install --select-once` to append and select
the separate `p16-native-dsdt-test` entry for one boot. Installation refuses
signed/PCR-signed images, enabled Secure Boot, an unexpected machine/BIOS,
changed source hashes, and an existing test entry. It saves the menu under
`/var/lib/p16-platform/native-dsdt-*` and keeps existing images and the default
entry unchanged. To cancel an unused one-shot selection, run
`sudo bootctl set-oneshot ''`. A failed test can return to the existing default
on the following boot; keep `p16-known-working` intact.

The test is not qualified until an observed reboot confirms working USB host
controllers, built-in and attached USB cameras, display/input, audio and
power controls. Do not promote it or remove recovery entries based only on a
successful image build. Live NVIDIA display-module unloading caused a GSP
RPC timeout (Xid 119) on this machine and must not be used for this recovery.

The November 5 test boot was observed with the original revision-4 firmware
DSDT, all 20 CPU devices and their topology attributes, the built-in ASUS and
external 5MP USB cameras, a working Hyprland desktop at 3840x2400@120, PipeWire
audio devices, and both `maistack` and `amicable` nodes Ready with CPU capacity
20. `scripts/promote-native-dsdt.py` installed policy package 1.1.4-1, set the
tested entry as both the menu and persistent EFI default, and verified that
all recovery image hashes stayed unchanged. The managed generation-24 image
was rebuilt and its native-DSDT payload verified, but was not promoted or
qualified by a generation-24 reboot. The experimental NPU service remains
disabled. MAIS-306 E2E was not run as part of the host repair.

## Boot-menu cleanup after native-DSDT qualification (November 5, 2026)

After a second successful native-DSDT boot, `scripts/cleanup-boot.py` replaced
the bring-up menu with two standalone entries: `Omarchy-P16` (the default)
and `Omarchy-P16-Recovery`. Their images, `omarchy_p16.efi` and
`omarchy_p16-recovery.efi`, are byte-identical copies of the observed working
kernel-23 native-DSDT image, SHA256
`f51024bb84598bbf5b3b792cb673f1e178a7a731e449c9716acb3b2f952dd714`.
The recovery copy is frozen; candidate kernels must be staged and qualified
separately before changing the primary image and its menu hash.

Six superseded stock, bring-up and test images were hash-verified into
`/var/lib/p16-platform/boot-cleanup-cbwthkb2/EFI/Linux` before removal from the
ESP. The original menu, inventory and EFI default are in the same archive.
Net ESP space reclaimed was 789896192 bytes (753 MiB). Kernel/module packages
remain installed; the managed generation-24 image remains on the ESP for
update verification but has no menu entry and is not reboot-qualified.
The fallback EFI loader binary was preserved; only its redundant menu item
was removed.

Policy package 1.1.5-1 preserves both qualified entries and removes regenerated
legacy Omarchy/test/fallback groups after relevant updates, while retaining
unrelated operating-system entries. `zz-p16-clean-menu.conf` also disables EFI
loader discovery that previously added the Limine self-loop. The five-second
menu timeout remains. Boot-image hashes, module dependencies, managed-image
checks and installed package files passed verification after cleanup.
