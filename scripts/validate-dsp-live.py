#!/usr/bin/env python3
"""Capture DSP/NPU status; optionally trial DSP binding without booting firmware.

The probe trial restores the installed power-management driver. It never
replaces the NPU module, loads DSP firmware, installs packages or writes EFI.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
DEVICES = tuple("NVDA830" + str(i) + ":00" for i in (1, 2, 3))
PLATFORM = Path("/sys/bus/platform/devices")
ORIGINAL = "sof-audio-acpi-mt8901"


def run(args, timeout=30):
    return subprocess.check_output([str(x) for x in args], text=True,
                                   stderr=subprocess.STDOUT, timeout=timeout)


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot():
    result = {"kernel": os.uname().release, "dsp": {}, "npu": {}}
    for device in DEVICES:
        path = PLATFORM / device
        item = {"driver": (path / "driver").resolve().name if (path / "driver").exists() else None}
        for name in ("power/runtime_status", "power/runtime_usage", "power/control", "status"):
            file = path / name
            item[name] = file.read_text().strip() if file.exists() else None
        result["dsp"][device] = item
    for name in ("status", "hfrp_status", "shutdown_status"):
        for directory in (Path("/sys/kernel/debug/n1x-npu-bootstrap"), PLATFORM / "NVDA200A:00"):
            file = directory / name
            if file.exists():
                result["npu"][str(file)] = file.read_text().strip()
                break
    for name in ("n1x_npu_bootstrap", "snd_sof_mt8901", "n1x_adsp"):
        path = Path("/sys/module") / name
        if path.exists():
            result[name] = {field: (path / field).read_text().strip()
                            for field in ("srcversion", "refcnt") if (path / field).exists()}
    return result


def wait_suspended(expected):
    deadline = time.monotonic() + 4
    while True:
        state = snapshot()
        if all(v["driver"] == expected and v["power/runtime_status"] == "suspended" and
               v["power/runtime_usage"] == "0" for v in state["dsp"].values()):
            return state
        if time.monotonic() >= deadline:
            raise RuntimeError("DSP binding/idle state differs from expected: " + json.dumps(state["dsp"]))
        time.sleep(0.1)


def probe_trial(module, report, modules=Path("/sys/module"),
                drivers=Path("/sys/bus/platform/drivers"), inspect=False):
    live = load_script("platform_live", "validate-platform-live.py")
    live.check_target()
    module = live.check_module(module)
    report["candidate"] = {"path": str(module),
                           "sha256": hashlib.sha256(module.read_bytes()).hexdigest(),
                           "srcversion": run(["modinfo", "-F", "srcversion", module]).strip(),
                           "enable_boot": False}
    wait_suspended(ORIGINAL)
    if (modules / "n1x_adsp").exists():
        raise RuntimeError("Existing n1x_adsp instance; refusing to replace it")
    if (modules / "snd_sof_mt8901/refcnt").read_text().strip() != "0":
        raise RuntimeError("Original DSP driver is in use; no unload attempted")
    driver = drivers / ORIGINAL
    bound = {p.name for p in driver.iterdir() if p.is_symlink() and p.name != "module"}
    if bound != set(DEVICES):
        raise RuntimeError("Original module serves unexpected devices; no unload attempted")
    run(["modinfo", "-n", "snd_sof_mt8901"])
    original_removed = inserted = False
    try:
        run(["rmmod", "snd_sof_mt8901"])
        original_removed = True
        run(["insmod", module, "enable_boot=0"])
        inserted = True
        if (modules / "n1x_adsp/parameters/enable_boot").read_text().strip() != "N":
            raise RuntimeError("Probe trial must have firmware boot disabled")
        report["probe"] = wait_suspended("n1x-adsp")
        for item in report["probe"]["dsp"].values():
            fields = dict(word.split("=", 1) for word in item["status"].split())
            if any(fields.get(name) != "0" for name in
                   ("attempted", "published", "loader_published", "loader_accepted", "ready", "notifications")):
                raise RuntimeError("Unexpected firmware activity during probe-only trial")
        if inspect:
            report["read_only_mmio"] = {device: (PLATFORM / device / "inspect").read_text().strip()
                                        for device in DEVICES}
            wait_suspended("n1x-adsp")
    finally:
        if inserted:
            run(["rmmod", "n1x_adsp"])
        if original_removed:
            run(["modprobe", "snd_sof_mt8901"])
            report["restored"] = wait_suspended(ORIGINAL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--test-probe", action="store_true")
    parser.add_argument("--inspect", action="store_true", help="root-only mailbox/config reads during the probe trial")
    parser.add_argument("--module", type=Path, default=ROOT / "platform/dsp/n1x_adsp.ko")
    args = parser.parse_args()
    if os.geteuid():
        parser.error("Run as root via sudo or pkexec")
    if args.inspect and not args.test_probe:
        parser.error("--inspect requires --test-probe")
    if args.report.exists():
        parser.error("Report already exists; choose a new path")
    live = load_script("platform_live", "validate-platform-live.py")
    installer = load_script("late_installer", "install-scp.py")
    live.check_target()
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "before": snapshot(),
              "dsp_firmware_started": False, "npu_module_replaced": False}
    hashes, messages = installer.boot_hashes(), live.kernel_messages()
    failure = None
    try:
        if args.test_probe:
            probe_trial(args.module, report, inspect=args.inspect)
    except Exception as error:
        report["error"] = str(error)
        failure = error
    finally:
        try:
            report["after"] = snapshot()
            report["kernel_messages"] = live.new_kernel_messages(messages)
            report["boot_artifacts_unchanged"] = installer.boot_hashes() == hashes
            if not report["boot_artifacts_unchanged"]:
                raise RuntimeError("Unexpected boot-artifact change")
            if any(item["driver"] != ORIGINAL for item in report["after"]["dsp"].values()):
                raise RuntimeError("Original DSP driver was not restored on all three devices")
            if any("n1x-adsp" in line.lower() and
                   any(word in line.lower() for word in ("failed", "timeout", "error"))
                   for line in report["kernel_messages"]):
                raise RuntimeError("New DSP driver errors in kernel log")
            live.check_kernel_messages(report["kernel_messages"])
        except Exception as error:
            report["postflight_error"] = str(error)
            failure = failure or error
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
        print(args.report, flush=True)
    if failure:
        raise SystemExit(str(failure))


if __name__ == "__main__":
    main()
