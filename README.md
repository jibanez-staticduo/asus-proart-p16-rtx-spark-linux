# ASUS ProArt P16 on Linux

Kernel packages, ACPI preparation and platform drivers for the ARM64 ASUS
ProArt P16 H7607BA. Board configuration targets BIOS H7607BA.300. The kernel
modules match ACPI devices and firmware interfaces; they do not use ASUS
product-name or BIOS checks.

## Components

| Path | Purpose |
| --- | --- |
| `kernel/` | Pinned Linux recipe, upstream patches and P16 integration patches |
| `acpi/` | Attributed ACPI builder and verified input transformations |
| `platform/` | DSP, SCP, Pluton, SPI HID and HID ALS drivers |
| `packages/` | Platform policy and locally prepared firmware PKGBUILDs |
| `config/` | mkinitcpio, Limine, module, udev and systemd policy |
| `scripts/` | Source preparation, build tools and manual diagnostics |
| `upstream/spi-hid/` | Dependency-pinned SPI HID review series |

See [driver status](docs/platform-driver-status.md),
[matching policy](platform/README.md) and
[upstream requirements](docs/upstream-readiness.md) for scope and limitations.
The [GPU power measurements](docs/gpu-power.md) cover the bounded SysPL3
experiment and its qualification limits.
The [native vLLM setup](docs/vllm-qwen38.md) records the Unsloth NVFP4 and
DFlash2 configuration; model execution and throughput remain unvalidated.

## Build

Use an AArch64 Arch Linux build environment with `base-devel`, `dkms`,
`python`, `binutils`, `libarchive` and the target kernel headers. Kernel builds
also require the dependencies in `kernel/PKGBUILD`. Build packages as a normal
user; installation is a separate root operation.

```sh
python3 -m unittest discover -s tests -v
make -C platform KDIR=/usr/lib/modules/$(uname -r)/build W=1 -j2
```

DSP, SCP and Pluton support normal Kconfig/Kbuild integration and external
module builds. Each driver's PKGBUILD supplies DKMS packaging. See
[platform build instructions](platform/README.md) for native KUnit tests.

Prepare board firmware locally:

```sh
sudo scripts/dump-acpi.sh
python3 scripts/build-acpi.py
python3 scripts/peripherals.py prepare
make -C packages MAKEPKG='makepkg --nodeps'
```

The package build skips runtime dependency installation; install the resulting
packages together. Source checksums remain enforced.

The ACPI package contains only the generated SSDT. A captured DSDT has
boot-dependent memory addresses and must not be installed into an initramfs.
OEM firmware and machine table dumps stay outside Git; package checksums
verify the local inputs and do not grant redistribution rights.

Build the kernel with `scripts/build-kernel.sh`. The recipe creates
`linux-p16-platform` and matching headers. For isolated candidate builds,
`scripts/prepare-kernel-candidate.py --output DIR --build` stages all source
and build output in a new directory. Signed upstream patches retain their
original authorship and notices.

## Installation and updates

Use `pacman -U` for the resulting firmware, policy, kernel/header and DKMS
packages. Do not mix modules from different kernel releases. Configure the
packaged update guard before the next package transaction:

```sh
sudo /usr/lib/p16-platform/install-guard
sudo systemctl enable p16-charge-limit.service
```

The battery policy writes 100 to existing `charge_control_end_threshold`
attributes through systemd-tmpfiles. Its udev rule also handles battery
appearance. Adjust the policy through the normal systemd configuration
override directories if a different charge limit is wanted.

The NPU/SROOT provider has a separate package and boot service; see
[update procedures](docs/update-safe.md). DSP boot and playback remain
experimental and are disabled by default. Published DSP memory stays retained
until reboot because verified whole-core shutdown is unfinished.

Keep a tested recovery image and menu entry before selecting a new kernel.
A successful build is insufficient to promote a candidate. Check display,
USB/cameras, CPU topology, audio, sensors and numerical NPU results after boot.
The [update guide](docs/update-safe.md) describes the package hooks and checks.

## Sources and licenses

Input versions and hashes are recorded in `sources.json`,
`config/platform-sources.json`, `config/platform-oem.json` and driver-local
`provenance.json` files. Protocol notes describe verified fields and remaining
unknowns. Python is used for input preparation, offline tests and diagnostics;
firmware transport, memory ownership and playback callbacks run in C drivers.

See [LICENSES.md](LICENSES.md) for source and OEM asset terms. Contributor
attribution, license headers and existing patch sign-offs are preserved.
