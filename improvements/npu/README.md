# Optional N1X NPU/DLA

This is a source-patch guide, not a driver installer. GPU/CUDA, ordinary audio,
ALS and coordinated sleep do not need this stack. No DSP consumer is enabled.
The provider and runtime are Johann Hartmann's
[n1x-npu-linux](https://github.com/johannhartmann/n1x-npu-linux), pinned at
`92e9434a65d2b9d3bb0517efb9efc371e9c6b42c`. Original GPL kernel/MIT userspace
notices, NOTICE and LICENSING.md apply. Do not substitute an arbitrary newer
revision without reviewing the secure-loader, runtime and PM ABI together.

## Source preparation

Apply the kernel guide first, including 9110. Use an ARM64 kernel with 4 KiB
pages, DRM_ACCEL and matching headers, NVDA200A revision 0x34/NVDA8600 revision
1, translated isolated IOMMU groups and the missing-_CCA coherency quirk.
The opt-in patch rejects 16/64 KiB page kernels before driver registration and
checks DMA/IORT/coherency before mapping registers. A supported CPU count does
not qualify the accelerator or firmware on another machine.

```sh
git clone https://github.com/johannhartmann/n1x-npu-linux.git n1x-source
cd n1x-source
git checkout --detach 92e9434a65d2b9d3bb0517efb9efc371e9c6b42c
# Set export_dir to this repository's absolute improvements/npu directory.
patch -p1 --fuzz=0 < "$export_dir/provider/0001-explicit-opt-in-and-early-dma-gate.patch"
patch -p1 --fuzz=0 < "$export_dir/provider/0002-dma-mmap-image-offset.patch"
patch -p1 --fuzz=0 < "$export_dir/runtime/0001-local-compiler-inputs.patch"
export N1X_SOURCE_ROOT="$PWD"
python3 "$export_dir/provider/test_opt_in.py"
python3 "$export_dir/provider/test_packaged_accel.py"
python3 "$export_dir/provider/test_dma_mmap.py" --fixture "$export_dir/provider/kernel-mmap-fixture.json"
# Optional: replace --fixture with /absolute/path/to/prepared/linux-7.3.
make -C /path/to/kernel/build M="$PWD/driver" W=1 modules
```

Stop on any patch or test failure. Package source, matching headers, modules and
signatures through your distribution. `provider/dkms.conf` is a neutral DKMS
example. The mmap correction retains the original coherent allocation/size and
uses vm_pgoff to select image pages; adding the image offset to the CPU/DMA base
instead caused the IOMMU mmap path to expose descriptor pages. Its test compiles
the actual provider and Linux IOMMU/remap functions and rejects that baseline.

## Firmware and compiler inputs

Neither this repository nor its tests include OEM firmware, drivers, compiler
libraries or generated models. Source licenses do not grant redistribution
rights for NVIDIA inputs. Acquire the versions described in upstream setup.md
under their applicable terms. `firmware/prepare-firmware.py` accepts only the
pinned Windows ARM64 617.42 nvlddmkm.sys and verifies the two SROOT images;
it writes only an explicit new local output directory. Run its `--help`, review
hashes, then copy firmware manually to the standard nvidia/n1x firmware location.
Never add extracted binaries to public Git/CI artifacts.

`runtime/prepare-compiler.py` checks full hashes of local TensorRT 11.4 Linux
libnvdla_compiler.so and Windows ARM64 nvdla_compiler.dll. The runtime patch
requires those independently supplied inputs under `/usr/local/lib/n1x-npu/`.
The compiler shim maps/patches vendor binaries in process; its applicable terms
and this integration need maintainer review. These tools do not download or
start hardware. To build source-only runtime (C++17, OpenSSL, Python numpy/onnx):

```sh
make runtime/libn1x_dla.so compiler
c++ -std=c++17 -O2 -Wall -Wextra -Werror -fPIC -shared -Iinclude -Iruntime \
  runtime/n1x_ort.cpp runtime/cudla_model.cpp -Lruntime -ln1x_dla \
  -Wl,-rpath,'$ORIGIN' -o runtime/libn1x_ort.so
c++ -std=c++17 -O2 -Wall -Wextra -Werror -DN1X_ORT_CLI_MAIN -Iinclude -Iruntime \
  runtime/n1x_ort.cpp -Lruntime -ln1x_ort -Wl,-rpath,'$ORIGIN' -o runtime/n1x-ort-run
python3 scripts/test_dla_runtime.py
python3 scripts/test_linux_accel_backend.py
```

The public source fallback descriptor builds without proprietary cuDLA headers.
The native ONNX path does not require GPU initialization; this is not transparent
acceleration of arbitrary applications. See upstream runtime APIs and demos.
`runtime/infer.py` is the three-task numerical qualification CLI; its paths match
a neutral `/usr/lib/n1x-npu/runtime` installation. A distribution must package
those source-built libraries/Python modules there before using this CLI.

## Explicit activation and PM

The provider patch removes ACPI autoload and requires `enable=1`. The example
`provider/n1x-npu-control`, service, blacklist and udev rules are optional source
inputs, not installed by this guide. Review and package them for your distro. The example service expects the
control helper at `/usr/lib/n1x-npu/control`; keep those paths consistent.
No modules-load entry, forced reset, automatic service enable or domain mutation
in udev is supplied. The helper validates both isolated groups before selecting
DMA domains; failure retains uncertain ownership and does not retry blindly.
Start manually only after firmware, source/header versions and IOMMU review.
Enable future boot activation only after a fresh-boot numerical and PM test.
Stop through the checked shutdown interface; never force-unload a refused module.

DLA/SROOT device links order suspend/resume; checked idle teardown and restart
can refuse active/poisoned work or published DSP leases. Runtime autosuspend
returns EBUSY: no low idle-power claim. Whole-core DSP shutdown is unfinished,
so DSP remains excluded. Local 41.8 source plus mmap fix passed three changing-
input ReLU jobs and allocation cleanup before and after 78.482 s s2idle on one
BIOS303/20-CPU H7607BA. Public opt-in gates and other machines require their own
QA; see [validation](../VALIDATION.md).

Reviewable distribution examples: [provider PR2395](https://github.com/omacom/omarchy-pkgs/pull/2395),
[local firmware PR2396](https://github.com/omacom/omarchy-pkgs/pull/2396), and
[runtime PR2397](https://github.com/omacom/omarchy-pkgs/pull/2397). The
examples are not prerequisites for a different Linux distribution.

## Portable safety tests

From `improvements/npu/provider`, run `python3 test_control.py` and
`python3 test_control.py --mutations`. They exercise the actual lifecycle helper
against private sysfs fixtures, including isolated groups, validation before
domain writes, partial bindings and checked shutdown before unload.
From `improvements/npu/firmware`, run `python3 test_prepare.py` for synthetic PE
extraction, hash checks, private output permissions, no-overwrite and race checks,
including negative controls. No vendor binaries or hardware are used.
