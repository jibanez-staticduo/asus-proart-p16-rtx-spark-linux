#!/usr/bin/env python3
"""Extract pinned OEM platform inputs as data; no Windows code is executed."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VENDOR = ROOT.parent / "n1x-lab/platform/sources/vendor"


def verify(path, spec):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing/nonregular input: {path}")
    if "bytes" in spec and path.stat().st_size != spec["bytes"]:
        raise ValueError(f"Size mismatch: {path}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != spec["sha256"]:
        raise ValueError(f"SHA-256 mismatch: {path}; preserving existing data")
    return digest


def validate_tree(base, entries):
    for spec in entries:
        relative = Path(spec["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe manifest path: {relative}")
        verify(base / relative, spec)


def capture(command, destination):
    with destination.open("xb") as stream:
        subprocess.run(command, stdout=stream, check=True)


def extract(vendor, lock, recovery_spec):
    recovery = vendor / lock["recovery"]
    verify(recovery, recovery_spec)
    destination = vendor / "platform-drivers"
    missing = []
    if destination.exists():
        for spec in lock['files']:
            relative = Path(spec['path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError(f'Unsafe manifest path: {relative}')
            target = destination / relative
            if target.exists() or target.is_symlink():
                verify(target, spec)
            else:
                missing.append(spec)
    else:
        missing = lock['files']
    if missing:
        # Keep all generated paths private until every selected file matches.
        with tempfile.TemporaryDirectory(prefix=".platform-extract-", dir=vendor) as temporary:
            work = Path(temporary)
            inner = work / "inner.exe"
            capture(["7z", "x", "-tPE", "-so", str(recovery), ".rsrc/EXE/102"], inner)
            subprocess.run(["innoextract", "--silent", "--output-dir", str(work / "recovery"),
                            str(inner)], check=True)
            iso = work / "recovery" / lock["iso_path"]
            wim = work / "boot.wim"
            capture(["7z", "x", "-so", str(iso), "sources/boot.wim"], wim)
            staged = work / "drivers"
            packages = sorted({str(Path(spec["path"]).parent) for spec in missing})
            subprocess.run(["wimextract", str(wim), "1", *["/" + p for p in packages],
                            "--dest-dir=" + str(staged), "--preserve-dir-structure", "--no-acls"],
                           check=True)
            validate_tree(staged, missing)
            # Publish only selected, verified files, not unrelated recovery data.
            selected = work / "selected"
            for spec in missing:
                target = selected / spec["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(staged / spec["path"], target)
            if not destination.exists():
                selected.rename(destination)
            else:
                # Publish additions without replacing a previously verified file.
                # Temporary files share the destination filesystem; link creation
                # is atomic and an existing target is revalidated, never replaced.
                for spec in missing:
                    target = destination / spec['path']
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.link(selected / spec['path'], target)
                    except FileExistsError:
                        verify(target, spec)
    validate_tree(destination, lock['files'])
    receipt = {"schema": 1, "recovery_sha256": recovery_spec["sha256"],
               "files": lock["files"], "windows_code_executed": False}
    (vendor / "platform-drivers.receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", type=Path, default=DEFAULT_VENDOR)
    args = parser.parse_args()
    lock = json.loads((ROOT / "config/platform-oem.json").read_text())
    sources = json.loads((ROOT / "config/platform-sources.json").read_text())
    spec = next(item for item in sources["vendor"] if item["name"] == lock["recovery"])
    receipt = extract(args.vendor, lock, spec)
    print(f"Verified {len(receipt['files'])} OEM driver/firmware inputs in {args.vendor / 'platform-drivers'}")


if __name__ == "__main__":
    main()
