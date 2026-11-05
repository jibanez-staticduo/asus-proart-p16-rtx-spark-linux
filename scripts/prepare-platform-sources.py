#!/usr/bin/env python3
"""Fetch pinned reference drivers; vendor installers are verified data, never executed."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = ROOT.parent / "n1x-lab/platform/sources"


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def prepare_repo(dest, spec):
    path = dest / "upstream" / spec["name"]
    if path.exists():
        if git(path, "rev-parse", "HEAD") != spec["commit"]:
            raise RuntimeError(f"Unexpected revision at {path}; preserving the existing checkout")
        if git(path, "status", "--porcelain"):
            raise RuntimeError(f"Modified source at {path}; preserving the existing checkout")
        if git(path, "remote", "get-url", "origin") != spec["url"]:
            raise RuntimeError(f"Unexpected source origin at {path}")
        git(path, "sparse-checkout", "set", *spec["paths"])
    else:
        path.mkdir(parents=True)
        git(path, "init")
        git(path, "remote", "add", "origin", spec["url"])
        git(path, "config", "remote.origin.promisor", "true")
        git(path, "config", "remote.origin.partialclonefilter", "blob:none")
        git(path, "fetch", "--depth=1", "--filter=blob:none", "origin", spec["commit"])
        git(path, "sparse-checkout", "init", "--cone")
        git(path, "sparse-checkout", "set", *spec["paths"])
        git(path, "checkout", "--detach", spec["commit"])
    missing = [p for p in spec["paths"] if not (path / p).exists()]
    if missing:
        raise RuntimeError(f"Missing source paths in {path}: {missing}")
    return {"name": spec["name"], "path": str(path), "commit": git(path, "rev-parse", "HEAD"),
            "origin": git(path, "remote", "get-url", "origin")}


def prepare_vendor(dest, spec, fetch):
    path = dest / "vendor" / spec["name"]
    if not path.exists():
        if not fetch:
            return {"name": spec["name"], "status": "not downloaded"}
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".partial")
        with urllib.request.urlopen(spec["url"], timeout=60) as response, partial.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        candidate = partial
    else:
        candidate = path
    with candidate.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != spec["sha256"]:
        raise RuntimeError(f"Checksum mismatch: {candidate}")
    if candidate != path:
        candidate.rename(path)
    return {"name": spec["name"], "path": str(path), "bytes": path.stat().st_size,
            "sha256": digest, "status": "verified"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--fetch-vendor", action="store_true")
    args = parser.parse_args()
    lock = json.loads((ROOT / "config/platform-sources.json").read_text())
    receipt = {"schema": 1, "git": [], "vendor": []}
    for spec in lock["git"]:
        receipt["git"].append(prepare_repo(args.dest, spec))
        print(f"Verified source: {spec['name']}", flush=True)
    for spec in lock["vendor"]:
        receipt["vendor"].append(prepare_vendor(args.dest, spec, args.fetch_vendor))
        print(f"Vendor input: {spec['name']} ({receipt['vendor'][-1]['status']})", flush=True)
    args.dest.mkdir(parents=True, exist_ok=True)
    (args.dest / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main()
