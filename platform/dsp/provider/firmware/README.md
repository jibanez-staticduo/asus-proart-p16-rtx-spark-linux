# Local secure-loader firmware package

Build with `makepkg` after placing `sroot-v0.bin` and `sroot-v1.bin` here.
The sibling NPU lab's `scripts/prepare_inputs.py` extracts these exact 67,508-byte
images from the pinned OEM display driver; the PKGBUILD and native NPU driver
independently pin both SHA-256 hashes. OEM firmware is not committed to Git.

For this machine the inputs are under `../n1x-lab/npu/sources/` relative to the
P16 repository. Install this package with the provider DKMS package. When adopting
previously unowned files, compare both hashes first and limit any pacman
`--overwrite` to these two exact paths under `/usr/lib/firmware/nvidia/n1x/`.
The package does not load firmware or change a running device.
