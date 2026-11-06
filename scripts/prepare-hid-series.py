#!/usr/bin/env python3
"""Reconstruct the SPI HID baseline and export the P16 fixes separately."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = Path('drivers/hid/spi-hid')
FILES = ('spi-hid-acpi.c', 'spi-hid-core.c', 'spi-hid-core.h',
         'spi-hid.h', 'spi-hid-trace.h')


def run(directory, *arguments):
    return subprocess.run(['git', *arguments], cwd=directory, check=True,
                          text=True, capture_output=True).stdout


def prerequisites():
    patches = sorted(path for path in (ROOT / 'kernel').glob('*.patch')
                     if path.name[:4].isdigit() and 2111 <= int(path.name[:4]) <= 2120)
    if len(patches) != 10:
        raise RuntimeError('The ten SPI HID prerequisite patches are required')
    return patches + [ROOT / 'kernel/2152-arm64-qcom-add-Surface-Pro-11-platform-support.patch']


def reconstruct(directory):
    for patch in prerequisites():
        run(directory, 'apply', '--include=drivers/hid/spi-hid/*', str(patch))


def without_input_quirk(source):
    source = source.replace('#include <linux/acpi.h>\n', '', 1)
    source = source.replace('#include <linux/dmi.h>\n', '', 1)
    guard = ('\tif (shid->input_stream_only && rtype == HID_INPUT_REPORT)\n'
             '\t\treturn -EOPNOTSUPP;\n')
    if source.count(guard) != 1:
        raise RuntimeError('Input-report quirk changed; review the patch split')
    source = source.replace(guard, '', 1)
    start = source.index('static void spi_hid_ll_request(')
    end = source.index('static struct hid_ll_driver spi_hid_ll_driver =', start)
    source = source[:start] + source[end:]
    source = source.replace('\t.request = spi_hid_ll_request,\n', '', 1)
    guard = ('\tshid->input_stream_only = dmi_match(DMI_PRODUCT_NAME, "ProArt P16 H7607BA") &&\n'
             '\t\tACPI_COMPANION(dev) &&\n'
             '\t\tacpi_dev_hid_uid_match(ACPI_COMPANION(dev), "MSFW1111", NULL);\n')
    if source.count(guard) != 1:
        raise RuntimeError('Device match changed; review the patch split')
    return source.replace(guard, '', 1)


def prepare(output):
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError('Use an empty output directory')
    manifest = {'prerequisites': [
        {'path': str(path.relative_to(ROOT)),
         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in prerequisites()], 'patches': [], 'result': {}}
    with tempfile.TemporaryDirectory(prefix='p16-hid-series-') as temporary:
        tree = Path(temporary)
        reconstruct(tree)
        run(tree, 'init', '-q')
        run(tree, 'add', '.')
        manifest['baseline'] = {
            name: hashlib.sha256((tree / PREFIX / name).read_bytes()).hexdigest()
            for name in FILES}

        def emit(name, subject, explanation):
            patch = run(tree, 'diff', '--no-ext-diff', '--no-color', '--binary',
                        '--src-prefix=a/', '--dst-prefix=b/')
            if not patch:
                raise RuntimeError(f'Empty patch: {name}')
            content = f'Subject: [PATCH] {subject}\n\n{explanation}\n\n---\n{patch}'
            (output / name).write_text(content)
            manifest['patches'].append(name)
            run(tree, 'add', '.')

        run(tree, 'apply', str(ROOT / 'kernel/spi-acpi-irq-retry.patch'))
        emit('0001-HID-spi-hid-retry-ACPI-GPIO-IRQ-lookup-at-probe.patch',
             'HID: spi-hid: retry ACPI GPIO IRQ lookup at probe',
             'A GPIO IRQ may be unavailable during SPI enumeration. Resolve it again\n'
             'at probe and propagate deferred-probe errors before creating the HID\n'
             'device.')
        (tree / PREFIX / FILES[0]).write_bytes((ROOT / 'platform/sensorhub' / FILES[0]).read_bytes())
        emit('0002-HID-spi-hid-evaluate-reset-on-the-ACPI-handle.patch',
             'HID: spi-hid: evaluate reset on the ACPI handle',
             'The acpi_device is the namespace device, rather than its physical SPI\n'
             'companion. Evaluate _RST using its namespace handle so reset reaches\n'
             'the firmware method checked during probe.')
        core = (ROOT / 'platform/sensorhub/spi-hid-core.c').read_text()
        (tree / PREFIX / 'spi-hid-core.c').write_text(without_input_quirk(core))
        emit('0003-HID-spi-hid-serialize-and-type-GET-report-responses.patch',
             'HID: spi-hid: serialize and type GET report responses',
             'Select GET_INPUT_REPORT for input reports and accept its response type.\n'
             'Hold output_lock through response validation and copying, preventing\n'
             'another caller from replacing the shared reply after completion.')
        (tree / PREFIX / 'spi-hid-core.c').write_text(core)
        (tree / PREFIX / 'spi-hid-core.h').write_bytes(
            (ROOT / 'platform/sensorhub/spi-hid-core.h').read_bytes())
        emit('0004-HID-spi-hid-handle-P16-stream-only-input-reports.patch',
             'HID: spi-hid: handle P16 stream-only input reports',
             'The P16 MSFW1111 sensor firmware sends input samples as DATA reports\n'
             'and does not answer GET_INPUT_REPORT. Let the pending HID sensor read\n'
             'complete from its DATA report and reject synchronous raw input reads.\n'
             'Restrict the behavior to the matching DMI product and ACPI device.')
        for name in FILES:
            actual = (tree / PREFIX / name).read_bytes()
            if actual != (ROOT / 'platform/sensorhub' / name).read_bytes():
                raise RuntimeError(f'Series does not reproduce packaged source: {name}')
            manifest['result'][name] = hashlib.sha256(actual).hexdigest()
    (output / 'series').write_text(''.join(name + '\n' for name in manifest['patches']))
    (output / 'baseline.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = prepare(args.output.resolve())
    print(f"Exported {len(manifest['patches'])} patches; all five packaged sources match")


if __name__ == '__main__':
    main()
