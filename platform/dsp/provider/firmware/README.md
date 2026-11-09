# Local secure-loader firmware package

Place the two signed images `sroot-v0.bin` and `sroot-v1.bin` in this directory
and run `makepkg`. Obtain them with the sibling NPU repository's
`scripts/prepare_inputs.py`; the PKGBUILD verifies acquisition hashes.

The kernel validates container structure and section bounds, then SROOT
authenticates the image. Firmware bytes remain outside Git under their OEM
license. Installing this package does not start or replace a running device.
