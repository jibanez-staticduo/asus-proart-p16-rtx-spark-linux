#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build a pinned native vLLM wheel with Python-only SM12x backports."""
import argparse
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile

BASE = Path(__file__).resolve().parent


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build(directory):
    profile = json.loads((BASE / "build-profile.json").read_text())
    directory.mkdir(parents=True, exist_ok=True)
    name = urllib.parse.unquote(profile["base_wheel_url"].rsplit("/", 1)[1])
    original = directory / name
    if not original.exists():
        partial = original.with_suffix(".partial")
        try:
            with urllib.request.urlopen(profile["base_wheel_url"], timeout=60) as response, partial.open("wb") as output:
                while chunk := response.read(8 * 1024 * 1024):
                    output.write(chunk)
            if sha256(partial) != profile["base_wheel_sha256"]:
                raise ValueError("Upstream wheel SHA-256 differs from pin")
            partial.replace(original)
        finally:
            partial.unlink(missing_ok=True)
    if sha256(original) != profile["base_wheel_sha256"]:
        raise ValueError("Cached upstream wheel SHA-256 differs from pin")
    patches = []
    for patch in profile["patches"]:
        path = BASE / patch["path"]
        if sha256(path) != patch["sha256"]:
            raise ValueError(f"Patch SHA-256 differs from pin: {path}")
        patches.append(path)
    destination = directory / name.replace(profile["base_version"], profile["version"])
    with tempfile.TemporaryDirectory(prefix="vllm-wheel-", dir=directory) as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(original) as archive:
            archive.extractall(root)
        for path in patches:
            subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "-d", str(root), "-i", str(path)], check=True)
        old_info, = root.glob("vllm-*.dist-info")
        info = old_info.rename(root / f"vllm-{profile['version']}.dist-info")
        metadata = info / "METADATA"
        metadata.write_text(re.sub(r"(?m)^Version: .*?$", f"Version: {profile['version']}", metadata.read_text()))
        version = root / "vllm/_version.py"
        version.write_text(version.read_text().replace(profile["base_version"], profile["version"]).replace(
            repr(profile["base_version"].split("+", 1)[1]) + ")",
            repr(profile["version"].split("+", 1)[1]) + ")"))
        (root / "vllm/_p16_build.json").write_text(json.dumps(profile, sort_keys=True, indent=2) + "\n")
        record = info / "RECORD"
        rows = []
        wheel = root / destination.name
        with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path in (record, wheel):
                    continue
                relative = path.relative_to(root).as_posix()
                data = path.read_bytes()
                entry = zipfile.ZipInfo(relative, date_time=(2026, 11, 8, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = (0o100644 << 16)
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, data)
                digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                rows.append((relative, "sha256=" + digest, str(len(data))))
            rows.append((record.relative_to(root).as_posix(), "", ""))
            buffer = io.StringIO(newline="")
            csv.writer(buffer).writerows(rows)
            entry = zipfile.ZipInfo(rows[-1][0], date_time=(2026, 11, 8, 0, 0, 0))
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, buffer.getvalue())
        wheel.replace(destination)
    print(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    build(parser.parse_args().directory)
