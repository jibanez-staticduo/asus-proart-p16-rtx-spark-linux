# SPDX-License-Identifier: MIT
"""Compute statistics from complete windows inside each recorded phase."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics


def stats(values):
    return {
        "n": len(values),
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
        "mean": statistics.mean(values),
    }


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def summarize(directory, ranges):
    rows = read_rows(directory / "telemetry.jsonl")
    work = read_rows(directory / "workload.jsonl")
    samples = [
        row for row in rows
        if row["kind"] == "sample" and row["phase"] != "preflight"
    ]
    start = statistics.median(row["monotonic"] - row["elapsed"] for row in samples)
    windows = [row for row in work if row["kind"] == "window"]
    phases = {}
    for label, lower, upper in ranges:
        selected = [row for row in samples if lower <= row["elapsed"] <= upper]
        complete = [
            row for row in windows
            if row["start"] >= start + lower and row["end"] <= start + upper
        ]
        if not selected or not complete:
            raise ValueError(f"No complete measurements for {directory.name}/{label}")
        phases[label] = {
            "elapsed_range": [lower, upper],
            "wall_tflops": stats([row["wall_tflops"] for row in complete]),
            "gpu_tflops": stats([row["gpu_tflops"] for row in complete]),
            "sm_mhz": stats([row["sm_mhz"] for row in selected]),
            "gpu_temp_c": stats([row["gpu_temp_c"] for row in selected]),
            "soc_temp_c": stats([
                row["words"]["0x818"] / 10 - 273.15 for row in selected
            ]),
            "controller_raw_histogram": dict(Counter(
                row["words"]["0x048"] for row in selected
            )),
            "clock_reason_histogram": dict(Counter(
                row["clock_reasons"] for row in selected
            )),
            "words": {
                offset: stats([row["words"][offset] for row in selected])
                for offset in selected[0]["words"]
            },
        }
    baseline = phases["baseline"]["wall_tflops"]["median"]
    report = {
        "metadata": rows[0],
        "final": rows[-1],
        "all_workload_checks_passed": bool(windows) and all(
            row["correct"] for row in windows
        ),
        "events": [row for row in rows if row["kind"] in {"applied", "restored", "abort"}],
        "kernel_check": json.loads((directory / "kernel-check-full.json").read_text()),
        "phases": phases,
        "median_throughput_change_percent": 100 * (
            phases["limited"]["wall_tflops"]["median"] / baseline - 1
        ),
    }
    if "recovered" in phases:
        report["recovery_vs_baseline_percent"] = 100 * (
            phases["recovered"]["wall_tflops"]["median"] / baseline - 1
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = Path(__file__).resolve().parent / "data"
    report = {
        "metadata": json.loads((data / "metadata.json").read_text()),
        "runs": {
            "run-01": summarize(data / "run-01", [
                ("baseline", 15, 29.5), ("limited", 32, 42.4),
            ]),
            "run-03": summarize(data / "run-03", [
                ("baseline", 2, 5.9), ("limited", 7, 9.9), ("recovered", 12, 15.9),
            ]),
        },
        "run-02": {
            "status": "rejected_before_workload_or_write",
            "reason": (data / "run-02/controller.log").read_text().strip(),
        },
        "post_state": json.loads((data / "post-state.json").read_text()),
        "data_sha256": {
            str(path.relative_to(data)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(data.rglob("*")) if path.is_file()
        },
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    for name, run in report["runs"].items():
        print(f"{name}: result={run['final']['result']}, "
              f"correct={run['all_workload_checks_passed']}")
        for phase, values in run["phases"].items():
            print(f"  {phase}: {values['wall_tflops']['median']:.3f} TFLOP/s, "
                  f"{values['wall_tflops']['n']} windows")


if __name__ == "__main__":
    main()
