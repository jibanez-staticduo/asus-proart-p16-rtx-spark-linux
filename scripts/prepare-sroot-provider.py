#!/usr/bin/env python3
"""Pin a reproducible DKMS source snapshot from the sibling reviewed NPU tree."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
NPU = ROOT.parent / "n1x-lab/npu"
DEST = ROOT / "platform/dsp/provider"


def main():
    paths = subprocess.check_output(["git", "ls-files", "driver", "include"], cwd=NPU, text=True).splitlines()
    paths.append("include/n1x_sroot_client.h")
    files = {}
    for name in sorted(set(paths)):
        path = NPU / name
        if path.suffix in (".c", ".h") or path.name == "Kbuild":
            if path.is_symlink():
                raise RuntimeError("Source symlinks are not accepted: " + name)
            files[name] = path.read_bytes()
    if files["include/n1x_sroot_client.h"] != (ROOT / "platform/dsp/n1x_sroot_client.h").read_bytes():
        raise RuntimeError("Provider/client header mismatch")
    manifest = {"base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=NPU, text=True).strip(),
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                "local_provider_changes_included": True}
    files["snapshot.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    tar_data = io.BytesIO()
    with tarfile.open(fileobj=tar_data, mode="w") as archive:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo("npu-source/" + name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            archive.addfile(info, io.BytesIO(data))
    archive_path = DEST / "npu-source.tar.gz"
    archive_path.write_bytes(gzip.compress(tar_data.getvalue(), mtime=0))
    sources = ("npu-source.tar.gz", "dkms.conf", "n1x-sroot-provider.conf",
               "n1x-platform-npu.service", "n1x-platform-npu-start")
    sums = [hashlib.sha256((DEST / name).read_bytes()).hexdigest() for name in sources]
    template = (DEST / "PKGBUILD.in").read_text()
    (DEST / "PKGBUILD").write_text(template.replace("SOURCE_SHA256", "\n            ".join("'" + s + "'" for s in sums)))
    (DEST / "receipt.json").write_text(json.dumps({"sources": dict(zip(sources, sums)), "snapshot": manifest}, indent=2) + "\n")
    print(DEST / "PKGBUILD")


if __name__ == "__main__":
    main()
