#!/usr/bin/env python3
"""Audit installed P16 platform drivers and perform bounded existing queries.

No DSP firmware boot, driver replacement, EFI or calibration-variable writes.
The optional service-start flag starts the already-enabled NPU service.
The explicit SCP-refresh flag replaces only the known older logging build;
it refuses a failed/poisoned mailbox and never resets platform firmware.
Use --after-boot-id to require evidence from a different boot.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("n1x-scp-dkms", "n1x-pluton-dkms", "n1x-sensorhub-dkms", "n1x-adsp-dkms",
            "n1x-sroot-provider-dkms", "n1x-adsp-firmware-local", "n1x-sroot-firmware-local")


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def run(args, timeout=30):
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Command failed: " + " ".join(map(str, args)) + "\n" +
                           (result.stdout + result.stderr)[-20000:])
    return result.stdout


def refresh_scp(report, modules=Path("/sys/module"), debug=Path("/sys/kernel/debug/n1x_scp")):
    loaded = (modules / "n1x_scp/srcversion").read_text().strip()
    installed = run(["modinfo", "-F", "srcversion", "n1x_scp"]).strip()
    report["scp_refresh"] = {"before": loaded, "installed": installed, "reloaded": False}
    if loaded == installed:
        return
    if (loaded, installed) != ("DDC3B43D4121E886E058EF7", "D952AC3E19ADB9131CCA01F"):
        raise RuntimeError("Unknown SCP build transition; no reload attempted")
    if (modules / "n1x_scp/refcnt").read_text().strip() != "0" or \
            "mailbox_queries=enabled" not in (debug / "status").read_text().splitlines():
        raise RuntimeError("SCP is busy or its mailbox failed; no reload attempted")
    if "/updates/dkms/" not in run(["modinfo", "-n", "n1x_scp"]).strip():
        raise RuntimeError("Expected installed SCP DKMS build")
    run(["rmmod", "n1x_scp"])
    # The SCP module's removal only releases debugfs/MMIO mappings. The
    # platform-owned firmware keeps running; SPI HID has no consumer link.
    run(["modprobe", "n1x_scp"])
    if (modules / "n1x_scp/srcversion").read_text().strip() != installed:
        raise RuntimeError("SCP refresh did not load the installed build")
    report["scp_refresh"]["reloaded"] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--after-boot-id")
    parser.add_argument("--start-npu-service", action="store_true")
    parser.add_argument("--refresh-scp", action="store_true")
    args = parser.parse_args()
    if os.geteuid():
        parser.error("Run as root via sudo or pkexec")
    if args.report.exists():
        parser.error("Choose a new report path")
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if args.after_boot_id == boot_id:
        parser.error("The requested reboot has not happened; no reboot verification claimed")
    live = load("audit_live", "validate-platform-live.py")
    provider = load("audit_provider", "validate-sroot-provider.py")
    late = load("audit_late", "install-scp.py")
    verify = load("audit_boot", "verify-boot.py")
    firmware = load("audit_firmware", "prepare-dsp-firmware.py")
    live.check_target()
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "kernel": os.uname().release,
              "boot_id": boot_id, "reboot_verified": bool(args.after_boot_id),
              "dsp_service_integration": False, "pluton_pcbi_service_integration": False,
              "sensor_calibration_accuracy": "unverified"}
    hashes, messages = late.boot_hashes(), live.kernel_messages()
    failure = None
    try:
        report["packages"] = run(["pacman", "-Q", *PACKAGES]).splitlines()
        report["dkms"] = run(["dkms", "status"])
        service = "n1x-platform-npu.service" if Path("/usr/lib/systemd/system/n1x-platform-npu.service").exists() else "n1x-npu.service"
        report["npu_service_name"] = service
        if args.start_npu_service:
            run(["systemctl", "start", service], timeout=60)
        if args.refresh_scp:
            refresh_scp(report)
        report["npu_service"] = run(["systemctl", "show", service,
                                     "-p", "ActiveState", "-p", "Result", "-p", "ConditionResult"])
        if "ActiveState=active" not in report["npu_service"] or "Result=success" not in report["npu_service"]:
            raise RuntimeError("NPU boot service did not start successfully")
        report["npu"] = provider.snapshot()
        if report["npu"]["status"].get("task_verified") != "1" or \
                report["npu"]["status"].get("queue_poisoned") != "0":
            raise RuntimeError("NPU startup was not healthy")
        report["module_versions"] = {}
        for name in ("n1x_scp", "n1x_pluton", "spi_hid", "spi_hid_acpi", "n1x_npu_bootstrap"):
            path = Path("/sys/module") / name
            installed = run(["modinfo", "-n", name]).strip()
            built = run(["modinfo", "-F", "srcversion", name]).strip()
            loaded = (path / "srcversion").read_text().strip()
            if loaded != built or "/updates/dkms/" not in installed:
                raise RuntimeError("Loaded/installed DKMS module differs: " + name)
            report["module_versions"][name] = {"path": installed, "srcversion": loaded}
        report["scp_version"] = Path("/sys/kernel/debug/n1x_scp/firmware_version").read_text().strip()
        report["pluton"] = json.loads(run(["n1x-pluton-info", "--discover"], timeout=15))
        devices = [p for p in Path("/sys/bus/iio/devices").glob("iio:device*") if
                   (p / "name").read_text().strip() == "als" and "spi" in str(p.resolve())]
        if len(devices) != 1:
            raise RuntimeError("Exactly one SPI ALS device did not enumerate")
        sensor = devices[0]
        scale = float((sensor / "in_illuminance_scale").read_text().strip())
        raw = []
        for _ in range(5):
            value = run(["/usr/bin/python", "-c",
                         "from pathlib import Path;import sys;print(Path(sys.argv[1]).read_text().strip())",
                         sensor / "in_illuminance_raw"], timeout=8).strip()
            raw.append(int(value))
        report["als"] = {"path": str(sensor.resolve()), "raw": raw, "scale": scale,
                          "lux": [value * scale for value in raw]}
        report["firmware"] = {}
        for name, expected in firmware.IMAGES.values():
            path = Path("/usr/lib/firmware/nvidia/n1x") / name
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
                raise RuntimeError("Installed DSP firmware differs: " + name)
            report["firmware"][name] = actual
        report["dsp"] = {}
        for name in ("NVDA8301:00", "NVDA8302:00", "NVDA8303:00"):
            device = Path("/sys/bus/platform/devices") / name
            report["dsp"][name] = {"driver": (device / "driver").resolve().name,
                                   "runtime": (device / "power/runtime_status").read_text().strip()}
            if (device / "status").exists():
                report["dsp"][name]["status"] = (device / "status").read_text().strip()
        report["default_entry"] = next(line for line in Path("/boot/limine.conf").read_text().splitlines()
                                        if line.startswith("default_entry:"))
        verify.verify_menu()
        report["limine_hashes_verified"] = True
    except Exception as error:
        report["error"] = str(error)
        failure = error
    finally:
        report["kernel_messages"] = live.new_kernel_messages(messages)
        report["boot_artifacts_unchanged"] = hashes == late.boot_hashes()
        try:
            live.check_kernel_messages(report["kernel_messages"])
            if not report["boot_artifacts_unchanged"]:
                raise RuntimeError("Unexpected boot-artifact change")
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
