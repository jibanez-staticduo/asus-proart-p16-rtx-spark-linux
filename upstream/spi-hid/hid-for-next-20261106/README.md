# HID for-next review draft

These four local commits were built on HID `for-next` commit
`720806515cb6921ab05caaa6f470f7054b56385e` with the prerequisites below.
They have not been submitted. The maintainer has not selected or accepted
this prerequisite stack.

`target-base.json` records the commits, source pins and patch hashes.
`0000-cover-letter.patch` records testing, limitations and tool assistance.
`recipients.json` is the output of the target tree's `get_maintainer.pl`;
it is a recipient inventory, not evidence that anyone has been contacted.
The draft has no human DCO sign-off. Its `Fixes:` IDs refer to the local
prerequisite imports and must be remapped before submission.

## Reproduce the source tree

Start with a clean worktree at the maintainer base above. Set `P16_SRC` to
this repository's absolute path, then run:

```sh
git am "$P16_SRC"/kernel/211[1-9]-*.patch "$P16_SRC"/kernel/2120-*.patch
git am --include=drivers/spi/spi.c --include=include/linux/spi/spi.h \
  "$P16_SRC/kernel/2152-arm64-qcom-add-Surface-Pro-11-platform-support.patch"
git am --include='drivers/hid/spi-hid/*' \
  --include=Documentation/devicetree/bindings/input/hid-over-spi.yaml \
  "$P16_SRC/kernel/2152-arm64-qcom-add-Surface-Pro-11-platform-support.patch"
git am "$P16_SRC"/upstream/spi-hid/hid-for-next-20261106/000[1-4]-*.patch
```

The two filtered imports of patch 2152 supply coherent RX support in the
SPI core and the SPI HID changes, respectively. They exclude its other
platform changes. Original prerequisite authorship and sign-offs are
preserved. Import dates can change commit IDs; the resulting source and
patch IDs are reproducible.

## Validation

The build report is `build/hid-target-20261106/report.json` in this
repository. The ARM64 modular configuration links `vmlinux` and all three
SPI HID modules with both ACPI and OF enabled. All four commits build
individually. The second configuration links the OF driver built into
`vmlinux`, with ACPI, DMI, PM and modules disabled. These are build-test
configurations; neither kernel was installed or booted.

No SPI HID compiler warnings were emitted. Each full minimal kernel build
retains the same 526 warning lines from unmodified ARM64, proc, block and
library code; these are recorded separately from driver diagnostics.
All four patches pass the target tree's checkpatch with `--no-signoff`.
The exported series also reproduces the review commit's complete tree
through an independent Git index.

Run the tests against the prepared target source:

```sh
python3 "$P16_SRC/platform/sensorhub/test_requests.py" \
  --source drivers/hid/spi-hid/spi-hid-core.c
python3 "$P16_SRC/platform/sensorhub/test_request_race.py" \
  --baseline-source /path/to/prerequisite-spi-hid-core.c \
  --source drivers/hid/spi-hid/spi-hid-core.c
```

The race test compiles the actual synchronous-request, GET and raw-request
callbacks from both sources under ASan/UBSan, mocking the wire and OS edges.
It stops caller 1 just before its reply copy and starts caller 2. The
prerequisite callback returns caller 2's payload to caller 1; the fixed
callback prevents replacement until caller 1 has copied its complete reply.
The saved baseline is in `build/hid-target-20261106/`.

Runtime lockdep on this target kernel and testing on other SPI HID hardware
remain open. Known prerequisite limitations, including fragmented reports,
are outside these four fixes.
