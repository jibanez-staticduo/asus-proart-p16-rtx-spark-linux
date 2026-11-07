# SysPL3 experiment

C/NVML controller, CUDA workload and telemetry from a bounded 5 W SysPL3
reduction on the H7607BA. See [results and limitations](../../docs/gpu-power.md).
This is a manual experiment, with no package or module integration.

Build and check without root:

```sh
make -C experiments/gpu-syspl3
make -C experiments/gpu-syspl3 check analyze
```

Requirements: C compiler, NVIDIA NVML development headers/library, CUDA 13.4
with `sm_121` support, cuBLAS and Python 3 for offline statistics. `check` uses
anonymous memory and pipes; it does not access hardware. `analyze` reads the
recorded data and writes `out/results.json`, without reading the host's state.

The controller refuses hardware execution unless a local boot ID and non-root
workload username are supplied at compilation as the string macros
`STUDY_BOOT_ID` and `STUDY_USER`. They are deliberately unset in the Makefile.
Product, BIOS, AC and performance-profile checks remain enforced. The hardware
invocation takes an existing empty output directory and the absolute workload
binary path. It requires root for `/dev/mem`; the CUDA child drops privileges.
There is no Makefile target that executes a hardware experiment.

The only MMIO writes are the SysPL3 OS request at `0x1c238118` and its commit
doorbell at `0x1c238000`. The request is 5 W below the current EC value. A
separate watchdog restores the owned override on parent death or deadline.
The temperature stop is 85 °C. These checks qualify neither a higher budget
nor this address on another platform.

`data/` contains the unmodified controller/workload telemetry and the final
read-only register capture. `metadata.json` identifies the measured software
and hashes the local source versions used in each hardware run. The published
controller includes the subsequent memory-error watcher fix and replaces the
local boot ID and username with compile-time gates. Machine UUIDs, binaries,
firmware dumps and unrelated container logs are excluded.

`kernel-check-full.json` records the broader post-run journal check. It includes
the NVIDIA allocation failures missed by run-01's original Xid/GSP watcher.
