#!/usr/bin/env python3
"""Install the P16 sensor-hub DKMS fix and update current/default UKIs.

Stages UKIs on the root filesystem, preserves the base P16 recovery image,
backs up replaced artifacts, and verifies the embedded DKMS payloads.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import lzma
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
BOOT = Path("/boot")
MENU = BOOT / "limine.conf"
RECOVERY = BOOT / "EFI/Linux/omarchy_linux-p16-asus-23.efi"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify = load("sensor_boot_verify", "verify-boot.py")
live = load("sensor_live_verify", "validate-platform-live.py")
late = load("sensor_late_install", "install-scp.py")


def digest(path, algorithm="sha256"):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def uki_entry(menu, package):
    match = re.search(r"^\s*//" + re.escape(package) + r"\s*\n(.*?)(?=^\s*//|\Z)", menu, re.M | re.S)
    if not match:
        raise ValueError(f"Missing unique UKI entry: {package}")
    block = match.group(1)
    paths = re.findall(r"^\s*path:\s*boot\(\):(/EFI/Linux/[^\s#]+\.efi)#([0-9a-fA-F]{128})\s*$", block, re.M)
    commands = re.findall(r"^\s*cmdline:\s*(.+)$", block, re.M)
    if len(paths) != 1 or len(commands) != 1:
        raise ValueError(f"Ambiguous UKI entry: {package}")
    path, pin = paths[0]
    if path != f"/EFI/Linux/omarchy_{package}.efi":
        raise ValueError(f"Unexpected UKI path for {package}")
    return path, pin.lower(), commands[0].strip()


def replace_pin(menu, relative, old, new):
    token = f"boot():{relative}#{old}"
    if menu.count(token) != 1:
        raise ValueError("UKI hash token missing or duplicated; preserving menu")
    return menu.replace(token, f"boot():{relative}#{new}", 1)


def installed_for(status, release):
    return any(re.search(r", " + re.escape(release) + r", [^:\n]+: installed(?: \([^\n]*\))?$", line)
               for line in status.splitlines())


def original_module(name, release, modules=Path("/usr/lib/modules"),
                    backups=Path("/var/lib/dkms/n1x-sensorhub/original_module"), arch=None):
    directory = modules / release / "kernel/drivers/hid/spi-hid"
    expected = [directory / (name + suffix) for suffix in (".ko", ".ko.zst", ".ko.xz", ".ko.gz")]
    choices = [p for p in expected if p.is_file()]
    if not choices:
        # DKMS moves in-tree originals to this cache when installing an
        # override. Its .origin sidecar must point to this exact kernel path.
        cache = backups / release / (arch or os.uname().machine)
        for target in expected:
            candidate = cache / target.name
            origin = Path(str(candidate) + ".origin")
            if candidate.is_file() and origin.is_file() and Path(origin.read_text().strip()).resolve() == target.resolve():
                choices.append(candidate)
    if len(choices) != 1:
        raise RuntimeError(f"Expected one original kernel module for rollback: {name}")
    return choices[0]


def atomic_copy(source, target):
    """Copy/verify on the target filesystem before replacing its old file."""
    source, target = Path(source), Path(target)
    descriptor, temporary = tempfile.mkstemp(prefix=".p16-sensorhub-", dir=target.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if digest(source) != digest(temporary):
            raise RuntimeError("Copy verification failed before EFI replacement")
        os.chmod(temporary, target.stat().st_mode & 0o777)
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_menu(text):
    descriptor, temporary = tempfile.mkstemp(prefix=".p16-sensorhub-menu-", dir=MENU.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, MENU.stat().st_mode & 0o777)
        os.replace(temporary, MENU)
        os.sync()
    finally:
        temporary.unlink(missing_ok=True)


def module_data(path):
    path = Path(path)
    if path.suffix == ".zst":
        return subprocess.check_output(["zstd", "-dq", "-c", str(path)])
    if path.suffix == ".xz":
        return lzma.decompress(path.read_bytes())
    if path.suffix == ".gz":
        import gzip
        return gzip.decompress(path.read_bytes())
    return path.read_bytes()


def embedded_modules(image, release, stage, execute):
    initrd = stage / (release + ".initrd")
    initrd.write_bytes(verify.sections(image)[".initrd"])
    extraction = stage / (release + "-extract")
    extraction.mkdir()
    result = {}
    try:
        execute(["lsinitcpio", "-x", initrd], cwd=extraction)
        for name in ("spi-hid", "spi-hid-acpi"):
            installed = Path(live.command(["modinfo", "-k", release, "-n", name]).strip())
            if "/updates/dkms/" not in str(installed):
                raise RuntimeError(f"DKMS override is not selected: {installed}")
            copies = [p for p in extraction.rglob(name + ".ko*") if p.is_file() and release in p.parts]
            if len(copies) != 1 or "/updates/dkms/" not in str(copies[0]):
                raise RuntimeError(f"UKI does not contain exactly one DKMS {name}")
            expected = hashlib.sha256(module_data(installed)).hexdigest()
            if hashlib.sha256(module_data(copies[0])).hexdigest() != expected:
                raise RuntimeError(f"UKI module differs from the installed DKMS build: {name}")
            result[name] = {"installed": str(installed), "uncompressed_sha256": expected}
    finally:
        initrd.unlink(missing_ok=True)
        shutil.rmtree(extraction)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid():
        parser.error("Run as root")
    live.check_target()
    if args.report.exists():
        parser.error("Report exists; choose a new report path")
    package = args.package.resolve(strict=True)
    info = live.command(["pacman", "-Qp", package]).split()
    if len(info) != 2 or info[0] != "n1x-sensorhub-dkms":
        parser.error("Expected the n1x-sensorhub-dkms package")
    original = MENU.read_text()
    defaults = re.findall(r"^default_entry:\s*Omarchy/(\S+)\s*$", original, re.M)
    releases = {p: r for r, p in verify.QUALIFIED_KERNELS.items()}
    current = verify.QUALIFIED_KERNELS.get(os.uname().release)
    if len(defaults) != 1 or defaults[0] not in releases or current is None:
        parser.error("Unvalidated current/default kernel")
    targets = list(dict.fromkeys([current, defaults[0]]))
    entries = {p: uki_entry(original, p) for p in targets}
    if any(BOOT / v[0].lstrip("/") == RECOVERY for v in entries.values()):
        parser.error("Current/default selection would replace the base P16 recovery image")
    verify.verify_menu()
    verify.verify_image(RECOVERY)
    live.check_sensor_bindings()
    before = late.boot_hashes()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path("/var/lib/p16-platform/boot-hook-backup") / ("sensorhub-" + timestamp)
    backup.mkdir(mode=0o700, parents=True)
    stage = backup / "stage"
    stage.mkdir()
    (backup / "limine.conf").write_text(original)
    rollback_modules = backup / "rollback-modules"
    rollback_modules.mkdir()
    original_modules = {}
    for name in ("spi-hid", "spi-hid-acpi"):
        # modinfo selects the override after an earlier successful package
        # install. Always preserve the kernel package's original driver.
        source = original_module(name, os.uname().release)
        live.check_module(source)
        original_modules[name] = rollback_modules / source.name
        shutil.copy2(source, original_modules[name])
    for relative, _, _ in entries.values():
        source = BOOT / relative.lstrip("/")
        shutil.copy2(source, backup / source.name)
        if digest(backup / source.name) != before[str(source)]:
            raise RuntimeError("Boot backup verification failed")
    report = {"package": " ".join(info), "backup": str(backup), "before": before,
              "targets": {}, "reboot_verified": False}
    deployed = []
    failed = None
    live_changed = core_removed = False
    with (backup / "install.log").open("w") as log:
        def execute(command, cwd=None):
            command = [str(x) for x in command]
            log.write("$ " + shlex.join(command) + "\n")
            log.flush()
            result = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=600)
            log.flush()
            if result.returncode:
                raise RuntimeError(f"Command failed ({result.returncode}): {shlex.join(command)}; see {backup / 'install.log'}")
        try:
            # Prevent the broad pacman hook from rebuilding all experimental
            # images. Preserve preflight/DKMS hooks, then build explicit UKIs.
            installed = subprocess.run(["pacman", "-Q", info[0]], capture_output=True, text=True)
            report["package_preexisting"] = installed.returncode == 0 and installed.stdout.split() == info
            if not report["package_preexisting"]:
                with tempfile.TemporaryDirectory(prefix="p16-sensorhub-hooks-") as hooks:
                    (Path(hooks) / "90-mkinitcpio-install.hook").symlink_to("/dev/null")
                    dirs = live.command(["pacman-conf", "HookDir"]).splitlines()
                    command = ["pacman", "-U", "--noconfirm"]
                    for directory in dirs + [hooks]:
                        command += ["--hookdir", directory]
                    execute(command + [package])
            if late.boot_hashes() != before:
                raise RuntimeError("An unexpected hook changed boot artifacts before explicit staging")
            kernels = sorted(p.parent.name for p in Path("/usr/lib/modules").glob("*/modules.builtin"))
            status = live.command(["dkms", "status", "-m", "n1x-sensorhub"])
            report["dkms_status"] = status
            for release in kernels:
                if not installed_for(status, release):
                    raise RuntimeError(f"Sensor DKMS build is not installed for {release}")
            for name, (relative, _, cmdline) in entries.items():
                release = releases[name]
                image = stage / (name + ".efi")
                command_file = stage / (name + ".cmdline")
                command_file.write_text(cmdline + "\n")
                print("Staging sensor-hub UKI:", release, flush=True)
                execute(["mkinitcpio", "--kernel", release, "--cmdline", command_file, "--uki", image])
                verify.verify_image(image)
                report["targets"][name] = {"release": release, "staged": str(image),
                                          "sha256": digest(image), "blake2b": digest(image, "blake2b"),
                                          "modules": embedded_modules(image, release, stage, execute)}
            images = [stage / (p + ".efi") for p in targets]
            growth = max(0, sum(p.stat().st_size - (BOOT / entries[n][0].lstrip("/")).stat().st_size
                                for n, p in zip(targets, images)))
            free = os.statvfs(BOOT)
            if free.f_bavail * free.f_frsize < max(p.stat().st_size for p in images) + growth + 32 * 1024 * 1024:
                raise RuntimeError("Not enough ESP space for verified atomic deployment; staged images preserved")
            with Path("/run/lock/boot-partition.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if late.boot_hashes() != before:
                    raise RuntimeError("Boot artifacts changed concurrently; refusing to deploy")
                updated = original
                try:
                    for name, (relative, pin, _) in entries.items():
                        target = BOOT / relative.lstrip("/")
                        deployed.append(target)
                        atomic_copy(stage / (name + ".efi"), target)
                        updated = replace_pin(updated, relative, pin, report["targets"][name]["blake2b"])
                        atomic_menu(updated)
                    verify.verify_menu()
                except BaseException:
                    for target in reversed(deployed):
                        atomic_copy(backup / target.name, target)
                    atomic_menu(original)
                    deployed.clear()
                    raise
            messages = live.kernel_messages()
            # Load installed DKMS modules now. The installed original binaries
            # remain copied above for rollback if the live check fails.
            execute(["rmmod", "spi_hid_acpi"])
            live_changed = True
            execute(["rmmod", "spi_hid"])
            core_removed = True
            execute(["modprobe", "spi_hid_acpi"])
            deadline = time.monotonic() + 3
            while True:
                devices = [p for p in Path("/sys/bus/iio/devices").glob("iio:device*")
                           if (p / "name").read_text().strip() == "als" and "spi" in str(p.resolve())]
                if len(devices) == 1:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("Installed sensor-hub driver did not enumerate ALS")
                time.sleep(0.1)
            samples = []
            for _ in range(5):
                samples.append(live.command([sys.executable, "-c",
                    "import sys;from pathlib import Path;print(Path(sys.argv[1]).read_text().strip())",
                    devices[0] / "in_illuminance_raw"], timeout=8).strip())
                live.check_kernel_messages(live.new_kernel_messages(messages))
            report["sensor"] = {"path": str(devices[0].resolve()), "raw_samples": samples,
                                "scale": (devices[0] / "in_illuminance_scale").read_text().strip()}
            report["kernel_messages"] = live.new_kernel_messages(messages)
            live.check_kernel_messages(report["kernel_messages"])
            report["after"] = late.boot_hashes()
            for path, pin in before.items():
                if path != str(MENU) and Path(path) not in deployed and report["after"][path] != pin:
                    raise RuntimeError(f"Recovery/other EFI image changed unexpectedly: {path}")
            report["recovery_unchanged"] = report["after"][str(RECOVERY)] == before[str(RECOVERY)]
            report["installed_and_live_validated"] = True
            try:
                execute(["modprobe", "n1x_pluton"])
                report["pluton"] = json.loads(live.command(["n1x-pluton-info", "--discover"], timeout=13))
            except Exception as error:
                report["pluton_error"] = str(error)
        except Exception as error:
            failed = error
            report["error"] = str(error)
            if deployed:
                with Path("/run/lock/boot-partition.lock").open("a") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    for target in reversed(deployed):
                        atomic_copy(backup / target.name, target)
                    atomic_menu(original)
                report["boot_rollback"] = late.boot_hashes() == before
            if live_changed:
                try:
                    if Path("/sys/module/spi_hid_acpi").exists():
                        execute(["rmmod", "spi_hid_acpi"])
                    if core_removed:
                        if Path("/sys/module/spi_hid").exists():
                            execute(["rmmod", "spi_hid"])
                        execute(["insmod", original_modules["spi-hid"]])
                    execute(["insmod", original_modules["spi-hid-acpi"]])
                    report["live_rollback"] = True
                except Exception as rollback_error:
                    report["live_rollback_error"] = str(rollback_error)
            report["installed_and_live_validated"] = False
        finally:
            log.flush()
            report["log_tail"] = (backup / "install.log").read_text(errors="replace")[-20000:]
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x") as stream:
                json.dump(report, stream, indent=2)
                stream.write("\n")
    print(args.report, flush=True)
    if failed:
        raise SystemExit(str(failed))


if __name__ == "__main__":
    main()
