#!/usr/bin/env python3
"""Capture N1X enablement evidence without probing or changing hardware registers."""

import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import struct


TABLES = ("DSDT", "SSDT1", "SSDT2", "SSDT3", "SSDT4", "SSDT5", "TPM2", "IORT", "SDEV")
TARGETS = {
    "scp": ("NVDA8200",),
    "pluton": ("NVDA8601",),
    "dsp": ("NVDA8301", "NVDA8302", "NVDA8303"),
    "camera": ("NVDA2801", "NVDA2802", "NVDA2803", "NVDA2820"),
    "sensorhub": ("MSFW1111",),
    "telemetry": ("NVDA8800", "NVDA8810"),
    "platform_control": ("NVDA0820", "NVDA6210", "NVDA8400"),
}


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def driver(path):
    p = path / "driver"
    return p.resolve().name if p.is_symlink() else None


def acpi_table(path):
    data = path.read_bytes()
    if len(data) < 36:
        raise ValueError(f"Short ACPI table: {path}")
    length = struct.unpack_from("<I", data, 4)[0]
    if length != len(data) or sum(data) % 256:
        raise ValueError(f"Invalid ACPI length/checksum: {path}")
    out = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
           "base64": base64.b64encode(data).decode()}
    if path.name == "TPM2" and len(data) >= 52:
        out["control_address"] = hex(struct.unpack_from("<Q", data, 40)[0])
        out["start_method"] = struct.unpack_from("<I", data, 48)[0]
    return out


def iio_devices(directory=Path("/sys/bus/iio/devices")):
    # IIO is a bus, not a /sys/class/iio class. Do not include trigger devices
    # or read raw channels: those reads can initiate device transactions.
    return [{"id": p.name, "path": str(p.resolve()),
             "name": read(p / "name"), "state": read(p / "state")}
            for p in sorted(directory.glob("iio:device*"))]


def collect(capture_acpi=False):
    result = {"schema": 1, "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "kernel": platform.release(), "architecture": platform.machine(),
              "device": {}, "targets": {}, "remoteproc": [], "tpm": [], "video": [],
              "hwmon": [], "iio": [], "acpi_tables": {}}
    for name in ("sys_vendor", "product_name", "board_name", "bios_version"):
        result["device"][name] = read(Path("/sys/class/dmi/id") / name)
    for kind, hids in TARGETS.items():
        devices = []
        for hid in hids:
            for p in sorted(Path("/sys/bus/acpi/devices").glob(hid + ":*")):
                nodes = []
                for n in sorted(p.glob("physical_node*")):
                    q = n.resolve()
                    nodes.append({"path": str(q), "driver": driver(q),
                                  "modalias": read(q / "modalias")})
                devices.append({"id": p.name, "path": read(p / "path"),
                                "status": read(p / "status"),
                                "acpi_driver": driver(p), "physical_nodes": nodes})
        result["targets"][kind] = devices
    for kind, directory in (("remoteproc", "remoteproc"), ("tpm", "tpm"),
                            ("video", "video4linux")):
        for p in sorted((Path("/sys/class") / directory).glob("*")):
            result[kind].append({"id": p.name, "path": str(p.resolve()),
                                 "name": read(p / "name"), "state": read(p / "state")})
    result["iio"] = iio_devices()
    for p in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        if read(p / "name") != "spbm_n1x":
            continue
        readings = {}
        for label in sorted(p.glob("*_label")):
            stem = label.name.removesuffix("_label")
            readings[stem] = {"label": read(label), "input": read(p / (stem + "_input")),
                              "cap": read(p / (stem + "_cap"))}
        result["hwmon"].append({"name": "spbm_n1x", "path": str(p.resolve()), "readings": readings})
    if capture_acpi:
        for name in TABLES:
            p = Path("/sys/firmware/acpi/tables") / name
            if p.exists():
                result["acpi_tables"][name] = acpi_table(p)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-acpi", action="store_true", help="Requires root; excludes the MSDM product-key table")
    args = parser.parse_args()
    if args.capture_acpi and os.geteuid() != 0:
        parser.error("--capture-acpi requires root")
    print(json.dumps(collect(args.capture_acpi), indent=2))


if __name__ == "__main__":
    main()
