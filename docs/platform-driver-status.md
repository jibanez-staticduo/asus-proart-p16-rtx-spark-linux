# P16 platform driver status

This records implemented interfaces and observed hardware results. Loading a
module, accepting firmware and providing a usable service are separate results.

The [upstream review](upstream-readiness.md) records source cleanup, offline
checks and the remaining submission blockers for each locally maintained driver.

| Component | Implemented and observed | Missing qualification or implementation |
| --- | --- | --- |
| NVIDIA GPU | Display and CUDA; installed modules match userspace | Suspend/resume and codec qualification are separate from desktop operation |
| ASUS controls/input | Keyboard, touchpad, battery and platform controls; current modules loaded | Physical charge cutoff and fan response need measurement |
| `spbm` | Power and temperature telemetry; current module loaded | Writable power limits require their own bounded workload validation |
| SPI HID sensors | Patched transport, Linux IIO devices and all five ALS raw channels; upstream per-channel scale fix validated on November 6 | Controlled optical response and calibration; rebase of transport fixes onto the selected HID series |
| SCP | Firmware version, sensor descriptors and firmware log; the VEML3328 measurement client uses SPI HID and the existing HID ALS IIO driver | No second mailbox IIO device is needed for the same physical sensor; additional firmware clients require their own protocol and device evidence |
| Pluton | ROT1/PFI transport and typed kernel-validated protocol/service/package queries, installed and hardware-verified on November 6; raw exchange disabled by default | Control-service lifecycle and upstream API review |
| NPU/SROOT | Native-DSDT automatic startup with the November 5 fixes and eight exact numerical outputs verified on November 6; shared DSP upload and checked shutdown tested on earlier boots | New shared-DSP release and lifecycle support depends on the unfinished DSP shutdown contract |
| MT8901 DSP | Three signed images accepted and ready on November 6; four primary-task message buffers registered; OPEN/HWFREE/CLOSE acknowledged; one queued SSPM power request completed; uninstalled 0.1.14 adds typed transitions, optional DL0 ALSA frontend and checked release of consumed loader staging | Staging-release and complete-stream hardware qualification, restart/additional endpoints, verified core shutdown and operating-memory/provider-lease release, suspend/resume and automatic startup qualification |

The uninstalled DSP `0.1.14-1` and provider `0.1.0-6` candidates separate
temporary loader staging from the retained DSP/provider lifetime lease. Only
the canonical successful `0x18` reply releases staging; uncertain/rejected
uploads retain it. NPU shutdown still refuses a published DSP lease even with
no staging allocation. All 136 platform tests, NPU `make check`, five DSP and
five NPU warning-free builds pass; both source archives and all eight local
recipe hashes were checked. The AFE sources remain unchanged from 0.1.13.
Receipts are in `build/dsp-staging-v2-20261106/report.json`. No new firmware
or power request was sent and no running module was replaced. Hardware release
qualification and whole-core shutdown/reclamation remain open.

The `0.1.13-1` DSP candidate passes all 136 platform tests and five warning-free
`W=1` builds each for DSP and AFE. The generated AFE patch matches its canonical
sources and both kernel-recipe hashes. All eight local package recipes' source
hashes match; the DSP archive's bytes, inventory, ownership and modes were
independently checked. Its report is
`build/dsp-asoc-final-20261106/report.json`. The new task transitions,
AFE owner and DL0 frontend have offline fault/concurrency coverage, not hardware
qualification. The running DSP remains unchanged on 0.1.3.

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
the legacy service disabled. No replacement or second handshake was attempted
on the failed November 5 boot. Package-file checks and all EFI/menu hashes
passed after installation. The changed kernel code passes `checkpatch.pl`
with no errors or warnings and builds with `W=1` without warnings.

The November 6 boot `db4397f4-646b-4b7d-9831-567db6218147` loaded this source
version automatically and `n1x-platform-npu.service` completed successfully.
Eight numerical cuDLA outputs matched independent references, recorded in
`n1x-lab/results/platform-enablement-20261106/npu-post-boot-numerical/`.
The post-boot audit also verified the module versions, PCBI queries, ALS
measurements, firmware hashes and unchanged boot configuration. This closes
the fresh-boot startup qualification gap; it does not provide the missing DSP
shutdown or shared-buffer release contract.

For a subsequent fresh-boot audit, use a new report path and the preceding
boot ID:

```sh
systemctl status n1x-platform-npu.service
sudo python3 scripts/verify-platform-install.py \
  --after-boot-id db4397f4-646b-4b7d-9831-567db6218147 \
  --report /path/to/new-post-boot-report.json
```

That verifier does not start the service itself. If startup fails, inspect the
retained status and kernel messages before any further hardware requests.

## DSP completion criteria

The latest uninstalled `0.1.11-1` candidate adds a separate IRQ preparation
step after all four DSP PREPARE acknowledgements. The AFE owner validates the
bound IRQ, exact register layout, rate code, active memif clock domain and
24-bit period count, and refuses an enabled IRQ. Checked counter/rate/domain
updates preserve unrelated bits and leave IRQ target and enable unchanged.
Failed writes are terminal. Endpoint ABI 2 reports readiness only after all
three succeed; old ABIs and missing helpers are rejected before processing
publication. All 134 tests, package/archive checks, checkpatch and five
warning-free builds each of DSP and AFE pass. Receipts are in
`build/dsp-irq-prepare-qualified-20261106/report.json`. The package is uninstalled.

The preceding uninstalled `0.1.10-1` candidate programs the published hardware ring
through the AFE owner before DSP PREPARE. Binding and publication alone left
the memif's previous DRAM address in place. The new helper validates the bound
substream, DAI, format and IRQ, then programs the lease's AFE address and length
with checked writes. It does not enable the memif or IRQ. Successful
repetition performs no register writes; partial programming failures are cached
and retain all published resources. Configuration rejects a missing helper
before publishing processing memory. The preceding DSP source fails the
prepare-order regression check. All 134 platform tests, package/archive checks,
checkpatch and five warning-free builds of both DSP and AFE pass, recorded in
`build/dsp-afe-prepare-qualified-20261106/report.json`.

The native transition trace also corrects an earlier extraction label:
`0x14002a8a0` is the resume method, while normal START is `0x140029310`.
The normal first-user path uses processing/host command `0x0f`, with host
optional channel-merge/hardware-gain operations between them. The previous
trace misidentified the gain object at `+0xa8` as IRQ. The corrected register
tables identify the separate speaker IRQ controller at `+0x2a8`; its preparation
follows firmware PREPARE. The stock Linux AFE trigger also controls the memif;
firmware ownership of that operation remains to be resolved.
START/STOP and the Linux frontend are not implemented or hardware-qualified.

The preceding uninstalled `0.1.9-1` candidate adds checked host producer publication
through `n1x_adsp_primary_commit()`. It publishes the prepared ring's byte
cursor and generation through the registered shared descriptor, without an
additional mailbox command. Sample writes precede descriptor publication;
the caller owns availability and serializes sample writes. The native end
boundary is preserved on the wire. A late power error can follow publication
and is terminal. This path has offline fault/concurrency tests but no hardware
qualification. All 134 platform tests, source-package/archive checks,
checkpatch and five warning-free DSP builds pass. The AFE source/header remain
unchanged and match the preceding qualified build inputs. Current receipts are
in `build/dsp-producer-20261106/report.json`. The ALSA frontend, START/STOP,
complete-stream qualification and verified shutdown/release remain open.

The preceding uninstalled `0.1.8-1` candidate adds bounded task-7 consumed-data
notifications and kernel callback/atomic-position APIs. It checks the inline
extent, ring/frame bounds and generation counter, preserves the last valid
snapshot on errors and synchronizes IRQ delivery on listener detach and close.
Tests exercise the actual IRQ and hold a callback in flight while detach or
actual close waits. All 133 platform tests, source-package/archive checks,
checkpatch and five warning-free DSP builds pass. The AFE source/header remain
unchanged and match the preceding qualified build inputs. Current receipts are
in `build/dsp-period-20261106/report.json`. Those interfaces did not provide the
ALSA frontend, producer publication or START/STOP; firmware notification
and complete-stream qualification and verified shutdown/release remain open.

The preceding uninstalled `0.1.7-1` candidate includes the P16 route-2 per-memif
processing task, 16, alongside primary task 7. Task 16 registers two AFE SRAM
message slices with commands 5 and 6, then opens. Configuration sends task-7
host HWPARAM, task-16 hardware HWPARAM and task-16 bridge HWPARAM. PREPARE
runs hardware/bridge commands on task 16 before host commands on task 7. Close
includes task-16 AFE-HWFREE with playback direction. Published IPC leases retain
independent references if later PCM publication fails or an unpublished PCM
peer is released. The earlier 0.1.5/0.1.6 candidates omitted this processing
task and must not be used to claim native pipeline qualification.

The current sanitizer tests, 132-test suite, source package, patch replay,
checkpatch and five warning-free DSP/AFE module builds pass. Source/package
receipts are in `build/dsp-processing-20261106/report.json`; the pinned native
instruction and routing evidence are in `dsp-primary-processing-analysis/`
under the November 6 results. These are offline checks. The live DSP remains
0.1.3, with the same healthy state and no new firmware request. The frontend,
START/STOP, period notifications, complete graph hardware qualification and
verified shutdown/release remain unfinished. The alternate native route-3
task graph is not implemented by the task-7 API. See `platform/dsp/protocol.md`.

The current `n1x_adsp` code explicitly reports `services=0`. Secure boot is its
only automatic host firmware request. Its receive handler recognizes the
`0xf002` boot-ready notification and queues `0xf005` power requests for a worker.
Version 0.1.3 provides a serialized kernel request/reply API with separate ACK and
notification handling and primary-task message-buffer registration. Its fault
tests cover publication order, token matching, terminal errors and concurrent
callers. A validation client registered all four buffers on the core selected
by the speaker/headphone firmware properties, then another client completed
OPEN, HWFREE and CLOSE without configuring or starting a stream. The latter
trial also handled one firmware power notification through SSPM. Reports are
`dsp-primary-registration-live.json` and `dsp-primary-task-live.json` under
`n1x-lab/results/platform-enablement-20261106/`. This is insufficient for
normal DSP operation: task CLOSE does not establish whole-core shutdown or
permit release of published DMA allocations.

The 0.1.4 source candidate provides exclusive primary-task handles, cached
lifecycle failures, validated service echoes and typed OPEN/HWFREE/CLOSE.
Raw task-7 transfers are rejected. It passes the 123-test platform suite and
warning-free driver/validation builds on all five installed kernels. It is
not installed or qualified on hardware; the live driver remains 0.1.3.

A subsequent read-only validation confirmed the native AFE SRAM properties:
size `0x14000`, CPU and audio-hardware views `0x1a6c0000`, DSP view
`0x4e5c0000`. The validation module built without warnings for the running
kernel, loaded and unloaded successfully, and left all three DSP status
records and boot artifacts unchanged. It mapped no SRAM and sent no firmware
request. The report is `dsp-sram-route-live.json` in the November 6 results.

The OEM allocator uses a distinct AFE SRAM pool. Its translation method
selects the audio-hardware or DSP view by adding the allocation's CPU offset
to the corresponding base. PCM HWPARAM copies a 96-byte descriptor into a
registered message buffer and sends that buffer's address as an 8-byte inline
payload. These results establish address roles and descriptor extent, not
permission to allocate the SRAM or start a stream. At that stage the native
format/tail fields and allocation selectors were unresolved. Pinned disassemblies and the exact
evidence are in `dsp-pcm-memory-analysis/report.json` in the same results tree.

The subsequent descriptor analysis traces container width from the native
ACX getters through the constructors into the 96-byte descriptor. It uses
width in bits, including 32 for 24 valid bits in a 32-bit container. The tail
contains the hardware flag, memif and IRQ IDs, memory-type wire value 1,
counter and ignore-IRQ byte. The native default AFE allocation selector is
also traced through the mode table and constructors. This is static analysis,
not a live Windows endpoint observation. The reproducible extracts are in
`dsp-pcm-descriptor-analysis/report.json` in the same results directory;
`scripts/analyze-dsp-pcm.py` rejects any binary differing from the pinned input.

Uninstalled candidate `0.1.4-2` includes `n1x_adsp_pcm.h` and fixed host/AFE
descriptor tests, with destination bounds, period arithmetic and overflow
checks under ASan/UBSan. All 129 platform tests pass; the header compiles in
kernel validation clients without warnings for all five installed kernels.
The package passes its source/tests and archive-content checks. Evidence is
`build/dsp-pcm-descriptor-20261106/report.json`. No validation client was
loaded and no firmware request was sent. The live DSP stays on 0.1.3 with
unchanged status. Exclusive AFE SRAM ownership, runtime-PM/DAI integration,
notifications and actual stream execution remain open, as does core shutdown.

The next kernel candidate now includes an AFE-owned SRAM allocator and opaque
leases in `kernel/p16-afe-sram.patch`, generated from `platform/dsp/afe/`.
It checks the firmware layout, claims and maps SRAM uncached, and zeros only
reserved allocations after obtaining power. Device, module and runtime-PM
references remain held for each lease. Published allocations refuse release;
outstanding leases also prevent forced AFE suspend and module unload. Manual
driver detach is disabled. The normal DRAM audio path remains available if
SRAM setup fails.

All 131 platform tests pass, including actual owner/suspend functions with
fault injection and eight concurrent leases under ASan/UBSan. The complete
AFE module builds with `W=1` without warnings for all five installed kernels;
its source version is `DA362818CB224F7E44456B6`. Exact patch replay and the
preceding recipe's AFE source reconstruction also pass. Receipts and the
superseded mapping draft are in `build/afe-sram-20261106/`. This candidate is
not installed or booted, has no connected PCM service client and makes no
new claim about stream execution or published-buffer release. The running
AFE and DSP instances remain unchanged. Client integration, notifications,
hardware qualification and verified quiesce/reclamation remain required.

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

## November 6 upstream preparation and PCBI queries

`upstream/spi-hid/` separates the local IRQ, reset, report-transaction and
stream-only sensor fixes into four drafts. The pinned prerequisite stack
reconstructs the packaged source files exactly. The GET-report callback passes
a sanitizer test covering types, bounds, malformed replies, timeout cleanup
and concurrent callers. All four patches pass checkpatch without warnings
when checking code separately from the still-required author sign-off.

The HID maintainer's `for-next` tree at
`720806515cb6921ab05caaa6f470f7054b56385e` does not contain the prerequisite
SPI HID driver. A rebase onto the maintainer's selected SPI HID series is still
required. The drafts have not been submitted or accepted.

The Pluton client now implements PCBI query-service commands 7 (protocol
versions) and 5 (services), with bounded buffers and complete reply validation.
Both completed on the live P16 and returned protocol 1 with services 1/v1 and
2/v1. The protocol reference, validation rules and qualification boundary are
in `platform/pluton/protocol.md`. No key, provisioning or firmware-control
request was sent. Other service operations remain unfinished.

The client wire tests and transport tests passed together with the existing
Python suite. The updated SCP and Pluton sources compile with `W=1` without
warnings for all five installed kernels, recorded in
`build/upstream-platform-20261106/report.json`.

Pluton `0.2.0-1` adds `N1X_PLUTON_QUERY`: the kernel constructs one of the
three supported requests, receives a complete bounded reply and validates
its command-specific body. Generic raw exchange now requires an explicit
load-time opt-in as well as `CAP_SYS_RAWIO`. The CLI uses the typed ioctl,
falling back only on `ENOTTY` for older drivers. The UAPI and errors are
documented in `platform/pluton/protocol.md`.

All 128 platform tests pass, including actual ioctl functions under ASan/UBSan,
fault injection and eight concurrent callers. Five isolated kernel builds
are warning-free. Package source checks and archive contents pass inspection;
the build report records the separate fakeroot tooling diagnostic. Receipts
are in `build/pluton-typed-query-20261106/`.

The installed and loaded source version is `7D7E316FC81DE794660075B`. The live
test forced typed-ioctl use by disabling the client's legacy fallback, then
verified protocol 1, services 1/v1 and 2/v1, platform 6 and package `PltnFull`.
It also verified default raw-exchange denial and a healthy transport after
all three queries. No firmware update or provisioning request was sent.
The subsequent platform audit passed with matching module versions, working
IIO measurements, healthy DSP/NPU state, no new kernel messages and unchanged
boot files. The dated receipts are `pluton-typed-query-install.json` and
`platform-post-pluton-typed-query.json` under
`n1x-lab/results/platform-enablement-20261106/`. Build-time provenance remains
a snapshot; these receipts record the subsequent hardware qualification.

## SCP sensor path and IIO units

The SCP sensor descriptor identifies a single VEML3328 ALS. Measurements are
already delivered through `MSFW1111` SPI HID to `hid_sensor_als`; adding a
second IIO device through the diagnostic SCP mailbox would duplicate it.
The November 6 test read illumination, intensity, color temperature and both
chromaticity channels through the standard IIO interface.

That test found an error in the older HID ALS driver: it returned the
illumination scale for every channel. The live descriptor specifies exponents
-1 for illumination, 0 for color temperature, and -4 for chromaticity. The
upstream fix is Linux commit `cad928dad9a4c26aa56b4223dc2a29bca517fb5f`.
`platform/als/` packages that upstream source, changing only the private-header
include path for a standalone build.

The backport compiles without warnings for all five kernels, passes sanitizer
tests for actual descriptor parsing and scale reads, and passes a live trial.
The older source fails the regression test. Live scales are now 0.1, 1 and
0.0001 respectively, with working raw samples on all five channels. The report
is `n1x-lab/results/platform-enablement-20261106/als-upstream-trial.json`.
Optical calibration remains separate from descriptor-unit correctness.
