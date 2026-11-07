# GPU system-power controls

A bounded SysPL3 reduction on the H7607BA changed GPU clock and throughput.
The current result supports retaining the EC defaults: it does not establish
a safe or useful higher sustained power budget.

## Measured behavior

Measurements used BIOS H7607BA.300, kernel `7.2.8-23-rtx-spark-npu`, NVIDIA
615.78.08 and CUDA 13.4.2 on the RTX Spark N1X. AC was connected, the selected
profile remained `performance`, and PowerMizer remained adaptive (`0`).

The workload was an 8192³ cuBLAS GEMM with FP16 inputs/output, FP32 accumulation
and tensor operations. Three matrices occupied 402653184 bytes, excluding
library/context allocations. After three warm-up GEMMs, batches of four
produced approximately 0.5 s windows. Eight output positions were checked per
window against the expected value 0.25; all checks passed. Existing desktop,
CPU and container workloads continued, so this was not an isolated benchmark.

The complete confirmation ran for 6 s at the default, 4 s with SysPL3 reduced
by 5 W, and 6 s after restoring the default. The table contains medians from
complete workload windows inside elapsed ranges 2–5.9, 7–9.9 and 12–15.9 s.
Power values describe the system, not the GPU alone.

| Phase | Effective SysPL3 | System power | GPU SM clock | Throughput | Windows |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | 130 W | 129.999 W | 1813 MHz | 75.940 TFLOP/s | 7 |
| Reduced | 125 W | 125.000 W | 1735 MHz | 73.619 TFLOP/s | 4 |
| Restored | 130 W | 129.964 W | 1839 MHz | 77.718 TFLOP/s | 7 |

The reduction coincided with 3.06% lower throughput; restoration recovered
throughput to 2.34% above the initial baseline. Temperature drift, varying
background load and the small sample count prevent treating these percentages
as a prediction of gains from higher limits. The confirmation completed with
no NVIDIA kernel errors, maximum recorded SoC temperature 83.55 °C and GPU
temperature 73 °C.

Joint sampling covered all eight OS/EC/UEFI/effective limit slots, six PID
outputs, bounds, TAU fields, filtered power, temperature, controller status,
GPU clock and NVML clock reasons. Status `0x20` tracked the active SysPL3
constraint. The restored phase transitioned toward `0x10`, consistent with
SysPL2 taking over. NVML clock reasons remained `0x400` across this change;
that flag alone does not identify the active system-power controller.

## Incomplete and rejected runs

The longer run planned 30/20/30 s phases but stopped about 42.48 s into the
experiment, during reduction. The last recorded SoC temperature was 84.85 °C,
the whole-degree package reading 85 °C, and GPU temperature 73 °C. Temperature
is the likely cause of the generic state/temperature guard stop; that version
did not log the exact failed guard field. Cleanup restored the override.

Its settled baseline was near 126 W, with status `0x10` and the SysPL3 PID
output at its ceiling. Reducing SysPL3 to 125 W moved status to `0x20`, system
power to 125 W and the SysPL3 PID output below its ceiling. This supports the
interpretation that SysPL2 bounded the longer baseline and SysPL3 became the
lower constraint during reduction. Exact N1X filter equations and TAU units
remain unresolved.

The broader post-run journal inspection also found two NVIDIA
`NV_ERR_NO_MEMORY` failures in `_memdescAllocInternal` during that run. The
original watcher caught Xid/GSP errors but missed this error class. Its
73.206 → 69.450 TFLOP/s comparison is therefore compromised and must not be
used as a clean performance estimate. The responsible process is unidentified;
correct benchmark output does not establish that other GPU clients were
unaffected. A Kubernetes pod separately hit its memory-cgroup limit between
runs, demonstrating concurrent memory pressure without establishing the cause
of the NVIDIA errors.

One intervening attempt was rejected by the temperature preflight before
starting the workload or writing an override. It is not a measurement.

The published controller now also stops on NVIDIA `NV_ERR_`, `Check failed`
and `Out of memory` messages. Pipe-based recognition checks passed for the
actual allocation-error text, Xid and GSP timeout. The corrected controller
was built and self-tested; it has not had another hardware run.

## Qualification and final state

SysPL3 has a measurable short-term role at 130 W, while SysPL2 constrained
the longer baseline near 126 W. Raising SysPL3 alone would not remove that
later SysPL2 constraint. Whether a higher SysPL3 value improves short bursts
has not been tested. The thermal stop and unqualified electrical headroom
provide no basis for a sustained increase. The 250 W register ceiling is not
a safe operating rating.

Only the SysPL3 OS request and its commit doorbell were written. No other
power slot, clock, profile, EC or UEFI value was changed. An independent
read-only capture after the runs verified all eight OS requests at zero,
SysPL1/2 at 126 W and SysPL3/4 at 130 W. The profile remained `performance`.
No persistent module, service, firmware or boot configuration was changed.

The [experiment sources and data](../experiments/gpu-syspl3/README.md) include
the corrected watcher, a separate restore watchdog, raw telemetry, complete
kernel-error checks and offline analysis. The control program is a manual,
machine-gated experiment; the kernel modules retain their firmware/device-ID
matching and have no dependency on it.
