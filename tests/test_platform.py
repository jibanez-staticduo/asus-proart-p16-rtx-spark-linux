"""Reject corrupt firmware inputs and malformed ACPI before extraction/install."""
import hashlib
import importlib.util
import importlib.machinery
import json
from pathlib import Path
import struct
import subprocess
import ast
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_loader(name, importlib.machinery.SourceFileLoader(name, str(path)))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


inventory = module("platform_inventory", "platform-inventory.py")
extractor = module("platform_extractor", "extract-platform-drivers.py")
dsp = module("dsp_firmware", "prepare-dsp-firmware.py")
live = module("platform_live", "validate-platform-live.py")
sensor_install = module("sensor_install", "install-sensorhub.py")
dsp_live = module("dsp_live", "validate-dsp-live.py")
provider_live = module("provider_live", "validate-sroot-provider.py")
dsp_boot = module("dsp_boot", "validate-dsp-boot.py")
provider_start = module("provider_start", "../platform/dsp/provider/n1x-platform-npu-start")
platform_audit = module("platform_audit", "verify-platform-install.py")


class DSPSignedContainerTests(unittest.TestCase):
    @staticmethod
    def cbor(value):
        def head(major, size):
            if size < 24:
                return bytes([(major << 5) | size])
            for additional, width in ((24, 1), (25, 2), (26, 4), (27, 8)):
                if size < 1 << (width * 8):
                    return bytes([(major << 5) | additional]) + size.to_bytes(width, "big")
            raise ValueError("Fixture integer too large")
        encode = DSPSignedContainerTests.cbor
        if isinstance(value, int):
            return head(0 if value >= 0 else 1, value if value >= 0 else -1 - value)
        if isinstance(value, bytes):
            return head(2, len(value)) + value
        if isinstance(value, list):
            return head(4, len(value)) + b"".join(encode(v) for v in value)
        if isinstance(value, dict):
            return head(5, len(value)) + b"".join(encode(k) + encode(v) for k, v in value.items())
        if isinstance(value, tuple):
            return head(6, value[0]) + encode(value[1])
        raise TypeError(value)

    def image(self):
        # Synthetic hashes and fake signatures exercise parsing only; they are
        # deliberately not accepted as pinned OEM firmware by prepare().
        native = bytearray(208)
        struct.pack_into("<III", native, 0, 1, 6, 0xefbeadde)
        struct.pack_into("<I", native, 204, 0xefbeadde)
        offset = 0
        for i, section in enumerate((0, 0x1000, 0x2100, 0x2200, 0x2300, 0x2000)):
            header, payload = (0, 208) if i == 0 else (208, 4)
            struct.pack_into("<8I", native, 12 + 32 * i,
                             0xefbeadde, 0xc0304, section, offset, header, payload, 0, 0)
            offset += header + payload
        native.extend(bytes(offset - len(native)))
        digests = []
        for i in range(6):
            _, _, _, offset, header, payload, _, _ = struct.unpack_from("<8I", native, 12 + 32 * i)
            digests.append([-43, hashlib.sha384(native[offset:offset + header + payload]).digest()])
        bodies = []
        for identity, protected_size, signature_size in ((1, 133, 96), (2, 423, 384)):
            metadata = {-1277952: {0: 1, 1: identity, 2: 1010580826,
                                  3: 0, 4: 0, 5: [0] * 6},
                        -1294334: digests, -1310719: 208}
            bodies.append([[(98, [b"", {}, self.cbor(metadata),
                                  [[bytes(protected_size), {}, bytes(signature_size)]]])]])
        prefix = self.cbor(bodies + [bytes(11)])
        self.assertEqual(len(prefix), 1805)
        return b"\x59\x07\x0d" + prefix + native

    def test_signed_container_hashes_are_not_reported_as_authentication(self):
        result = dsp.inspect_image(self.image(), 0xc0304)
        self.assertEqual(len(result["sections"]), 6)
        self.assertTrue(result["section_digests_verified"])
        self.assertFalse(result["signatures_authenticated"])

    def test_corruption_truncation_wrong_core_and_trailing_data_are_rejected(self):
        image = self.image()
        corrupt = bytearray(image)
        corrupt[-1] ^= 1
        for data, image_id in ((image[:-1], 0xc0304), (image + b"x", 0xc0304),
                               (corrupt, 0xc0304), (image, 0xc0305)):
            with self.subTest(size=len(data), image_id=image_id):
                with self.assertRaises(ValueError):
                    dsp.inspect_image(data, image_id)

    def test_cbor_limits_duplicates_and_indefinite_lengths_are_rejected(self):
        for data in (b"\x9f", b"\x5b" + b"\xff" * 8,
                     b"\x81" * 14 + b"\x00", b"\xa2\x00\x01\x00\x02"):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    dsp.CBOR(data).complete()


class SCPRefreshTests(unittest.TestCase):
    def test_busy_or_failed_mailbox_never_reloads(self):
        for refcnt, queries in (("1", "enabled"), ("0", "failed"), ("0", "disabled")):
            with self.subTest(refcnt=refcnt, queries=queries), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "n1x_scp").mkdir()
                (root / "n1x_scp/srcversion").write_text("DDC3B43D4121E886E058EF7")
                (root / "n1x_scp/refcnt").write_text(refcnt)
                debug = root / "debug"
                debug.mkdir()
                (debug / "status").write_text("mailbox_queries=" + queries + "\n")
                with patch.object(platform_audit, "run", return_value="D952AC3E19ADB9131CCA01F\n") as run:
                    with self.assertRaisesRegex(RuntimeError, "busy|failed"):
                        platform_audit.refresh_scp({}, root, debug)
                    run.assert_called_once_with(["modinfo", "-F", "srcversion", "n1x_scp"])


class ProviderBootServiceTests(unittest.TestCase):
    def layout(self, root):
        controls = []
        for index, (name, supplier) in enumerate(provider_start.EXPECTED_IOMMU.items()):
            device = root / "bus/platform/devices" / name
            device.mkdir(parents=True)
            iommu = root / supplier
            iommu.mkdir()
            (device / "iommu").symlink_to(iommu)
            group = root / "kernel/iommu_groups" / str(index)
            (group / "devices").mkdir(parents=True)
            (group / "devices" / name).symlink_to(device)
            (device / "iommu_group").symlink_to(group)
            (group / "type").write_text("identity\n")
            controls.append(group / "type")
        return controls

    def test_dma_domains_are_selected_only_after_both_groups_validate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controls = self.layout(root)
            extra = controls[1].parent / "devices/another-device"
            extra.symlink_to(root / "another-device")
            with self.assertRaisesRegex(RuntimeError, "shared"):
                provider_start.prepare(root)
            self.assertTrue(all(p.read_text().strip() == "identity" for p in controls))
            extra.unlink()
            provider_start.prepare(root)
            self.assertTrue(all(p.read_text().strip() == "DMA" for p in controls))

    def test_recovery_and_unqualified_kernels_are_skipped_before_firmware_access(self):
        for release in ("7.2.8-23-omarchy-p16-asus", "7.3.0-7-omarchy", "unknown"):
            with self.subTest(release=release), self.assertRaisesRegex(RuntimeError, "Unqualified NPU kernel"):
                provider_start.check_host(Path("/nonexistent"), release, Path("/nonexistent"))

    def test_partially_bound_endpoints_prevent_domain_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controls = self.layout(root)
            driver = root / "bus/platform/devices/NVDA200A:00/driver"
            driver.mkdir()
            with self.assertRaisesRegex(RuntimeError, "Partially bound"):
                provider_start.prepare(root)
            self.assertTrue(all(p.read_text().strip() == "identity" for p in controls))


class DSPBootTrialTests(unittest.TestCase):
    def test_firmware_publish_preserves_existing_and_conflicting_files(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "firmware.bin"
            self.assertTrue(dsp_boot.publish_file(target, b"verified firmware"))
            self.assertFalse(dsp_boot.publish_file(target, b"verified firmware"))
            with self.assertRaisesRegex(RuntimeError, "Preserving conflicting"):
                dsp_boot.publish_file(target, b"different firmware")
            self.assertEqual(target.read_bytes(), b"verified firmware")
            link = Path(directory) / "link.bin"
            link.symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, "Preserving conflicting"):
                dsp_boot.publish_file(link, b"verified firmware")
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ["firmware.bin", "link.bin"])

    def test_failed_atomic_publish_cleans_up_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "firmware.bin"
            with patch.object(dsp_boot.os, "link", side_effect=FileExistsError("race")):
                with self.assertRaises(FileExistsError):
                    dsp_boot.publish_file(target, b"verified firmware")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_child_boot_writer_sends_the_exact_six_byte_command(self):
        tree = ast.parse((ROOT / "scripts/validate-dsp-boot.py").read_text())
        writers = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and
                   isinstance(n.value, str) and n.value.startswith("import os,sys;f=os.open")]
        self.assertEqual(len(writers), 1)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "control"
            target.touch()
            subprocess.run(["python", "-c", writers[0], str(target)], check=True)
            self.assertEqual(target.read_bytes(), b"start\n")


class ProviderTrialTests(unittest.TestCase):
    def state(self, stopped=False):
        return {"status": dict.fromkeys(("dla_bound", "sroot_bound", "idle_observed", "ping_verified",
                "region5_registered", "config_valid", "config_apply_accepted", "lpwr_accepted", "task_verified"), "1") |
                {"queue_poisoned": "0", "error": "0"},
                "hfrp_status": {"error": "0", "power_accepted": "1"},
                "shutdown_status": {"attempted": "1" if stopped else "0", "error": "0",
                    "hardware_off": "1" if stopped else "0", "released": "1" if stopped else "0",
                    "hfrp_held": "0" if stopped else "1", "upload_held": "0" if stopped else "1"},
                "module_refcnt": "0", "info": {"abi_version": 2, "capabilities": 8,
                    "retained_jobs": 0, "retained_bytes": 0}}

    def test_busy_unhealthy_or_previously_stopped_driver_is_not_changed(self):
        for area, key, value in (("info", "retained_jobs", 1), ("info", "retained_bytes", 1),
                                 ("info", "capabilities", 0), ("info", "abi_version", 1),
                                 ("status", "queue_poisoned", "1"), ("status", "task_verified", "0"),
                                 ("hfrp_status", "error", "-5"), ("shutdown_status", "attempted", "1")):
            with self.subTest(key=key):
                state = self.state()
                state[area][key] = value
                with patch.object(provider_live, "snapshot", return_value=state), \
                     patch.object(provider_live, "trigger_stop") as stop, \
                     patch.object(provider_live, "run") as run:
                    with self.assertRaises(RuntimeError):
                        provider_live.checked_stop_and_unload({})
                    stop.assert_not_called()
                    run.assert_not_called()

    def test_unconfirmed_stop_or_retained_references_prevent_removal(self):
        for key, value in (("hardware_off", "0"), ("released", "0"), ("error", "-5"),
                           ("hfrp_held", "1"), ("upload_held", "1"), ("module_refcnt", "1")):
            with self.subTest(key=key):
                after = self.state(stopped=True)
                if key == "module_refcnt":
                    after[key] = value
                else:
                    after["shutdown_status"][key] = value
                with patch.object(provider_live, "snapshot", side_effect=[self.state(), after]), \
                     patch.object(provider_live, "trigger_stop") as stop, \
                     patch.object(provider_live, "run") as run:
                    with self.assertRaises(RuntimeError):
                        provider_live.checked_stop_and_unload({})
                    stop.assert_called_once()
                    run.assert_not_called()

    def test_confirmed_hardware_stop_reaches_only_normal_removal(self):
        with patch.object(provider_live, "snapshot", side_effect=[self.state(), self.state(stopped=True)]), \
             patch.object(provider_live, "trigger_stop") as stop, \
             patch.object(provider_live, "run", side_effect=RuntimeError("normal removal boundary")) as run:
            with self.assertRaisesRegex(RuntimeError, "normal removal boundary"):
                provider_live.checked_stop_and_unload({})
            stop.assert_called_once()
            run.assert_called_once_with(["rmmod", "n1x_npu_bootstrap"])


class DSPProbeTrialTests(unittest.TestCase):
    def test_failed_insert_and_failed_probe_restore_original_driver(self):
        for failed_insert in (True, False):
            with self.subTest(failed_insert=failed_insert), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "test.ko").write_bytes(b"test module fixture")
                modules, drivers = root / "modules", root / "drivers"
                original = modules / "snd_sof_mt8901"
                original.mkdir(parents=True)
                (original / "refcnt").write_text("0")
                driver = drivers / dsp_live.ORIGINAL
                driver.mkdir(parents=True)
                for name in dsp_live.DEVICES:
                    (driver / name).symlink_to(root / name)
                calls = []

                def run(args):
                    calls.append(args[:2])
                    if args[0] == "insmod":
                        if failed_insert:
                            raise RuntimeError("insert failed")
                        parameter = modules / "n1x_adsp/parameters/enable_boot"
                        parameter.parent.mkdir(parents=True)
                        parameter.write_text("N")
                    return ""

                waits = [None, {"restored": True}] if failed_insert else \
                        [None, RuntimeError("probe failed"), {"restored": True}]
                report = {}
                with patch.object(dsp_live, "load_script", return_value=live), \
                     patch.object(live, "check_target"), patch.object(live, "check_module", return_value=root / "test.ko"), \
                     patch.object(dsp_live, "wait_suspended", side_effect=waits), \
                     patch.object(dsp_live, "run", side_effect=run):
                    with self.assertRaisesRegex(RuntimeError, "insert failed|probe failed"):
                        dsp_live.probe_trial(root / "test.ko", report, modules, drivers)
                self.assertEqual(calls[-1], ["modprobe", "snd_sof_mt8901"])
                self.assertEqual(["rmmod", "n1x_adsp"] in calls, not failed_insert)
                self.assertEqual(report["restored"], {"restored": True})


class SensorBootInstallTests(unittest.TestCase):
    def test_original_module_uses_verified_dkms_backup_when_in_tree_copy_was_moved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = "7.2.8-test"
            original = root / "modules" / release / "kernel/drivers/hid/spi-hid/spi-hid.ko"
            cache = root / "backups" / release / "aarch64" / "spi-hid.ko"
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b"original kernel driver")
            origin = Path(str(cache) + ".origin")
            origin.write_text(str(original))
            self.assertEqual(sensor_install.original_module("spi-hid", release,
                root / "modules", root / "backups", "aarch64"), cache)
            origin.write_text(str(root / "another-driver.ko"))
            with self.assertRaisesRegex(RuntimeError, "Expected one original"):
                sensor_install.original_module("spi-hid", release,
                    root / "modules", root / "backups", "aarch64")

    def test_installed_status_accepts_original_module_note_but_rejects_built_only(self):
        release = "7.2.8-23-omarchy-p16-asus"
        prefix = "n1x-sensorhub/0.1.0, " + release + ", aarch64: "
        for suffix in ("installed", "installed (Original modules exist)"):
            self.assertTrue(sensor_install.installed_for(prefix + suffix + "\n", release))
        for suffix in ("built", "added", "broken"):
            self.assertFalse(sensor_install.installed_for(prefix + suffix + "\n", release))
        self.assertFalse(sensor_install.installed_for(prefix + "installed\n", release + "-npu2"))

    def menu(self):
        return ("default_entry: Omarchy/linux-rtx-spark-npu\n"
                "  //linux-rtx-spark-npu\n"
                "  path: boot():/EFI/Linux/omarchy_linux-rtx-spark-npu.efi#" + "a" * 128 + "\n"
                "  cmdline: root=/dev/mapper/root console=tty0\n"
                "  //linux-p16-asus-23\n"
                "  path: boot():/EFI/Linux/omarchy_linux-p16-asus-23.efi#" + "b" * 128 + "\n"
                "  cmdline: recovery\n")

    def test_only_selected_image_hash_changes(self):
        before = self.menu()
        path, pin, command = sensor_install.uki_entry(before, "linux-rtx-spark-npu")
        self.assertEqual(command, "root=/dev/mapper/root console=tty0")
        after = sensor_install.replace_pin(before, path, pin, "c" * 128)
        self.assertEqual(after.replace("c" * 128, "a" * 128), before)
        self.assertIn("omarchy_linux-p16-asus-23.efi#" + "b" * 128, after)

    def test_ambiguous_hash_token_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicated"):
            sensor_install.replace_pin(self.menu() * 2,
                "/EFI/Linux/omarchy_linux-rtx-spark-npu.efi", "a" * 128, "c" * 128)

    def test_nonstandard_efi_path_is_rejected(self):
        menu = self.menu().replace("/EFI/Linux/omarchy_linux-rtx-spark-npu.efi", "/EFI/Linux/../other.efi")
        with self.assertRaisesRegex(ValueError, "Unexpected UKI path"):
            sensor_install.uki_entry(menu, "linux-rtx-spark-npu")

    def test_failed_copy_verification_preserves_old_boot_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / "staged.efi", root / "existing.efi"
            source.write_bytes(b"new verified image")
            target.write_bytes(b"known working image")
            check = sensor_install.digest
            with patch.object(sensor_install, "digest", side_effect=lambda p:
                              "corrupt" if Path(p).name.startswith(".p16-sensorhub-") else check(p)):
                with self.assertRaisesRegex(RuntimeError, "Copy verification failed"):
                    sensor_install.atomic_copy(source, target)
            self.assertEqual(target.read_bytes(), b"known working image")
            self.assertEqual(sorted(x.name for x in root.iterdir()), ["existing.efi", "staged.efi"])


class LiveTrialTests(unittest.TestCase):
    def test_unvalidated_bios_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            dmi = Path(temporary)
            (dmi / "product_name").write_text("ProArt P16 H7607BA\n")
            (dmi / "bios_version").write_text("H7607BA.300\n")
            live.check_target(dmi)
            (dmi / "bios_version").write_text("H7607BA.301\n")
            with self.assertRaisesRegex(RuntimeError, "Unvalidated bios_version"):
                live.check_target(dmi)

    def test_shared_spi_driver_is_rejected_before_unload(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "spi-MSFW1111:00").symlink_to(directory / "target")
            live.check_sensor_bindings(directory)
            (directory / "spi-another-device").symlink_to(directory / "another")
            check = live.check_sensor_bindings
            with patch.object(live, "check_sensor_bindings",
                              side_effect=lambda: check(directory)), \
                 patch.object(live, "command") as run:
                with self.assertRaisesRegex(RuntimeError, "refusing to unload shared"):
                    with live.sensor_trial(Path("core.ko"), Path("acpi.ko")):
                        self.fail("Trial must not start")
                run.assert_not_called()

    def sensor_context(self, run):
        self.enterContext(patch.object(live, "check_sensor_bindings"))
        self.enterContext(patch.object(live, "check_module", side_effect=lambda p: p))
        self.enterContext(patch.object(live, "command", side_effect=run))
        self.enterContext(patch.object(live.Path, "exists", return_value=True))
        return live.sensor_trial(Path("core.ko"), Path("acpi.ko"))

    def test_busy_core_restores_acpi_without_retrying_unload(self):
        calls = []

        def run(args):
            calls.append(args)
            if args == ["rmmod", "spi_hid"]:
                raise RuntimeError("core is busy")
            return ""

        with self.assertRaisesRegex(RuntimeError, "core is busy"):
            with self.sensor_context(run):
                self.fail("Trial must not start")
        self.assertEqual(calls[-1], ["modprobe", "spi_hid_acpi"])
        self.assertEqual(calls.count(["rmmod", "spi_hid"]), 1)
        self.assertFalse(any(call[0] == "insmod" for call in calls))

    def test_failed_acpi_insert_removes_trial_core_and_restores_packaged_driver(self):
        calls = []

        def run(args):
            calls.append(args)
            if args == ["insmod", Path("acpi.ko")]:
                raise RuntimeError("ACPI insert failed")
            return ""

        with self.assertRaisesRegex(RuntimeError, "ACPI insert failed"):
            with self.sensor_context(run):
                self.fail("Trial must not start")
        self.assertEqual(calls[-2:], [["rmmod", "spi_hid"], ["modprobe", "spi_hid_acpi"]])

    def test_failed_sample_unloads_both_trial_modules_and_restores_packaged_driver(self):
        calls = []

        def run(args):
            calls.append(args)
            return ""

        with self.assertRaisesRegex(RuntimeError, "sample failed"):
            with self.sensor_context(run):
                raise RuntimeError("sample failed")
        self.assertEqual(calls[-3:], [["rmmod", "spi_hid_acpi"], ["rmmod", "spi_hid"],
                                      ["modprobe", "spi_hid_acpi"]])

    def test_failed_pluton_discovery_unloads_trial_module(self):
        calls = []
        with patch.object(live, "check_module", side_effect=lambda p: p), \
             patch.object(live.Path, "exists", return_value=False), \
             patch.object(live, "command", side_effect=lambda args: calls.append(args)):
            with self.assertRaisesRegex(RuntimeError, "discovery failed"):
                with live.pluton_trial(Path("pluton.ko")):
                    raise RuntimeError("discovery failed")
        self.assertEqual(calls, [["insmod", Path("pluton.ko"), "enable_transport=1"],
                                 ["rmmod", "n1x_pluton"]])


class IIOInventoryTests(unittest.TestCase):
    def test_iio_bus_devices_are_found_without_reading_raw_channels_or_triggers(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for identifier, name in (("iio:device0", "human_presence"),
                                     ("iio:device1", "als"), ("trigger0", "ignore")):
                path = directory / identifier
                path.mkdir()
                (path / "name").write_text(name + "\n")
                # A raw read would block forever; inventory must only read
                # identification attributes and leave this FIFO unopened.
                import os
                os.mkfifo(path / "in_illuminance_raw")
            result = inventory.iio_devices(directory)
            self.assertEqual([x["id"] for x in result], ["iio:device0", "iio:device1"])
            self.assertEqual([x["name"] for x in result], ["human_presence", "als"])


class DSPBundleTests(unittest.TestCase):
    def bundle(self):
        return (struct.pack("<I", 2) + b"mt8901" + struct.pack("<I", 3) +
                b"mt8971" + struct.pack("<I", 5) + b"one" + b"other")

    def test_correct_soc_is_selected_without_modifying_signed_bytes(self):
        result = dsp.variants(self.bundle())
        self.assertEqual(result[b"mt8901"], (24, b"one"))
        self.assertEqual(result[b"mt8971"], (27, b"other"))

    def test_truncation_overflow_duplicate_soc_and_trailing_bytes_are_rejected(self):
        bad_size = bytearray(self.bundle())
        struct.pack_into("<I", bad_size, 10, 0xffffffff)
        duplicate = self.bundle().replace(b"mt8971", b"mt8901")
        for data in (b"", struct.pack("<I", 0), struct.pack("<I", 0xffffffff),
                     self.bundle()[:-1], self.bundle() + b"x", bad_size, duplicate):
            with self.subTest(data=bytes(data[:24])):
                with self.assertRaises(ValueError):
                    dsp.variants(data)


class ACPITableTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "TPM2"

    def table(self):
        data = bytearray(52)
        data[:4] = b"TPM2"
        struct.pack_into("<I", data, 4, len(data))
        struct.pack_into("<Q", data, 40, 0x12340000)
        struct.pack_into("<I", data, 48, 15)
        data[9] = -sum(data) % 256
        return data

    def test_ffa_tpm_is_distinguished_from_pluton(self):
        self.path.write_bytes(self.table())
        result = inventory.acpi_table(self.path)
        self.assertEqual(result["start_method"], 15)
        self.assertEqual(result["control_address"], "0x12340000")

    def test_truncated_and_corrupt_tables_are_rejected(self):
        for data in (b"TPM2", self.table()[:-1], self.table() + b"x"):
            with self.subTest(size=len(data)):
                self.path.write_bytes(data)
                with self.assertRaises(ValueError):
                    inventory.acpi_table(self.path)
        data = self.table()
        data[-1] ^= 1
        self.path.write_bytes(data)
        with self.assertRaises(ValueError):
            inventory.acpi_table(self.path)


class OEMExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.vendor = Path(self.temporary.name)
        self.recovery = self.vendor / "recovery.exe"
        self.recovery.write_bytes(b"verified container fixture")
        self.spec = {"sha256": hashlib.sha256(self.recovery.read_bytes()).hexdigest()}
        self.relative = "Windows/System32/DriverStore/FileRepository/test/firmware.bin"
        self.file = self.vendor / "platform-drivers" / self.relative
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(b"verified signed firmware fixture")
        self.lock = {"recovery": "recovery.exe", "files": [
            {"path": self.relative, "bytes": self.file.stat().st_size,
             "sha256": hashlib.sha256(self.file.read_bytes()).hexdigest()}]}

    def test_corrupt_recovery_never_invokes_an_extractor(self):
        self.recovery.write_bytes(b"wrong container")
        with patch.object(extractor.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                extractor.extract(self.vendor, self.lock, self.spec)
            run.assert_not_called()
        self.assertFalse((self.vendor / "platform-drivers.receipt.json").exists())

    def test_changed_firmware_is_preserved_and_not_certified(self):
        changed = b"a different OEM firmware revision"
        self.file.write_bytes(changed)
        with patch.object(extractor.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                extractor.extract(self.vendor, self.lock, self.spec)
            run.assert_not_called()
        self.assertEqual(self.file.read_bytes(), changed)
        self.assertFalse((self.vendor / "platform-drivers.receipt.json").exists())

    def test_verified_existing_inputs_need_no_extraction(self):
        with patch.object(extractor.subprocess, "run") as run:
            extractor.extract(self.vendor, self.lock, self.spec)
            run.assert_not_called()
        receipt = json.loads((self.vendor / "platform-drivers.receipt.json").read_text())
        self.assertFalse(receipt["windows_code_executed"])
        self.assertEqual(receipt["files"], self.lock["files"])

    def test_manifest_cannot_escape_the_extracted_tree(self):
        for relative in ("../outside", "/etc/passwd"):
            with self.subTest(relative=relative):
                with self.assertRaisesRegex(ValueError, "Unsafe manifest"):
                    extractor.validate_tree(self.vendor, [{"path": relative}])


if __name__ == "__main__":
    unittest.main()
