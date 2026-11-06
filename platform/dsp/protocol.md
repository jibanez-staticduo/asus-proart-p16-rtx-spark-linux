# MT8901 DSP protocol evidence

This is a static-analysis record, not a claim of hardware qualification. The
Windows drivers were imported into separate Ghidra databases and never executed.
The original binaries remain unchanged. Addresses below refer to image virtual
addresses in the pinned drivers.

## Inputs

| Driver | Version | SHA256 |
| --- | --- | --- |
| `nvadsp.sys` | 2.13.0.1 | `8b21e4652bb0ac178a2c470c8d6f982155e715c46112ac3b93de3ebfe00b13d6` |
| `nvasd.sys` | 2.13.0.0 | `a3eaa49ea54b1a7511542091efbd527000aeff4148753e68f179077c539224de` |
| `nvpep.sys` | 2.6.0.0 | `34cf1f2a9372a092555fb0887bfb76a8f205a76ff7e557224b353b09497c9bb1` |

All three drivers are now pinned in `config/platform-oem.json`. The new audio
client and power-driver inputs were independently reconstructed from the outer
ASUS recovery archive after its SHA256 matched `config/platform-sources.json`.
All 17 selected inputs passed size/hash verification. The extraction receipt is
`n1x-lab/platform/sources/vendor/platform-drivers.receipt.json`.

`nvasd.inf` identifies its driver as the ACX Streaming Device for `NVDA9022`.
The driver contains separate MT8195 and MT8901 implementations. Only the
MT8901 paths are relevant to this machine; the shared image also contains
Bluetooth sideband paths, which are not proof that those features are wired
on this board.

## Firmware power request

`nvadsp` function `0x140006220` dispatches notification class 2, command
`0xf005`. It reads a little-endian 32-bit value at packet offset 24 and schedules
`0x140006a70`. That worker calls `0x14004fb38`, which sends a 16-byte
`PoFxPowerControl` input with GUID
`e3b43f54-2e2d-43ae-b348-f2d3a5fc6700`:

| Input offset | Width | Value |
| --- | --- | --- |
| 0 | 8 bits | 0 |
| 4 | 32 bits | 1 |
| 8 | 32 bits | Firmware notification value |
| 12 | 32 bits | 0 |

The same GUID occurs at `nvpep` address `0x140047b80`. Its handler
`0x14001ff38` checks for a non-null input of exactly 16 bytes,
then ORs the request byte and three input words into the per-device SCMI
configuration before calling `0x1400169d0`. This is the same operation as
Linux `mtk_send_power_control_req()` in `drivers/soc/mediatek/power_wrap/`.
The field mapping is therefore supported by both caller and receiver code.
Its execution on running DSP firmware remains unverified.

The Linux candidate queues these notifications and dispatches them from a
worker. It records the first submission error or queue overflow and performs
no further power submissions for that instance. This does not establish a
DSP shutdown contract: `0xf005` is a firmware-initiated request, not a host
shutdown acknowledgement.

## Audio request packet construction

`nvasd` function `0x140002268` constructs a 288-byte packet. Its callers clear
the backing buffer before construction. The fields directly observed in
this constructor are:

| Packet offset | Width | Observed content |
| --- | --- | --- |
| 0 | 32 bits | 2 |
| 4 | 32 bits | 16, or 16 plus inline payload length |
| 18 | 8 bits | Caller-provided IPI/channel ID |
| 19 | 8 bits | `0x10` |
| 20 | 8 bits | Caller-provided data mode |
| 21 | 8 bits | Caller-provided acknowledgement mode |
| 22 | 16 bits | Command |
| 24 | 32 bits | First parameter; inline payload length in data mode 1 |
| 28 | 32 bits | Second parameter |
| 32 | Up to 256 bytes | Inline payload in data mode 1 |

The constructor rejects data mode 1 when the payload pointer is null or the
payload length exceeds 256. It does not establish the receive-side validity
rules for firmware packets. In particular, a transmit-side constant is not
sufficient evidence to reject a firmware notification with a different field.

For acknowledged sends, `nvadsp` assigns a nonzero transaction value at packet
offset 16. Its acknowledgement matcher compares that 16-bit value. Its client
notification matcher compares the first 32-bit packet field and the byte at offset 18.
Those are separate matching paths and must not share an untyped completion.

`n1x_adsp_ipc.h` implements the observed request constructor and field decoder
as an internal codec. Its tests cover literal wire vectors, maximum payloads,
untouched outputs on rejection, every packet truncation, and separation of
acknowledgements from notifications. The codec does not publish SRAM, ring a
doorbell or define a userspace ABI. Hardware transmission and its lifetime
state machine remain to be integrated and tested.

## Mailbox slots and ownership

`nvadsp` function `0x140030000` maps the final 1024 bytes of shared SRAM as the
control area. Receive data begins at control offset `0x20`. Its mailbox
configuration literals at `0x1400504f0` contain a 288-byte slot stride and a
288-byte packet size. `0x14004aa48` queues transmit records with interrupt
index 0 and slot index 1. The transmit worker at `0x140024248` passes those
indices through `0x14004a7d8`; the copy function `0x14004a570` writes into
the shared SRAM slot, not the mailbox register aperture.

| Control-area offset | Observed role |
| --- | --- |
| `0x10` | Receive-slot ownership word, released by the host after copying |
| `0x14` | Transmit busy word, checked and set by the host before publication |
| `0x20` | Receive packet, 288 bytes |
| `0x140` | Transmit packet, 288 bytes; receive base plus one slot stride |

`0x14004b2b8`, called from `0x14004f898`, installs the transmit busy-word pointer.
The transmit worker sets that word before copying, then the mailbox helper
publishes the interrupt after a barrier. The INF pins inbound status/set to
mailbox offset `0x100`, outbound status to `0x104`, and outbound clear to
`0x10c`. Interrupt index zero uses bit zero of the inbound set register.

A future transport must serialize publication, wait for a matching
acknowledgement separately from notifications, and preserve ownership on
uncertain completion. A timeout does not establish that firmware has stopped
reading the SRAM slot or any DRAM buffer described by the request. None of
these transmit paths has been exercised by the Linux candidate yet.

## Notification registration is not a wire command

`nvasd` function `0x140004e50` creates a request with command field `0xdf00`,
then submits IOCTL `0x152010`. The IOCTL value is stored at literal address
`0x14000548c` in that driver.

`nvadsp` identifies the same IOCTL through the literal at `0x14000424c`.
That branch queues a pending client notification request. It does not call
the mailbox send function. Therefore `0xdf00` in this path is a host-side
notification filter; it must not be sent to firmware as a guessed discovery
or initialization command.

The actual MT8901 audio paths construct acknowledged inline requests for
commands 5, 6, `0x20a` and `0x20b`, with 32-byte payloads. Other paths exist
for Bluetooth sideband. Their payload layouts, DMA ownership, stream state
transitions and completion rules still need to be recovered before adding
Linux audio or compute interfaces.
