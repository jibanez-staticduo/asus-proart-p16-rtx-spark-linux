# Hardware validation and remaining work

These observations are from an ASUS ProArt P16 H7607BA, N1X ARM64, BIOS 303.
They describe what was tested, not certification of every distro or BIOS.

| Area | Evidence | Limits |
|---|---|---|
| Kernel and GPU | Full native ARM64 Linux 7.3 build and successful boot; NVIDIA 615.78.08 active | The compact vanilla7.3 patch export is source-tested; its exact standalone combination has not had a second full kernel build |
| Coordinated s2idle | Admission for all 20 CPUs; three manual lid cycles of roughly 380, 152 and 1221 seconds recovered GPU/Wi-Fi/SD | No direct physical DRIPS residency measurement; one earlier external-module trial had intermittent Wi-Fi scanning failure |
| Sleep energy | 92.296 Wh to 91.847 Wh across 1225.775 s: 0.449 Wh, about 1.319 W including transitions | One long integrated-kernel trial, gauge timing limits precision; not a new measurement of the final sensor-reset revision |
| ASUS WMI | Loaded on ARM64; firmware profiles, battery threshold and fan RPM exposed | Factory fan policy retained; no custom fan-curve thermal qualification |
| ASUS optional initialization | Architecture, i8042 and per-fan capability harnesses pass; no corresponding warnings on the tested boot | Other ASUS machines not physically tested |
| SPI-HID/light sensor | `_RST` succeeds; GPIO 190 interrupt and HID/IIO hub appear; cover/light test changes 0 to about 2175 lux then ambient | Absolute calibration unverified |
| Sensor resume | One lid s2idle cycle of about 57s; sensor reports 13 lux after resume; SPI error/timeout counters 0; GPU/audio active | Repeated cycles and long sleep-power measurement with this revision pending |
| Audio | DAPM route correction and local PipeWire graph work after boot; jack switching exercised | OEM-derived tuning is not redistributed; see audio guide for the public configuration's validation scope |
| Telemetry | Production firmware parsing, probe-policy and RAM-backed MMIO tests; exported patch compiles against the running Linux 7.3 headers; driver loaded on this hardware | Channel availability depends on firmware; absolute energy calibration and inherited diagnostic interpretation remain limited |
| Keys | Fn+Esc and ASUS application key received/usable | Fn+F7 external display selection awaits an external monitor |
| Bluetooth | Controller powered after the sensor-resume cycle | One opcode0x0c24 warning during suspend entry; peripheral reconnection not validated |

BIOS eligibility is intentionally not a version whitelist. The code still
checks model/platform and relevant firmware contracts. This does not mean
all BIOS releases have been physically tested.

No DSDT override was used for these BIOS 303 tests. The upstream BIOS 300 ACPI
builder remains historical reference; it is not required by this patch guide.

The exported source tests exercise actual patched functions using stubs,
including ASan/UBSan where specified. They cannot prove firmware cooperation,
hardware power draw, wake reliability or audio tuning quality.

## Export verification (2026-11-04)

- All 83 kernel patches apply to pristine Linux 7.3 with `--fuzz=0`.
- Reference config plus fragment survives `make olddefconfig`; all exported
  kernel source harnesses pass on that resulting tree.
- Telemetry patch applies to its pinned source; firmware parser, probe policy
  and MMIO callback tests pass, and `spbm.ko` builds against the running ARM64
  Linux 7.3 headers. No module is loaded by these build checks.
- Audio route patch applies cleanly to that tree; the public graph and
  hardware-gate export checks pass. The neutral graph has not had an isolated
  runtime/audio validation or physical listening test.
