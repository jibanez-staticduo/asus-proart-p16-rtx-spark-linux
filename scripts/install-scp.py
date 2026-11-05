#!/usr/bin/env python3
"""Install a late-loaded platform DKMS package, preserving current boot images."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import importlib.util
import re
import subprocess
import tempfile

PACKAGES = {"n1x-scp-dkms": "n1x_scp", "n1x-pluton-dkms": "n1x_pluton",
            "n1x-adsp-dkms": "n1x_adsp", "n1x-sroot-provider-dkms": "n1x_npu_bootstrap",
            "n1x-adsp-firmware-local": None}


def npu_initramfs_audit():
    path = Path(__file__).with_name("verify-boot.py")
    spec = importlib.util.spec_from_file_location("late_boot_verify", path)
    verify = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verify)
    checked = []
    with tempfile.TemporaryDirectory(prefix="p16-provider-initrd-") as directory:
        for image in sorted(Path("/boot/EFI/Linux").glob("*.efi")):
            sections = verify.sections(image)
            release = sections.get(".uname", b"").rstrip(b"\0\n").decode()
            if release not in ("7.2.8-23-omarchy-p16-asus-npu2", "7.2.8-23-rtx-spark-npu"):
                continue
            initrd = Path(directory) / "initrd"
            initrd.write_bytes(sections[".initrd"])
            listing = subprocess.check_output(["lsinitcpio", str(initrd)], text=True)
            if re.search(r"(?:^|/)(?:n1x_npu_bootstrap|n1x-npu-bootstrap)\.ko(?:\.(?:zst|xz|gz))?$", listing, re.M):
                raise RuntimeError("NPU is embedded in this UKI; stage an explicit image update before provider installation: " + str(image))
            checked.append(str(image))
    if len(checked) != 2:
        raise RuntimeError("Expected both current and RTX NPU UKIs for late-provider validation")
    return checked


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, nargs="+")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run as root (sudo or pkexec)")
    packages = [path.resolve(strict=True) for path in args.package]
    infos = []
    for package in packages:
        info = subprocess.check_output(["pacman", "-Qp", str(package)], text=True).split()
        if len(info) != 2 or info[0] not in PACKAGES:
            parser.error("This installer accepts only the allowlisted late-loaded platform driver/firmware packages")
        infos.append(info)
    if len({info[0] for info in infos}) != len(infos):
        parser.error("Duplicate package name in the transaction")
    before = boot_hashes()
    provider = any(info[0] == "n1x-sroot-provider-dkms" for info in infos)
    audit = npu_initramfs_audit() if provider else None
    with tempfile.TemporaryDirectory(prefix="p16-scp-hooks-") as temporary:
        # These modules are not initramfs dependencies. Skip only the broad image
        # regeneration for this transaction. All configured preflight/DKMS
        # hooks remain active; no installed hook is changed or removed.
        (Path(temporary) / "90-mkinitcpio-install.hook").symlink_to("/dev/null")
        directories = subprocess.check_output(["pacman-conf", "HookDir"], text=True).splitlines()
        command = ["pacman", "-U", "--noconfirm"]
        for directory in directories + [temporary]:
            command.extend(["--hookdir", directory])
        command.extend(map(str, packages))
        transaction = subprocess.run(command)
    after = boot_hashes()
    receipt = {"packages": [" ".join(info) for info in infos], "before": before, "after": after,
               "boot_artifacts_unchanged": before == after, "package_exit": transaction.returncode}
    if audit is not None:
        receipt["npu_not_embedded_in_ukis"] = audit
    if transaction.returncode == 0:
        try:
            receipt["dkms"] = {}
            releases = sorted(path.parent.parent.name for path in Path("/usr/lib/modules").glob("*/build/Makefile"))
            for name, version in infos:
                if not name.endswith("-dkms"):
                    continue
                module, module_version = name.removesuffix("-dkms"), version.rsplit("-", 1)[0]
                status = subprocess.check_output(["dkms", "status", "-m", module], text=True)
                receipt["dkms"][module] = status
                for release in releases:
                    pattern = r"^" + re.escape(module + "/" + module_version + ", " + release) + r", [^:]+: installed(?:\s.*)?$"
                    if not re.search(pattern, status, re.M):
                        raise RuntimeError("DKMS installation not confirmed: " + module + " / " + release)
            if provider:
                subprocess.run(["systemctl", "daemon-reload"], check=True)
                subprocess.run(["systemd-analyze", "verify", "/usr/lib/systemd/system/n1x-platform-npu.service"], check=True,
                               capture_output=True, text=True)
                subprocess.run(["systemctl", "disable", "n1x-npu.service"], check=True,
                               capture_output=True, text=True)
                subprocess.run(["systemctl", "enable", "n1x-platform-npu.service"], check=True,
                               capture_output=True, text=True)
                subprocess.run(["/usr/libexec/n1x-platform-npu-start", "--prepare"], check=True,
                               capture_output=True, text=True)
                receipt["provider_service_verified_without_restart"] = True
                receipt["npu_service"] = "n1x-platform-npu.service"
        except Exception as error:
            receipt["postflight_error"] = str(error)
    if args.report:
        args.report.write_text(json.dumps(receipt, indent=2) + "\n")
    if before != after:
        raise RuntimeError("Boot artifacts changed unexpectedly; inspect the installation report")
    print("Verified: all existing EFI images and Limine configuration are unchanged", flush=True)
    if transaction.returncode:
        raise SystemExit(transaction.returncode)
    if receipt.get("postflight_error"):
        raise SystemExit(receipt["postflight_error"])
    print("Platform packages installed and DKMS builds verified. Loaded driver instances were preserved.", flush=True)


if __name__ == "__main__":
    main()
