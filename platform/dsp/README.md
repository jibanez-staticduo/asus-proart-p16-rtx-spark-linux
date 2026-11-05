# Experimental MT8901 DSP boot

This module reuses MediaTek's existing SSPM power-management path and adds a
gated secure-loader and boot-ready experiment for the P16's two HiFi5 cores
and HiFi1 core. Audio routing, streaming and general compute services are
not implemented. On 2026-11-05 SROOT accepted all three pinned signed images,
and both HiFi5 cores plus HiFi1 sent the expected notification-class `0xf002`
ready event with error zero. Eight NPU numerical inference outputs were then
verified with all three DSPs running. Evidence is in
`../n1x-lab/results/platform-enablement-20261105/dsp-boot-trial-2.json` and
`dsp-boot-post-npu-numerical/complete.json`. Reboot persistence remains pending.

`provenance.json` records the inspected OEM driver functions and pinned
inputs. `scripts/prepare-dsp-firmware.py` preserves the complete signed
images, validates the bounded CBOR/container layout and verifies all six
section SHA-384 digests against both COSE metadata records. These local
checks do not authenticate the signatures; that remains SROOT's job.

The Linux ACPI override exposes 16 KiB tails of shared SRAM at
`1a15c000`, `1a19c000` and `1a5dc000`. The OEM's larger resources end at the
same addresses. Only each final 1 KiB and the mailbox aperture are mapped.
The config words are at `end - 0x60` (DMA address) and `end - 0x58` (size).
The receive buffer is at `end - 0x3e0`. The OEM notification class is byte
19 high nibble 2; command `0xf002` at offset 22 signals firmware ready.
Two read-only power-cycle captures showed changing, nonzero SRAM config
words. The boot guard preserves a pending mailbox or valid existing DMA
window; it does not mistake uninitialized SRAM for a live publication.

Secure upload goes through the owning NPU module's serialized kernel API;
this module never maps SROOT. The compatible provider source is in the
sibling `n1x-lab/npu` repository. An older loaded NPU module causes an
explicit boot request to fail before power, DMA allocation or MMIO writes.
HiFi5-1 also requires HiFi5-0 readiness before attempting publication.

Before the first DRAM config write, the module holds runtime PM and module
references. Once published, DSP DRAM and the provider's firmware DMA buffer
are retained until reboot, including after timeout/rejection. There is no
automatic retry, guessed reset or unload. Published instances refuse system
sleep. This lifetime policy must be replaced by a verified DSP shutdown
protocol before enabling normal desktop operation with firmware boot.

Run the offline fault-injection tests:

```sh
python platform/dsp/test_driver.py
cd ../n1x-lab/npu
make test-portable
```

For a temporary binding trial with boot disabled, build against the running
kernel and run:

```sh
make -C /usr/lib/modules/$(uname -r)/build M="$PWD/platform/dsp" modules
pkexec /usr/bin/python "$PWD/scripts/validate-dsp-live.py" --test-probe \
  --report "$PWD/../n1x-lab/results/NEW-dsp-probe.json"
```

The trial requires all three stock DSP devices to be runtime-suspended with
zero usage, checks the target and module ABI, replaces only their power
driver temporarily, verifies the native driver's idle status, then restores
the installed driver. It records kernel messages and boot-artifact hashes.
It does not load firmware or replace the NPU module.

`--inspect` additionally reads the mailbox/config words under runtime PM,
without acknowledging a notification or writing a DMA descriptor.
`scripts/validate-sroot-provider.py` checks NPU shutdown/reload and numerical
inference. `scripts/validate-dsp-boot.py` performs the explicit firmware boot
trial; it never retries a published request and leaves DMA pinned until
reboot. Automatic DSP boot remains disabled for normal desktop operation.

The DKMS package keeps native autoload and firmware boot disabled. Build it
with `cd platform/dsp && makepkg -f`. Signed firmware remains staged under
`../n1x-lab/platform/firmware` until the secure-boot trial is prepared; the
module package does not install or activate firmware. The optional local
firmware package is built from `platform/dsp/firmware` after staging verified
images there with `scripts/prepare-dsp-firmware.py --dest platform/dsp/firmware`.

`scripts/prepare-sroot-provider.py` snapshots the reviewed sibling NPU driver
and headers into a hash-pinned DKMS recipe under `platform/dsp/provider`.
That package provides standard DKMS rebuilds for installed/future kernels and
owns `n1x-platform-npu.service` plus a small Bash startup helper. It checks BIOS
300, selects isolated DMA domains before modprobe, and verifies driver readiness.
The native driver validates hardware/firmware; there is no kernel-version
allowlist. Enable the new unit and disable the old `n1x-npu.service` without
stopping a published experiment. Normal startup leaves DSP firmware boot disabled.
See `docs/update-safe.md` for the kernel packaging and pacman/mkinitcpio flow.
