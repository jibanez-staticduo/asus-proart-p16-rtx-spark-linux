# HID ALS channel-scale backport

This packages the upstream HID ALS driver at Linux commit
[`cad928dad9a4c26aa56b4223dc2a29bca517fb5f`](https://github.com/torvalds/linux/commit/cad928dad9a4c26aa56b4223dc2a29bca517fb5f).
The only source adaptation is the trigger-header include path for the
standalone build. Original copyright and license notices are preserved.

The P16's SCP firmware exposes its VEML3328 sensor through SPI HID. The
existing HID sensor hub and HID ALS IIO client already carry the measurements;
a second IIO device over the diagnostic SCP mailbox would duplicate that
sensor. Its descriptor advertises these unit exponents:

| Measurement | Exponent | Scale |
| --- | --- | --- |
| Illumination / intensity | -1 | 0.1 |
| Color temperature | 0 | 1 |
| Chromaticity X / Y | -4 | 0.0001 |

The older ALS driver applies the illumination scale to every channel. The
upstream fix stores and returns the scale of each channel's descriptor.
`provenance.json` pins the upstream sources and the observed P16 descriptor.

This source revision also includes upstream callback-registration ordering:
sensor callbacks are registered before the IIO device becomes visible, and
the callback is removed if registration fails.

Build the source tests and module:

```sh
python3 platform/als/test_scale.py
make -C /usr/lib/modules/$(uname -r)/build M="$PWD/platform/als" W=1 modules
cd platform/als && makepkg -f
```

The tests compile the actual descriptor parser and scale-read callback with
AddressSanitizer and UndefinedBehaviorSanitizer. They cover the P16's differing
scales, devices with only color channels, missing fields and invalid scan
indices. A runtime qualification must also check the exposed IIO scales and
real samples. Correct descriptor units do not establish optical calibration.

The November 6, 2026 P16 trial verified the four scales above and all five raw
channels. One set converted to 143 lux, 4431 K and chromaticity X/Y of
0.3761/0.3747. No new kernel messages appeared and all boot hashes stayed
unchanged. The result is recorded in
`n1x-lab/results/platform-enablement-20261106/als-upstream-trial.json`.

The DKMS package replaces only `hid_sensor_als`. It uses the existing sensor
hub, trigger, power management and SPI transport. Installation must first
verify that this module is absent from all managed initramfs images; it must
not silently leave an older embedded copy in use.
