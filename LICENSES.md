# Licensing

- `acpi/acpi_builder.py` and `acpi/power-*`: MIT, copyright Josephine Pfeiffer. The full license is in `acpi/LICENSE`; provenance and the upstream commit are recorded in `sources.json`.
- `acpi/acpica-unix-20260408.tar.gz`: unmodified ACPICA source release. Its license texts and copyright notices are included in the archive.
- `kernel/`: Linux patches and kernel recipe under their respective source notices and the PKGBUILD's GPL-2.0-only license designation (see [GPL-2.0](licenses/GPL-2.0.txt)). The recipe downloads and verifies the Linux source release; its `COPYING` and `LICENSES/` apply to the resulting kernel. Upstream author and Signed-off-by notices are preserved.
- Installation helpers, `scripts/p16-temperatures`, `cuda/vector-add.cu`, configuration files, tests and documentation in this repository: MIT; see `licenses/MIT.txt`.
- `experiments/gpu-syspl3/`: MIT; see `licenses/MIT.txt`.
- Platform kernel drivers and internal headers under `platform/`, including Kconfig/Kbuild files and KUnit tests,: GPL-2.0-only, except the Pluton userspace ABI header marked `GPL-2.0 WITH Linux-syscall-note`. The DSP power-management attribution is retained in its source header.
- `platform/sensorhub/`: GPL-2.0-only, with the SPI HID authors' copyright notices retained in each source file.
- `platform/als/`: upstream HID ALS source and private trigger header under their existing GPL-2.0-only notices. The pinned commit and standalone-build adaptation are recorded in `platform/als/provenance.json`.

File-level license notices take precedence over this summary. External OEM
firmware and reference binaries retain their original licenses; this repository's
source licenses do not grant redistribution rights for those inputs.

Machine firmware dumps, generated EFI images, kernel packages, passwords, private keys and machine-specific UUIDs are excluded from this repository.

`config/speaker-firmware.json` records extraction metadata from Josephine
Pfeiffer's MIT-licensed hardware lock at the commit linked inside it. The
downloaded ASUS/Cirrus firmware retains its OEM license and stays in ignored
`build/` output; it is not redistributed here. The source license is retained
in `acpi/LICENSE`.
