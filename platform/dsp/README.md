# Experimental MT8901 DSP boot

The uninstalled `0.1.13-1` candidate implements the P16 route-2 playback
configuration chain: host task 7 and processing task 16. Processing IPC uses
two AFE-owned 1 KiB SRAM slices, registered with commands 5 and 6 before OPEN.
HWPARAM configures the task-7 host ring, task-16 hardware ring and task-16
bridge. PREPARE sends hardware/bridge descriptors to task 16, then the host
descriptor twice to task 7. The preceding 0.1.5/0.1.6 candidates omitted the
processing task and must not be used to qualify a stream.

The AFE owner verifies the active ALSA playback substream, format, memif and IRQ.
Separate IPC leases retain device/module/runtime-PM references independently of
the PCM lease. Published IPC memory remains retained even if later PCM
publication fails. The client validates descriptors before registration and
stops requests after any firmware-stage failure. Normal close stops a started
stream, then follows primary HWFREE/CLOSE with processing
HWFREE/AFE-HWFREE/CLOSE; acknowledgements do not
establish whole-core shutdown or permit memory release.

This requires the matching `p16-afe-sram.patch` kernel candidate. Missing AFE
helpers are rejected before processing registration. The native route-3 path
selects other logical tasks and is rejected rather than sending task-7 packets
on that route. The current P16 firmware selects route 2. The first ALSA
frontend implementation covers DL0 playback. Other endpoints, stream hardware
qualification and verified whole-core shutdown/reclamation remain unfinished.
The running driver is unchanged.

Version 0.1.8 decodes class-0 playback-consumed notifications into a checked
host-ring offset and write-generation counter. A kernel listener receives IRQ
snapshots, and an atomic pointer API exposes the last valid position. The
counter is not an elapsed-period count. Invalid positions report a cached
stream error; stale generations are ignored. Detach and close synchronize the
IRQ before listener data may be released. Listeners must drain their own queued
work separately. These interfaces have offline bounds, IRQ and teardown-race
tests; no new module or streaming workload has run on hardware.

Version 0.1.9 adds `n1x_adsp_primary_commit()` for a prepared host ring. The
kernel client fills samples through `pcm_view`, then publishes its byte cursor.
The driver checks the cursor and cached read address, orders sample writes
before the registered descriptor, and advances the write generation. The
native method uses shared memory without an additional mailbox command. The
client owns buffer availability and serializes sample writes. A late power
error can follow publication and is terminal; published resources remain held.
The end boundary is preserved on the wire, including a read pointer received
at that boundary. These are source/offline results, not streaming qualification.

Version 0.1.10 programs the published hardware ring through the AFE owner
before the first DSP PREPARE. The owner rechecks the bound substream, DAI,
format and IRQ, then uses the lease's AFE address for the memif, checking each
register write. Repeated preparation does no further hardware writes. Partial
programming failures are terminal and retain the lease. Configuration requires
the new owner helper before publishing any processing buffers. This does not
enable the memif or IRQ and does not implement START/STOP. The running modules
remain unchanged; the new path is not hardware-qualified.

Version 0.1.11 separates SRAM address preparation from IRQ preparation.
After all four DSP PREPARE acknowledgements, the AFE owner checks that the
bound IRQ is disabled and programs its period counter, rate and active AFE
clock domain. Endpoint ABI 2 reports `irq_prepared` only after all three checked
updates succeed; older owner ABIs are rejected before processing publication.
Failures retain published memory and stop the sequence. No IRQ target or enable
bit is changed. The native stream's optional hardware-gain object is separate
from its IRQ controller; the preceding trace's IRQ label was incorrect.
The Linux frontend and complete-stream qualification remain open.

Version 0.1.12 adds typed START/STOP for the prepared route-2 graph. START
requires published samples and matching AFE endpoint ABI 2 IRQ readiness,
then starts task 16 before task 7 in normal playback mode 0. STOP stops task 7,
then task 16, then asks the AFE owner to disable and clear the owned HiFi1 IRQ
and remove its MCU/HiFi1 targets. The audio client owns DAPM/codec routing;
these calls do not program a gain, channel merge or CPU memif enable.

Repeated successful START/STOP sends no additional requests. Failed task
transactions or IRQ-stop writes cache a terminal error and retain published
memory. Successful STOP detaches and synchronizes the period listener; the
client drains its own queued work. Close attempts STOP before HWFREE/CLOSE
for a started stream. Restart after STOP requires reinitialization, which is
not implemented. Task acknowledgements and IRQ disable do not prove DMA
quiescence. Actual-function sanitizer tests cover transaction/owner faults,
each IRQ-stop I/O failure and concurrent callers; no stream has been started
on hardware with this candidate.

Version 0.1.13 connects the typed playback client to the existing AFE/SoundWire
DL0 frontend through `platform/dsp/afe/mt8901-afe-dsp.c`. The AFE's read-only
`dsp_playback` module parameter selects this experimental path at startup and
defaults to false. It requires a matching DSP client ABI before opening a task;
it neither loads firmware nor adds an unconnected sound card. Existing DPCM
and DAPM routes continue to own the SoundWire codecs.

Configuration runs in PREPARE, after ALSA has assigned the runtime format and
period fields. The frontend reserves a matching AFE ring, configures/prepares
the task graph, and registers a consumed-position listener. ALSA's managed
buffer remains the application staging buffer. The ACK callback copies only
newly committed frames into DSP-owned coherent memory, handles ring/boundary
wraps, preserves a full-ring write boundary and publishes the producer cursor.
The pointer callback reports the DSP host consumed position instead of the AFE
hardware-ring pointer. Validated IRQ positions accumulate byte progress;
subperiod and duplicate notifications do not signal an ALSA period. Workqueue
delivery coalesces complete periods and handles errors outside hard IRQ.

Close detaches the IRQ listener and drains queued work before consuming the
primary handle. It retires the retained AFE lease's borrowed substream pointer
even after a task failure, while preserving its allocation and memif/power
references. The frontend rejects reconfiguration after publication and does
not advertise pause, resume or no-period-wakeup support. Restart, additional
endpoints and complete-stream hardware qualification remain open. The path
is not installed or enabled on this machine.

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

## Kernel service transport

Version 0.1.2 adds `n1x_adsp_transfer()` for kernel service clients. It requires
a successfully booted DSP, assigns the transaction token, serializes requests,
publishes the SRAM slot before the doorbell, waits for the matching ACK, and
checks that firmware released the transmit slot. Notifications and transaction
ACKs have separate completion paths. No request is sent automatically.

Timeout, unmatched or duplicate ACK, slot-release failure and firmware power
failure prevent further requests. The driver retains the existing boot DMA,
power and module references. A successful exchange confirms transport completion;
the service client must still validate its reply and its buffer lifetime.

`n1x_adsp_client.h` is a kernel-only interface. No raw userspace command device
or audio/compute endpoint is added. This transport has passed offline tests but
has completed primary-buffer registration and OPEN/HWFREE/CLOSE on running
DSP firmware in version 0.1.3. PCM buffer-region mapping, streaming endpoints
and shutdown remain unfinished.

```sh
python3 platform/dsp/test_transport.py
python3 -m unittest discover -s tests -p test_dsp_ipc.py
```

The first test compiles the actual request, ACK and exported API functions
under ASan/UBSan. It checks SRAM publication order, terminal errors, reply
ownership and concurrent callers. The packet codec and boot/IRQ fault tests
remain separate checks. `protocol.md` records the transport and the matching
public MediaTek audio-service structures.

Version 0.1.3 adds `n1x_adsp_prepare_primary()` for the primary playback task's
four message-buffer registrations. It reads the speaker and headphone firmware
routes, requires them to agree and match the selected DSP, and registers four
1 KiB slices of the existing boot-owned DRAM window. Publication follows the
OEM command order 5, 6, `0x20a`, `0x20b`. A completed registration is reused;
a failed sequence retains its memory and cannot be replayed. Status reports
the attempted/completed registrations and their first error.

```sh
python3 platform/dsp/test_primary.py
```

This does not open or start a playback stream. The validation modules in
`validation/` check the running kernel's firmware-node properties and invoke
the kernel registration API explicitly. They are separate from the installed
driver and never run at startup. The November 6 registration and task lifecycle
trials passed, recorded in `dsp-primary-registration-live.json` and
`dsp-primary-task-live.json` under
`../n1x-lab/results/platform-enablement-20261106/`. Audio workloads, shutdown
and DMA release remain unfinished.

Version 0.1.4 adds an exclusive primary-task handle. `n1x_adsp_primary_open()`
registers the message buffers and sends OPEN; `n1x_adsp_primary_close()` sends
HWFREE then CLOSE and consumes the handle on every result. A second opener
receives `-EBUSY`. Any failed lifecycle request prevents reopening; a successful
close permits another open. Neither result releases DSP DMA or starts a stream.
Raw transfers to task 7 are rejected so they cannot bypass handle ownership.

```sh
python3 platform/dsp/test_lifecycle.py
python3 platform/dsp/test_task_client.py
```

The lifecycle test compiles the actual implementation under ASan/UBSan and
checks all three request failures, mismatched replies, reference ownership,
reopening and eight concurrent openers. The validation client now uses this
API. Version 0.1.4 remains an uninstalled candidate; its client has not run on
hardware. The earlier 0.1.3 task trial qualified the wire sequence, not the
new handle implementation. The currently running published driver cannot be
replaced until its retained resources are cleared by reboot.

`scripts/prepare-sroot-provider.py` snapshots the reviewed sibling NPU driver
and headers into a hash-pinned DKMS recipe under `platform/dsp/provider`.
That package provides standard DKMS rebuilds for installed/future kernels and
owns `n1x-platform-npu.service` plus a small Bash startup helper. It checks BIOS
300, selects isolated DMA domains before modprobe, and verifies driver readiness.
The native driver validates hardware/firmware; there is no kernel-version
allowlist. Enable the new unit and disable the old `n1x-npu.service` without
stopping a published experiment. Normal startup leaves DSP firmware boot disabled.
See `docs/update-safe.md` for the kernel packaging and pacman/mkinitcpio flow.

## Firmware power notifications

The 0.1.1 candidate also handles notification-class `0xf005` packets. The OEM
notification worker reads a little-endian 32-bit value at packet offset 24,
then submits a 16-byte power-control request with request ID zero and data
words `{1, value, 0}`. That layout maps to Linux's
`power_cntl_scmi_data` and `mtk_send_power_control_req()`; the exact OEM functions
and the distinction between source evidence and hardware validation are in
`provenance.json`.

The interrupt handler queues each value in a fixed 16-entry FIFO. A workqueue
submits SSPM requests in order, outside interrupt context. Queue overflow or
an SSPM failure stops further submissions for that instance. The driver never
retries an uncertain power operation. Removal synchronizes and detaches the
IRQ before cancelling work and releasing power state. Status includes queued
and completed request counts and the first power error.

Fault tests cover message classification, the exact Linux request payload,
FIFO order, overflow, terminal errors and removal order. One firmware power
notification completed through SSPM during the November 6 task trial. That
result does not implement audio/compute
services or establish a DSP shutdown protocol; firmware boot remains opt-in.
