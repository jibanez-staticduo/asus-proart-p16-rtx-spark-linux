# Licensing

This repository is a fork of Johann Hartmann's P16 Linux support repository.
Its original files remain available in the inherited Git history and upstream,
under the notices and license information present in those revisions.

## Fork hardware improvements

- `improvements/kernel/*.patch` and `config.reference`: preserved from the
  public pinned kernel recipe in `improvements/sources.json`, under their
  original Linux/source notices and GPL-2.0-only package designation.
  Consolidated ports preserve attribution and do not add author sign-offs.
- `improvements/kernel/test_*`: regression tests accompanying those kernel
  patches, GPL-2.0-only; existing SPDX notices are retained.
- `improvements/telemetry/0001-*` and C tests: GPL-2.0-only, based on Johann
  Hartmann's GPL driver; full text in `improvements/telemetry/LICENSE`.
- New fork documentation, `improvements/verify.py`, `kernel/check.sh` and
  `kernel/config.fragment`: MIT, copyright 2026 P16 Linux contributors;
  `licenses/MIT.txt` supplies the license.
- `improvements/audio/mt8901-acp-sdw-legacy-mach.c` and its route patch retain
  the base driver's GPL-2.0 SPDX license; public audio graph/tests are MIT.
- Audio component provenance and license information is recorded in
  `improvements/audio/README.md`. No OEM-derived audio tuning or firmware
  binaries are added by this fork.
- `improvements/npu/provider/*.patch` and its source-function harnesses retain
  the upstream GPL-2.0-only kernel terms; the runtime/local-input tools follow
  upstream MIT notices and NOTICE/LICENSING.md at the pinned source revision.
- `improvements/diagnostics/` adapts GPL-2.0-only Johann Hartmann drivers/tests;
  the referenced upstream UAPI retains its Linux-syscall-note exception.
- `improvements/tools/` and `improvements/policy/` are MIT; included license
  texts and upstream configuration attribution apply. None grants rights to
  external OEM firmware/compiler binaries, which are not included.
