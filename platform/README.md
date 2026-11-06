# Platform driver matching

The drivers match their hardware interfaces, independently of the computer's
manufacturer, product name and BIOS version. ACPI IDs select supported IP
blocks; resource sizes, firmware ABI versions and protocol bounds are checked
before access. Register offsets, image IDs and supported task graphs describe
the hardware or firmware ABI and remain part of the implementation.

- DSP uses the ACPI memory/IRQ resources. Firmware uses the normal firmware
  loader, with a `firmware-name` property override and per-core default names.
  Related devices are located through ACPI companions rather than generated
  platform instance names.
- AFE SRAM size and CPU/VAD/DSP address views come from the existing ASD
  firmware properties. Alignment, address-width and overflow checks apply to
  each view. ALSA buffer limits follow the reported SRAM capacity.
- SCP uses ACPI SRAM and mailbox resources. The implemented log/query ABI is
  checked directly; different firmware ABIs need corresponding protocol support.
- Pluton uses the ACPI register window and stride alias, and checks `_HRV`.
- SPI HID identifies the stream-only MSFW1111 controller by its ACPI ID and
  HID VID/PID. It does not depend on the host system's DMI strings.
- NPU/SROOT uses the GB10/T264 display VBIOS-locator register. Its address is
  part of the SoC register map; the NVDA3000 SVP resource does not describe it.
  Firmware staging uses the loaded image size. The host checks signed-container
  structure and section bounds; SROOT authenticates its certificate/signatures.
  Firmware package checksums remain acquisition checks, outside the driver.
- hwmon selects the supported MT8901 MTEL ABI using firmware `PLID = 2` and the
  advertised MTEL schema. Its fixed SoC register map is not an ASUS board table.
  The maintained patch is applied to the pinned hwmon source by
  `scripts/prepare-spark-hwmon.sh`.

These changes do not establish hardware qualification on other computers.
Published DSP memory still requires a verified quiesce/shutdown contract before
release. No running module is replaced by preparing or testing these sources.
