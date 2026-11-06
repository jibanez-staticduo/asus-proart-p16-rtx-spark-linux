# MT8901 audio DSP

The driver implements secure loading, boot notifications, bounded task IPC and
an experimental route-2 playback client for two HiFi5 cores and one HiFi1 core.
ACPI provides memory/IRQ resources and routing properties. Firmware uses the
normal firmware loader, with per-core defaults and a `firmware-name` override.
See [protocol.md](protocol.md) for wire formats and reference provenance.

Firmware startup is disabled by default. Published DSP operating DRAM, AFE
SRAM and provider/module/runtime-PM leases are retained until reboot. Task
STOP, CLOSE and IRQ disable do not establish whole-core DMA quiescence.
Shutdown and reclamation must be verified before default startup is enabled.

## Secure loading and IPC

The driver requires SROOT client ABI v2 before taking power or publishing
operating memory. SROOT authenticates signed images; the host validates their
container structure and bounds. A canonical completed upload may release its
temporary image staging, independently of the retained DSP lifetime lease.
Malformed or uncertain uploads retain staging and block further submissions.

Requests use checked packets, nonzero transaction tokens and serialized
mailbox ownership. ACKs and asynchronous notifications have separate matching
paths. Slot/ACK waits are bounded. Unexpected replies, timeout, power failure
or uncertain ownership make the channel terminal. No raw userspace command
interface is provided.

## Playback

The client supports route 2 with primary task 7 and processing task 16. Task 7
uses boot-owned coherent DRAM. Task 16 uses two AFE-owned 1 KiB IPC slices,
registered before OPEN. HWPARAM configures the host, hardware and bridge rings;
PREPARE orders AFE ring programming, four task descriptors and IRQ preparation.
START runs processing before primary; STOP reverses that task order before
stopping the owned IRQ. Failures retain published memory and stop the sequence.

The AFE DL0 frontend uses the existing DPCM/DAPM/SoundWire routing. Its read-only
`dsp_playback` parameter defaults to false. ALSA's managed buffer is copied into
the coherent host ring on producer ACK. Consumed-position notifications drive
the pointer and complete-period accounting; workqueue delivery handles periods
and errors outside hard IRQ. Close synchronizes listeners and drains work
before retiring borrowed substream pointers.

The frontend requires the matching [AFE owner](afe/README.md) and provider ABI.
Route 3, restart, additional endpoints and complete-stream hardware qualification
remain open. Offline tests do not qualify audible playback or shutdown.

## Build and tests

```sh
make -C platform KDIR=/usr/lib/modules/$(uname -r)/build W=1 -j2
python3 -m unittest discover -s tests -v
cd platform/dsp && makepkg
```

`Kbuild` supports external/DKMS builds and `CONFIG_N1X_ADSP` integration.
Actual-function sanitizer harnesses cover boot, IPC, task/PCM transitions,
producer/period handling, faults and concurrency. Native KUnit protocol tests
are described in [platform/README.md](../README.md). Do not replace a published
live module to validate a new package; qualify changed sources after a reboot.
