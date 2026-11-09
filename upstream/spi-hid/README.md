# SPI HID patch review

These five unsent patches separate the ACPI IRQ, reset, report transaction,
stream-only sensor and decoded input-body fixes. `series` specifies their application order.
`baseline.json` pins every prerequisite and records the hashes of the baseline
and resulting source files. Reapplying the series reproduces all five packaged
SPI HID source files byte for byte.

The HID maintainer's `for-next` tree was inspected at commit
`720806515cb6921ab05caaa6f470f7054b56385e`; it does not contain
`drivers/hid/spi-hid/`. The draft therefore depends on the prerequisite series
being selected by the maintainer before it can be rebased there.

The subsequent [for-next review draft](hid-for-next-20261106/README.md)
applies the pinned prerequisites and these fixes to that exact maintainer
base. It includes the required coherent-RX SPI-core dependency, full ARM64
module/built-in link checks, per-commit builds, a reproduced baseline reply
race and recipient discovery. It is a local integration stack; prerequisite
selection, target-kernel hardware qualification and human DCO review remain
open. The reset diagnostic in this revision uses debug level.

The prerequisite SPI HID driver is the June 9, 2026 series carried in kernel
patches 2111–2120, followed by the SPI HID changes in patch 2152. The latter
provides multi-lane transfers and coherent receive buffers. Those dependencies
are required; these are not standalone patches against an unmodified Linux
tree. Existing author and license notices are preserved.

The five fixes are:

1. Retry a missing ACPI GPIO IRQ at probe, including deferred-probe propagation.
2. Evaluate `_RST` using the ACPI namespace handle.
3. Select and receive input versus feature reports correctly, and retain the
   request mutex until the matching response has been copied.
4. Restrict stream-only input handling to the `MSFW1111` sensor controller (HID VID 0000, PID 0100). Its
   asynchronous DATA reports complete HID sensor reads; synchronous raw input
   requests return `-EOPNOTSUPP`.
5. Keep decoded input lengths in a native `u16` structure, separately from
   the packed little-endian wire header. The shared wire format is unchanged.

Regenerate into a new empty directory:

```sh
python3 scripts/prepare-hid-series.py --output /path/to/empty-directory
python3 -m unittest discover -s tests -p test_hid_series.py
```

The tests reconstruct the prerequisite sources, check each patch independently
against the preceding state, apply the complete series, and compare the result
with the packaged sources. These checks establish source equivalence, not
hardware behavior or compatibility with a newer HID tree.

`platform/sensorhub/test_requests.py` compiles the actual GET-report callback
with AddressSanitizer and UndefinedBehaviorSanitizer. It checks report types,
short buffers, malformed replies, timeout cleanup and two concurrent callers.
The concurrency case holds the first caller at response-copy time while the
second attempts to acquire the request mutex, then verifies both replies.

Submission still requires rebasing onto the maintainer's selected SPI HID
series, current-tree build and hardware testing, and an author-reviewed DCO
sign-off. No sign-off, review, or maintainer acceptance is asserted by these
generated drafts.
