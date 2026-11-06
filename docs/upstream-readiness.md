# Platform driver upstream review

Review date: November 6, 2026.

The current sources are suitable for continued local development. The complete
driver set is not ready for Linux upstream submission. The table identifies
the independently reviewable changes and the remaining blockers.

## Current candidate: DSP 0.1.13

The uninstalled `0.1.13-1` candidate adds an optional DL0 playback client to
the existing AFE/SoundWire frontend. It checks primary-client ABI 1, copies
newly acknowledged application frames to DSP-owned coherent memory, reports
the host consumed position and defers complete-period/error delivery out of
hard IRQ. Close detaches listeners and drains work before retiring the
borrowed ALSA substream pointer, including after terminal task failure. The
allocation and all published-resource references remain held. The AFE
`dsp_playback` parameter defaults to false.

All 136 platform tests, five warning-free DSP builds and five matching AFE
builds pass. Actual-function sanitizer tests cover subperiod/coalesced
notifications, producer/full-ring/boundary vectors, helper/ABI faults and a
blocked worker racing with close. The eleven-file AFE patch reproduces the
canonical integration; both kernel-recipe hashes and all eight platform
recipes' local source hashes match. Source and patch checkpatch pass with
SPDX dependencies available and no tool errors. Package source bytes,
inventory, ownership and modes were independently verified. The current
receipt is `build/dsp-asoc-final-20261106/report.json`.

This does not qualify a complete stream on hardware. Notification cadence,
firmware memif/IRQ operation and codec output still need testing. Restart,
additional endpoints, whole-core shutdown/DMA release and suspend/resume
remain incomplete. The live DSP remains unchanged on 0.1.3. The earlier
readiness and packaging failures below are retained as dated audit history.

## Prior candidate: DSP 0.1.12

The uninstalled DSP `0.1.12-1` candidate now passes all 135 platform tests,
five warning-free `W=1` DSP builds and five matching AFE builds. Whole-file
and generated-patch checkpatch pass with `--no-signoff`. The regenerated AFE
patch reproduces its canonical sources exactly; both kernel-recipe hashes
match. All eight platform recipes' local source checksums match, including
all 22 DSP inputs. The package archive's source bytes, inventory, root
ownership and file modes were independently verified. Receipts are in
`build/dsp-trigger-qualified-20261106/report.json`.

Typed START sends task 16 then task 7 in normal route-2 mode 0. STOP reverses
the task order, then disables/clears the owned HiFi1 IRQ and removes its MCU
and HiFi1 targets through the AFE owner. Close stops a started stream before
HWFREE/CLOSE. Actual-function sanitizer tests cover concurrent callers,
transaction and owner faults, every IRQ-stop I/O failure and unrelated-bit
preservation. These are offline results. ALSA integration and complete-stream
hardware qualification, restart after STOP, whole-core shutdown/DMA release
and suspend/resume remain incomplete. The live DSP is unchanged on 0.1.3.

DSP's SRAM comment now states the hardware constraint without the OEM
implementation reference; supporting analysis remains in the documentation.
SPBM's discovery-history comments and NPU's method-2 comment remain. The
broader subsystem, API, security, hardware and submission blockers below also
remain. This candidate does not establish blanket upstream readiness.

## Audit before the 0.1.12 candidate repair

The pre-repair working-tree check did not support blanket submission approval.
Evidence is in `build/upstream-readiness-final-20261106/`, including source
hashes, package checksum comparisons, whole-file and patch checkpatch logs,
the platform test log and the NPU check log. This audit changed no driver
source, loaded module, boot file or firmware state.

- The platform suite passed 134 of 135 tests. The AFE patch-reproduction test
  failed: `kernel/p16-afe-sram.patch` no longer reproduces the canonical owner
  and header in `platform/dsp/afe/`.
- DSP source version 0.1.12 implements kernel-client START/STOP operations,
  but its package recipe has 22 sources and 21 checksums. Seven existing
  checksums are stale and `test_trigger.py` has no checksum. The other seven
  platform recipes' local source checksums match.
- Current DSP and AFE source hashes differ from the qualified 0.1.11 build
  receipt. Those earlier five-kernel builds do not qualify this working tree;
  no replacement build matrix was run during this audit. The new transitions
  also lack hardware qualification. ALSA endpoint integration, restart after
  STOP, whole-core shutdown/DMA release and suspend/resume remain incomplete.
- NPU `make check` passed. Current SCP, Pluton, DSP, AFE, DSP validation,
  SPI HID and NPU kernel sources pass checkpatch with `--no-signoff`.
  All four target-tree HID patches pass the same code review check. Generated
  module metadata and userspace compiler headers are excluded. The imported
  ALS warning and SPBM's barrier/macro diagnostics remain.
- Discovery-history comments remain in `build/spark_hwmon/spbm.c` and
  `n1x-mmio.h`. DSP's SRAM comment refers to the OEM implementation; NPU's
  boot comment refers to the original method-2 path. Protocol constraints,
  limitations, attribution and provenance should be preserved when editing
  these comments. Wording alone cannot establish whether code was generated.

SPI HID remains a submission draft requiring prerequisite selection, `Fixes:`
ID remapping, target-kernel hardware qualification and human DCO sign-off.
SCP and Pluton still require subsystem/interface review; NPU requires in-tree
integration and UAPI/security review; SPBM needs interface and board-mapping
review. HID ALS is already upstream and should remain a backport. The older
receipts below describe their recorded snapshots, not the current DSP/AFE
sources.

## Prior submission assessment (DSP 0.1.11)

The latest DSP review passed all 134 platform tests, including producer
publication, consumed-data and callback-lifetime tests. Its current source hashes, five
kernel builds, source-package/archive checks and checkpatch results are in
`build/dsp-irq-prepare-qualified-20261106/report.json`. The log is
`build/dsp-irq-prepare-qualified-20261106/platform-tests.log`. The preceding broader review
passed the NPU repository's `make check`, recorded in
`build/upstream-confirm-npu-20261106.log`; NPU sources have not changed since.
These are offline checks; no new DSP module or firmware request was used.

The kernel sources for SCP, Pluton, DSP, AFE SRAM and NPU pass whole-file
checkpatch with `--no-signoff`. The four SPI HID patches also pass against
their prepared HID target tree. The imported ALS debug-string warning and
SPBM's barrier/macro diagnostics remain. Kernel checks exclude generated
`.mod.c` files, userspace-only compiler/ONNX headers and standalone test
programs; the broader initial scan is preserved in
`style-report-all-c-headers.json`. The final check uses the prepared HID tree,
including its SPDX validator with dependencies in an isolated build directory.

DSP source version 0.1.11 includes the P16 route-2 task-7/task-16 configuration
chain, a bounded consumed-position listener and host producer publication.
The AFE owner now programs the published SRAM ring before
DSP PREPARE, with current ownership checks, idempotence and terminal programming
errors. After all four DSP PREPARE acknowledgements, a separate owner operation
prepares the disabled IRQ's counter, rate and active clock domain. Endpoint
ABI 2 exposes IRQ readiness; old ABIs and missing helpers are rejected before
processing publication. The address prepare-order regression remains covered.
It separates class-0
notifications from transaction ACKs, validates ring offsets and generations,
and synchronizes IRQ callbacks before detach/close can release client data.
The preceding 0.1.5/0.1.6 candidates omitted processing task 16. Their green
offline tests did not qualify a complete native graph; neither was installed.

The current package is uninstalled and lacks hardware qualification. The ALSA
frontend/period delivery, START/STOP and verified
whole-core shutdown and DMA release remain incomplete. Five current DSP builds
pass without `W=1` warnings. The matching AFE sources/header and all five
warning-free AFE builds are in the same current report. All 134 platform tests,
source-package checks and current
DSP checkpatch pass. Archive source bytes, inventory, ownership and modes were
checked independently. The live DSP remains healthy on 0.1.3; no consumed-data
notification or complete stream has been qualified on hardware.

The final confirmation found nine stale DSP source hashes and a missing hash
for the new producer test. The recipe now has 21 matching source/checksum
entries, and package `0.1.9-1` builds successfully. Its archive was independently
checked for exact source bytes, inventory, ownership and modes. All eight
platform package recipes' local source hashes match. The before/after checks
are in `build/upstream-confirm-latest-20261106/`. These packaging corrections
do not resolve the subsystem, API, security or hardware blockers below.

The subsequent `0.1.10-1` package passes the same source/archive checks and is
also uninstalled. Its native transition analysis corrects the earlier
`stream_start` label: that extract was the separate resume method. Normal
START/STOP are recorded separately in `platform/dsp/protocol.md`. Its first
owner trace misidentified the hardware-gain object as IRQ. The corrected
`0.1.11` analysis checks separate gain/IRQ tables and the speaker IRQ vtable;
IRQ preparation follows firmware PREPARE. The new package also passes source
and archive checks and remains uninstalled. Firmware memif/gain ownership and
the Linux trigger integration remain unresolved. No new transition was sent
to hardware.

The comments are not uniformly ready for submission. Concrete examples are
the discovery-history and dated upstream-availability claims in `spbm.c`, the
validation-history comment in `n1x-mmio.h`, DSP's OEM SRAM-initialization
reference and NPU's original-method-2 reference. Keep protocol requirements
and hardware limitations in code; move investigation history into provenance
documentation when preparing each series. Required attribution, licensing,
accurate qualification limits and tool-assistance disclosures must remain.
Concise wording cannot establish authorship or correctness. This review cannot
certify that every module is free of generated prose or ready for submission.

## Earlier qualification

The preceding suite passed all 131 platform tests. Pluton's protocol vectors and
actual ioctl fault/concurrency tests also pass with `-Wall -Wextra -Werror`
and ASan/UBSan. Its current kernel sources pass checkpatch with `--no-signoff`.
Version 0.2.0 builds with `W=1` without compiler warnings for all five installed
kernels, recorded in `build/pluton-typed-query-20261106/report.json`.

The preceding audit reproduced four stale Pluton checksums, an omitted required
header and inconsistent module/package versions. That failed verification is
retained in `build/upstream-audit-20261106/pluton-source-verification.log`.
Package `0.2.0-1` fixes all three issues and passes source/package checks.
Fakeroot emitted a tooling diagnostic; the final archive's ownership, modes,
file inventory and source bytes were checked independently.

The installed module's source version is `7D7E316FC81DE794660075B`. Typed
queries 4, 5 and 7 passed on hardware, with legacy fallback explicitly disabled
in the test. Raw exchange was denied by default. The installation and live
results are recorded in
`n1x-lab/results/platform-enablement-20261106/pluton-typed-query-install.json`.
The subsequent full audit passed in `platform-post-pluton-typed-query.json`
in the same directory: installed/loaded versions match, sensors respond, DSP
and NPU status remain healthy, and no new kernel errors or boot-file changes
were recorded. This qualifies the query interface; control-service support
and upstream subsystem/API review remain open.

The discovery-history comments in SPBM and the OEM/original-implementation
references in DSP/NPU listed below remain present. This working tree cannot
be described as entirely free of discovery remarks or ready for submission.
Only the idle Pluton module was refreshed during qualification. The published
DSP and NPU instances were preserved.

| Component | Source review | Submission status |
| --- | --- | --- |
| HID ALS | Upstream commit `cad928dad9a4c26aa56b4223dc2a29bca517fb5f`, with only the standalone trigger-header include adaptation; per-channel units tested | Already upstream. Carry as a backport for older kernels; do not submit as a new driver or original fix. |
| SPI HID | Four focused patches reproduce the packaged transport; reply race reproduced on the baseline; per-commit ARM64 module builds and an OF built-in link pass on HID for-next plus pinned prerequisites | Local integration draft. Maintainer prerequisite selection, target-kernel hardware qualification and human DCO review remain open. |
| SCP | Firmware log and bounded queries, with firmware ABI matching; measurements use the existing HID ALS IIO client | Local diagnostic driver. Review the debugfs snapshot/query interface, BIOS-specific matching and subsystem placement before preparing a series. |
| Pluton | Serialized ROT1/PFI transport and typed PCBI capability/running-firmware queries with kernel reply validation; raw exchange requires explicit opt-in | Query interface qualified locally. Target subsystem/API review remains; query-service integration does not implement the control service. |
| NPU/SROOT | DRM accelerator frontend, protocol guards and checked shutdown; portable tests and previous numerical workloads pass | Requires an in-tree series with Kconfig, Makefile and maintainer integration, reviewed firmware/DMA dependencies and UAPI. The current trusted-loadable execution policy needs explicit security review. |
| DSP | Three firmware boots, four primary-task buffer registrations, OPEN/HWFREE/CLOSE acknowledgements and one completed SSPM power request verified on hardware; uninstalled 0.1.13 adds the task graph, typed START/STOP, AFE ownership and an optional DL0 ALSA frontend with producer/pointer/queued-period integration | Incomplete. Complete-stream hardware qualification, restart, additional endpoints, verified shutdown/DMA release and suspend/resume remain missing. Published DSP state retains DMA, power and module references until reboot. |
| SPBM hwmon | Separately pinned `spark_hwmon` source at `ef94faace8a44380e8ba8c8bd8cffd185c82af67`; local telemetry and reduction-only controls previously tested | Not covered by the six-component build matrix. Source still contains discovery-history comments; its raw sysfs interfaces, board-specific mappings and remaining checkpatch diagnostics need review before submission. |

The SPI HID base inspected for the existing drafts is HID `for-next` commit
`720806515cb6921ab05caaa6f470f7054b56385e`, which lacks
`drivers/hid/spi-hid/`. Tests against the packaged prerequisite stack do not
replace qualification against the eventual submission base.

The NPU address-list and tensor-range checks do not validate the embedded
engine instruction stream. Its default access policy uses device permissions;
`require_rawio=1` is optional. The existing trusted-loadable policy is documented
in the sibling NPU repository's `SECURITY.md`. Review that policy before treating
the interface as suitable for untrusted workloads. Shared DSP upload also
prevents NPU shutdown while a published DSP buffer exists.

## Source cleanup

The six platform drivers' comments describe register semantics, ownership,
ordering and failure handling. Vendor analysis addresses and binary hashes
belong in protocol/provenance documentation. SCP's stale "sensor IIO client
pending" module description was removed. DSP's experimental module parameter
and description remain accurate qualification labels while services and
shutdown are incomplete.

Existing copyright and SPDX notices are preserved. The license summary now
covers the platform drivers, Pluton UAPI and upstream ALS/SPI HID sources.
Source-package checksums and the generated NPU provider snapshot were refreshed.
This review changes repository sources; it does not replace loaded modules.

The unchanged SPI HID prerequisite code contains two existing FIXME comments
about fragmented reports and report-descriptor validation. They are real
limitations of the imported driver and are outside the four focused fixes.
The unchanged upstream ALS source has one checkpatch warning for a function
name embedded in a debug string. Neither is presented as a newly introduced
issue or silently changed in the backport.

The broader driver set is not free of discovery remarks. In the pinned
`build/spark_hwmon/` checkout, `spbm.c:26` records how the interface was
discovered and makes a dated upstream-availability claim; `n1x-mmio.h:5`
describes analysis and validation history. These belong in provenance or
validation documentation. DSP's SRAM-initialization comment and NPU's
method-2 comment also retain references to the OEM/original implementation.
No authorship can be inferred from wording alone; this review does not
certify that code is free of defects merely because comments are concise.

## Verification

Review artifacts are under ignored `build/upstream-review-20261106/`:

All 30 module builds passed with no compiler warnings. First-party kernel
sources and the four SPI HID patches passed checkpatch without errors or
warnings, using `--no-signoff` for code review. The 116 platform tests, DSP
fault tests and NPU `make check` passed. Source verification also passed for
the refreshed SCP, Pluton, DSP and NPU-provider package recipes.

- `build-report.json`: isolated `W=1` module builds for SCP, Pluton, DSP, ALS,
  SPI HID and NPU on all five installed kernel headers. Build logs record each
  result; no module is loaded by this check.
- `style-report.json`: whole-file checkpatch for first-party kernel sources
  and code checks for the four SPI HID patches. NPU compiler/ONNX userspace
  headers are outside the kernel source check. The ALS warning above is
  retained rather than changing the upstream backport.

`build/upstream-review-tests.log` records the 116-test platform suite.
`build/upstream-review-dsp-tests.log` records the DSP driver fault tests.
`build/upstream-review-npu-tests.log` records `make check` in the NPU repository.
These are offline checks; earlier live results remain documented in
[platform driver status](platform-driver-status.md).

Earlier DSP 0.1.2 transport checks are recorded separately in
`build/dsp-transport-20261106/report.json`: five warning-free builds, the
exported kernel client API, fault tests and the built source package. The
candidate was superseded by installed DSP 0.1.3-1. Its five warning-free
builds and source hashes are recorded in `build/dsp-primary-20261106/report.json`.

The current review passed all 122 platform tests and NPU `make check`.
Logs are `build/upstream-review-current-tests.log` and
`build/upstream-review-current-npu-tests.log`. The current whole-file and
four-patch checks are recorded in
`build/upstream-review-current/style-report.json`; generated `.mod.c` files
are excluded. The first-party kernel sources and validation clients pass
without diagnostics. Imported SPI HID sources pass; the ALS backport retains
the upstream debug-string warning described above. SPBM reports one missing
barrier comment and two macro diagnostics. The file-scope declaration macro
requires manual judgment rather than applying an invalid expression wrapper.

Hardware reports under
`n1x-lab/results/platform-enablement-20261106/` record primary-buffer
registration (`dsp-primary-registration-live.json`), the task lifecycle and
one completed power request (`dsp-primary-task-live.json`). Both passed with
unchanged boot artifacts; neither started a PCM stream or verified DMA
release. After registration and before the task lifecycle trial, eight NPU
outputs matched their references, including five buffer reuses, in
`npu-post-dsp-primary-reuse-qualified/complete.json`.

The expanded suite passed 120 tests after adding Pluton command 4. Installed
Pluton package `0.1.1-4` provides running-firmware metadata queries. The live
P16 returned platform ID 6 and package `PltnFull`. The read-only audit in
`n1x-lab/results/platform-enablement-20261106/platform-post-pluton-package-info-recheck.json`
confirmed all six loaded/installed module versions, PCBI queries, IIO samples,
firmware hashes and unchanged boot artifacts with no new kernel messages.
The earlier audit failure is retained: DKMS status exceeded 30 seconds during
memory pressure and a separate Kubernetes memory-cgroup OOM. The DKMS audit
deadline is now 120 seconds; the cluster's limits and workloads were not changed.

The subsequent DSP 0.1.4 candidate adds exclusive primary-task handles and
reserves task 7 for the typed OPEN/HWFREE/CLOSE interface. All 123 platform
tests pass, including eight concurrent openers and every lifecycle request
and reply-echo failure. The driver and validation modules compile with `W=1`
without warnings for all five installed kernel headers. Build receipts are in
`build/dsp-lifecycle-20261106/report.json`; source-package checks passed in
`build/dsp-lifecycle-package-qualified-20261106.log`. This candidate is not
installed or hardware-qualified. The running DSP remains on version 0.1.3;
PCM endpoints and whole-core shutdown/release are still missing.

Submission must follow the target subsystem's workflow and the kernel's
[patch submission guide](https://docs.kernel.org/process/submitting-patches.html).
The current drafts have no author-approved DCO sign-off and have not been sent.

The subsequent DSP `0.1.4-2` package candidate includes a checked 96-byte PCM
descriptor encoder, with native container-width and tail-field traces. All
129 platform tests pass and the encoder compiles without warnings in kernel
validation clients for all five installed kernels. The package passes source,
test and archive-content checks. Receipts are in
`build/dsp-pcm-descriptor-20261106/report.json`; the package is not installed.
The encoder does not allocate or publish memory or provide a streaming
endpoint. SRAM ownership, notification handling, stream/lifecycle hardware
qualification and verified shutdown/DMA release remain required.

The uninstalled AFE SRAM integration patch subsequently adds the allocator
and runtime-PM lifetime guard under the existing audio owner. All 131 tests
pass, and the complete AFE module builds without `W=1` compiler warnings for
all five installed kernels. The local patch passes checkpatch with
`--no-signoff`, reconstructs the canonical sources exactly and is included
with checked hashes in the kernel recipe. Its preceding AFE patch-stack inputs
also reproduce the pinned base. Hardware qualification and PCM client
integration remain open; published SRAM still cannot be reclaimed without
the missing verified DSP shutdown contract. Its stable-owner strategy and
kernel API require subsystem review. Details are in `platform/dsp/afe/README.md`.
