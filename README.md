# ASUS ProArt P16 RTX Spark: Linux hardware patches

Linux hardware support for the **ASUS ProArt P16 H7607BA with NVIDIA N1X / RTX
Spark (AArch64)**. This fork collects the fixes developed and tested on BIOS 303
on top of [Johann Hartmann's original repository](https://github.com/johannhartmann/asus-proart-p16-rtx-spark-linux)
and Josephine Pfeiffer's [P16 work](https://github.com/pfeifferj/archlinuxarm-proart-p16).

## Start here

The current, distribution-independent guide is in **[improvements/](improvements/README.md)**.
It provides source patches, prerequisites, configuration and regression tests.
Use your distribution's kernel/module packaging and boot procedure.

| Component | Improvements |
|---|---|
| [Kernel](improvements/kernel/README.md) | Coordinated s2idle, LPS0 timing, PCI/SD resume fixes, MT7925 MLO correction and required N1X platform prerequisites |
| ASUS platform | ARM64 WMI, exposed/preserved battery charge threshold, fan RPM and capability-aware initialization |
| Light sensor | Virtual GPIO/IRQ handling and ACPI reset fix that brings up SPI-HID and IIO illuminance |
| [Audio](improvements/audio/README.md) | Board-specific speaker routing and public audio support instructions |
| [Telemetry](improvements/telemetry/README.md) | Linux 7.3 driver binding, BIOS-independent exact-model admission and validated MMIO sensor fallback |
| [Validation](improvements/VALIDATION.md) | What was physically tested, measured results and remaining limitations |

The physical target is H7607BA BIOS 303. Other BIOS releases are not excluded by
number, but must pass the code's hardware and firmware compatibility checks.
AMD/x86 ProArt models are different machines. Read the component guides before
applying patches; this is not an unattended installer.

## Original work and attribution

This is a GitHub fork of [Johann Hartmann's P16 repository](https://github.com/johannhartmann/asus-proart-p16-rtx-spark-linux),
which builds on [Josephine Pfeiffer's P16 work](https://github.com/pfeifferj/archlinuxarm-proart-p16).
The original BIOS 300/Linux 7.2.8 installer remains available there and in the
fork's inherited Git history. The current tree contains the portable hardware
patches and their guides. Our BIOS 303 tests did not use a DSDT override.

[Source pins and attribution](improvements/sources.json) and
[licensing](LICENSES.md) accompany the patches. Linux/NVIDIA/ASUS kernel work
retains its original notices; the fork does not claim authorship of the base
platform implementation.
