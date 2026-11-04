# Licensing

- `acpi/acpi_builder.py` and `acpi/power-*`: MIT, copyright Josephine Pfeiffer. The full license is in `acpi/LICENSE`; provenance and the upstream commit are recorded in `sources.json`.
- `acpi/acpica-unix-20260408.tar.gz`: unmodified ACPICA source release. Its license texts and copyright notices are included in the archive.
- `kernel/`: Linux patches and kernel recipe under their respective source notices and the PKGBUILD's GPL-2.0-only license designation (see [GPL-2.0](licenses/GPL-2.0.txt)). The recipe downloads and verifies the Linux source release; its `COPYING` and `LICENSES/` apply to the resulting kernel. Upstream author and Signed-off-by notices are preserved.
- New installation scripts, `scripts/p16-temperatures`, `scripts/p16-charge-limit`, `cuda/vector-add.cu`, configuration files, tests and documentation in this repository: MIT; see `licenses/MIT.txt`.

Machine firmware dumps, generated EFI images, kernel packages, passwords, private keys and machine-specific UUIDs are excluded from this repository.

`config/speaker-firmware.json` records extraction metadata from Josephine
Pfeiffer's MIT-licensed hardware lock at the commit linked inside it. The
downloaded ASUS/Cirrus firmware retains its OEM license and stays in ignored
`build/` output; it is not redistributed here. The source license is retained
in `acpi/LICENSE`.

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
