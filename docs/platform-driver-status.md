# P16 platform driver status

This records implemented interfaces and observed hardware results. Loading a
module, accepting firmware and providing a usable service are separate results.

| Component | Implemented and observed | Missing qualification or implementation |
| --- | --- | --- |
| NVIDIA GPU | Display and CUDA; installed modules match userspace | Suspend/resume and codec qualification are separate from desktop operation |
| ASUS controls/input | Keyboard, touchpad, battery and platform controls; current modules loaded | Physical charge cutoff and fan response need measurement |
| `spbm` | Power and temperature telemetry; current module loaded | Writable power limits require their own bounded workload validation |
| SPI HID sensors | Patched transport and Linux IIO ALS/proximity devices; ALS samples obtained | Controlled optical response and calibration |
| SCP | Firmware version, sensor descriptors and firmware log | An SCP sensor streaming client integrated with IIO |
| Pluton | ROT1/PFI transport and channel discovery | PCBI service implementation and validation |
| NPU/SROOT | Accelerator interface, numerical inference, shared DSP upload and checked NPU shutdown tested on earlier boots | Fresh-boot validation of the November 5 lifecycle fixes on the native-DSDT boot |
| MT8901 DSP | Three signed images accepted and three firmware-ready notifications observed on an earlier boot | Service IPC, Linux audio/compute endpoints, verified shutdown and DMA release, suspend/resume, and reboot qualification |

## November 5 native-DSDT NPU startup failure

The attempt at 20:22 CET reached HFRP phase 1 and timed out with `-ETIMEDOUT`.
The kernel retained the module and runtime-PM reference. No SROOT firmware
upload, DLA task, or DSP firmware boot was attempted. Retrying or replacing
that module instance would discard an uncertain hardware state.

The source audit found that `n1x_boot_delay()` used the DLA interrupt path during
HFRP setup, before HFRP had established DLA power. The callback now uses a
sleep until power is confirmed, and avoids interrupt re-enablement during
shutdown. The test compiles the actual callback and rejects DLA MMIO in both
states; it failed against the previous implementation.

The shared loader also now rejects DSP uploads once NPU shutdown has begun.
Shutdown checks for published DSP buffers under the same lock before changing
accelerator state. There is no DSP buffer-release contract yet, so it returns
`-EBUSY` while such buffers exist. Tests exercise all three buffer slots and
verify that refusal performs no MMIO, DMA release or lifetime-state change.

The NPU service's stop action now requests the kernel's checked shutdown,
requires confirmed hardware-off and released references, then unloads the
module. An unsuccessful check leaves the module in place. The installer audits
all Linux UKIs, including renamed primary/recovery images, and checks the host
without changing IOMMU domains or restarting a loaded instance.

These changes are in provider package `0.1.0-4`. A successful build and portable
tests do not establish that the live HFRP failure has been resolved. That
requires a new boot, successful service startup and numerical inference.

The package is installed for all five installed kernel releases, with matching
module source version `AA4C27A61D0319E02A71961`. The NPU service is enabled and
the legacy service disabled. The old module remains resident for this boot;
no replacement or second handshake was attempted. Package-file checks and
all EFI/menu hashes passed after installation. The changed kernel code passes
`checkpatch.pl` with no errors or warnings and builds with `W=1` without warnings.

The next checkpoint is a reboot into `Omarchy-P16`, followed by:

```sh
systemctl status n1x-platform-npu.service
sudo python3 scripts/verify-platform-install.py \
  --after-boot-id 25df30f5-492e-4a3c-a10e-170f4376b8b7 \
  --report /path/to/new-post-boot-report.json
```

That verifier does not start the service itself. If startup fails, inspect the
retained status and kernel messages before any further hardware requests.

## DSP completion criteria

The current `n1x_adsp` code explicitly reports `services=0`. Its only firmware
request is secure boot; its receive handler recognizes the `0xf002` boot-ready
notification. This is insufficient for normal DSP operation.

The OEM reference is `nvadsp.sys` 2.13.0.1, pinned in
`platform/dsp/provenance.json`. Its packet transport, service interfaces and
power transitions must be recovered and tested before exposing Linux services.
The existing MT8901 audio route uses the stock driver and remains independent
of this experimental firmware-loader module. Replacing that route with a
firmware-ready notification would remove working functionality.

Full support requires a request/reply transport with bounded completion and
validated service discovery; a verified quiesce/power-off protocol before DMA
release; suspend/resume and reinitialization; and actual audio/compute workloads
through appropriate Linux interfaces. Unknown commands, guessed resets and
unconditional autostart do not meet those criteria.
