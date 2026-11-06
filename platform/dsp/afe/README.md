# MT8901 AFE SRAM ownership

These canonical sources generate `kernel/p16-afe-sram.patch`, which is included
in the P16 kernel recipe. The patch adds the SRAM owner to the existing AFE
module, with a private gen_pool and a kernel-only opaque lease API. It is an
uninstalled integration candidate; the running AFE module is unchanged.

Initialization checks the P16 product, BIOS, hardware version and complete
SRAM property tuple before claiming the 80 KiB I/O resource. It maps the SRAM
without reading or clearing it. The native mapping at `nvasd` `0x140024560`
uses `MmMapIoSpace` with cache type 0; Microsoft's
[cache-type definition](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/wdm/ne-wdm-_memory_caching_type)
identifies that as uncached. The candidate uses `devm_ioremap_resource`, with
no write combining. The pinned instruction extract is recorded in
`n1x-lab/results/platform-enablement-20261106/dsp-afe-sram-mapping-analysis/`.

Each reservation is a multiple of 16 bytes, comes from the exclusive pool,
and is zeroed only after obtaining a runtime-PM reference. Its handle also
retains device and module references. The view operation returns CPU, AFE
and DSP address views for the same offset; the client must select the view
its wire protocol requires. Releasing an unpublished handle returns its
allocation and drops those references.

The client must mark a buffer published before submitting any address to
firmware. Release then returns `EBUSY` without changing the handle or its
resources. Before publication, `bind` derives the memif, fixed IRQ and PCM
format from an active, non-running ALSA playback substream. The complete ring
must match the lease, and the format must remain unchanged. Endpoint reads
check output structure size, substream ownership and format; publication
rechecks ownership and format again. The startup path rejects reuse of a bound
memif until its lease is released. The AFE runtime-suspend callback also returns `EBUSY` while any
lease exists, including when system sleep forces that callback despite PM
usage references. Driver bind/unbind attributes are suppressed, and live
leases prevent module unload. This is a retention guard, not the requested
final shutdown protocol: a verified DSP quiesce/release operation is still
needed before published buffers can be reclaimed.

`base.json` pins the prepared P16 kernel inputs. The generator preserves the
existing clock, runtime-PM, IRQ and audio-routing fixes. SRAM setup failure
leaves the ordinary DRAM audio path available. Only a client reservation
accesses SRAM contents; probe sends no DSP command. The DSP 0.1.7 configuration
client uses the bound endpoint for the task-7/task-16 playback chain.
The initial DL0 frontend and typed task transitions are implemented below;
stream hardware qualification remains open.

Reproduce the patch in a new output file and run the tests:

```sh
python3 scripts/prepare-afe-sram-patch.py --output /tmp/p16-afe-sram.patch
python3 platform/dsp/afe/test_sram.py
python3 -m unittest discover -s tests -v
```

The C harness compiles the actual owner and patched suspend callback under
ASan/UBSan, mocking the OS, pool and register-access edges. It checks firmware
and allocation failures, reference balancing, zeroing, exhaustion/reuse,
address views, publication retention, forced-suspend refusal and eight
concurrent non-overlapping leases. The patch-replay test independently applies
the packaged patch with zero fuzz and compares all resulting files to
the canonical sources and generated integration.

The preceding owner-only candidate passed 131 platform tests and complete AFE
module builds with `W=1`
without warnings against all five installed kernel headers. Build receipts
are in `build/afe-sram-20261106/report.json`. The three existing AFE files were
also reconstructed from the kernel recipe's preceding patch segments and
match the pinned base byte for byte (`recipe-base-replay.json`). No AFE module
was loaded, no live SRAM was allocated and no firmware request was sent.
The target kernel still needs boot and hardware qualification. The local
integration patch has no human DCO sign-off and has not been submitted.

The subsequent `0.1.6` DSP candidate adds initial primary PREPARE with published
endpoint revalidation. The 132-test suite, current patch replay and complete
DSP/AFE `W=1` builds pass for all five installed kernels. The newer receipt in
`build/dsp-pcm-prepare-20261106/report.json` covers the exact final AFE source
bytes, including the last line wrap. The package and both modules remain
uninstalled; these offline checks do not qualify streaming or memory release.

The `0.1.7` integration also reserves independent IPC leases in 1 KiB multiples
from a bound PCM peer's owner. IPC leases cannot bind a PCM substream. The DSP
uses two slices for processing-task commands 5 and 6, through the AFE/VAD
address view. Publication before registration retains the complete IPC lease
on uncertain completion, independently of PCM publication or peer release.
The sanitizer tests cover both roles, invalid sizes/peers, allocation faults,
non-overlap, unpublished reference balance and published IPC retention after
the PCM peer is released. Complete DSP/AFE builds pass on five kernels, as do
all 132 platform tests, exact patch replay and checkpatch. The current receipt
is `build/dsp-processing-20261106/report.json`. Hardware qualification and the
verified whole-core shutdown/release contract remain open.

The `0.1.10` DSP candidate calls `mt8901_afe_sram_prepare()` before sending
DSP PREPARE. Binding and publication alone did not program the hardware ring.
The new helper requires the published, non-running lease and checks the current
substream, DAI, format and fixed IRQ ownership. It sets the memif address to
the allocation's AFE/VAD view using checked `regmap_write()` calls for the
base, inclusive end, base MSB and end MSB. The shared address helper discards
write errors, so it cannot provide this contract. Memif DMA bookkeeping changes
only after all four writes succeed. It leaves the ALSA runtime's DMA
allocation unchanged and does not enable a memif, channel merge or IRQ.
Successful preparation is idempotent. A programming failure may follow partial
register writes, so it is cached and cannot be retried or release the lease.
The sanitizer harness checks a nonzero SRAM allocation offset, AFE versus DSP
address selection, invalid owners, all four write failures and eight
concurrent preparers. Receipts are in `build/dsp-afe-prepare-qualified-20261106/`.

The helper accepts the MT8901 layout with separate base/end MSBs and no optional
FIFO prepare callback. Other layouts fail before register writes. Each of the
four writes propagates its error; partial completion is terminal. The tests
reject changed register metadata and overflowing address extents and verify
that a failed sequence leaves the prior DMA bookkeeping unchanged. They also
verify the ALSA runtime allocation is never changed. The preceding direct-call
draft under `build/dsp-afe-prepare-20261106/` is superseded: its use of the
shared helper could not report an underlying register-write failure.

Version `0.1.11` adds a separate `mt8901_afe_sram_prepare_irq()` operation
and endpoint ABI 2. Address preparation precedes DSP PREPARE; IRQ preparation
follows its four acknowledgements. The owner validates the current substream,
DAI and fixed IRQ, the exact MT8901 register layout, rate code, active memif
clock domain and 24-bit period count. A read error or already-enabled IRQ is
rejected before writing. Checked updates set the period count, rate and domain
while preserving unrelated bits. They do not route or enable the IRQ. A write
failure is cached; successful repetition performs no further writes.

The endpoint's `irq_prepared` flag remains false until all three updates
succeed. The DSP client requires ABI 2 and both preparation helpers before
publishing processing memory. Its fault tests verify the address/firmware/IRQ
order and terminal IRQ failure behavior. The owner tests cover malformed
register metadata, failed rate/domain lookup, an active IRQ, period overflow,
all three update failures, retained allocations and eight concurrent preparers.
Current receipts are in `build/dsp-irq-prepare-qualified-20261106/`.

The native object at stream offset `+0xa8` is hardware gain, not the IRQ
controller; the preceding transition analysis misidentified it. The corrected
register tables and separate speaker IRQ object are recorded in
`dsp-stream-owner-analysis/report.json` in the dated native-analysis results.
These are source/offline results. The current kernel and running AFE module
are unchanged, and stream qualification and DMA release remain open.

Version `0.1.12` adds `mt8901_afe_sram_stop_irq()` for the route-2 HiFi1
client after both task STOP acknowledgements. It revalidates the substream,
format, DAI and fixed IRQ, including an ALSA STOP callback whose substream
is still RUNNING. Checked operations disable the IRQ, read and toggle its
pending-clear bit, then remove its MCU and HiFi1 targets. Unrelated register
bits are preserved. IRQ readiness is cleared before programming; an I/O error
is cached and cannot be retried. Successful repetition performs no writes.

The owner does not disable a memif, power off a DSP or release published
memory. STOP and IRQ disable do not prove DMA quiescence. The actual-function
sanitizer harness covers both pending-clear bit values, failures at all five
I/O steps, all seven playback IRQs, RUNNING ownership, malformed metadata,
idempotence and retained references. These changes are not installed or
hardware-qualified.

Version `0.1.13` integrates a disabled-by-default DSP playback path into the
existing DL0 AFE/SoundWire frontend. The read-only `dsp_playback` module
parameter selects it when a stream opens. It requires a nonatomic PCM and
DSP primary-client ABI 1 before any task request. Probe still performs no
DSP request. The ordinary component setup and ALSA managed-buffer allocator
are reused; pointer, producer ACK and sync-stop callbacks select the active
DSP client or preserve the direct path's behavior.

The frontend configures the SRAM/task graph in PREPARE because ALSA sets the
runtime format and period fields after the driver's HW_PARAMS returns. The
managed application buffer is copied into the DSP-owned coherent host ring
on producer ACK, including full-ring and ALSA-boundary wraps. The exposed
pointer tracks the DSP host ring. Its IRQ listener accumulates byte progress
without treating the echoed producer generation as a period count. Complete
periods and errors are delivered through queued work; work is drained outside
the PCM stream lock and before the primary handle or substream can be freed.

`mt8901_afe_sram_retire()` clears only the borrowed substream pointer on a
published lease. It preserves the reservation, allocation and all references,
and subsequent endpoint/prepare operations fail without dereferencing the old
substream. DSP close calls it after listener synchronization, even when a
failed transaction prevents further firmware requests. Configuration requires
this helper before publishing processing memory. Retirement is not DMA release.

`test_frontend.py` compiles the actual callbacks with ASan/UBSan, verifies
client-ABI and helper checks, producer vectors, partial-period accumulation,
queued period/error delivery, failure retention and a blocked worker racing
with close. The owner test frees a retired substream and then probes all
endpoint/programming operations under the sanitizers. No frontend or transition
has been hardware-qualified; restart, additional endpoints and verified DSP
shutdown/DMA release remain required.
