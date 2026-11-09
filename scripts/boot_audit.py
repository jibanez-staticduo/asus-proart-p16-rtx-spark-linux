#!/usr/bin/env python3
"""Read-only UKI inventory and late-module placement checks."""

import hashlib
from pathlib import Path
import importlib.util
import re
import subprocess
import tempfile


def late_module_initramfs_audit(modules, label, boot_root=Path('/boot')):
    path = Path(__file__).with_name("verify-boot.py")
    spec = importlib.util.spec_from_file_location("late_boot_verify", path)
    verify = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verify)
    checked = []
    with tempfile.TemporaryDirectory(prefix="p16-provider-initrd-") as directory:
        for image in sorted((boot_root / 'EFI/Linux').glob('*.efi')):
            sections = verify.sections(image)
            release = sections.get(".uname", b"").rstrip(b"\0\n").decode()
            if not re.fullmatch(r'[a-zA-Z0-9._+-]+', release):
                raise RuntimeError('Missing or invalid UKI kernel release: ' + str(image))
            initrd = Path(directory) / "initrd"
            initrd.write_bytes(sections[".initrd"])
            listing = subprocess.check_output(["lsinitcpio", str(initrd)], text=True)
            names = '|'.join(re.escape(name) for name in modules)
            if re.search(r"(?:^|/)(?:" + names + r")\.ko(?:\.(?:zst|xz|gz))?$", listing, re.M):
                raise RuntimeError(label + " is embedded in this UKI; stage an explicit image update before installation: " + str(image))
            checked.append(str(image))
    if not checked:
        raise RuntimeError('No Linux UKIs found for late-provider validation')
    return checked


def npu_initramfs_audit(boot_root=Path('/boot')):
    return late_module_initramfs_audit(('n1x_npu_bootstrap', 'n1x-npu-bootstrap'), 'NPU', boot_root)


def boot_hashes():
    paths = sorted(Path("/boot/EFI/Linux").glob("*.efi"))
    if not paths or not Path("/boot/limine.conf").is_file():
        raise RuntimeError("Expected the managed P16 EFI/Limine boot setup")
    paths.append(Path("/boot/limine.conf"))
    result = {}
    for path in paths:
        with path.open("rb") as stream:
            result[str(path)] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result
