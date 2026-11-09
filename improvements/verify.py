#!/usr/bin/env python3
"""Verify the curated export without building, installing or accessing hardware."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
entries = {}
for line in (root / 'SHA256SUMS').read_text().splitlines():
    expected, name = line.split('  ', 1)
    if name in entries:
        raise SystemExit(f'Duplicate checksum entry: {name}')
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise SystemExit(f'Unsafe export path: {name}')
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit(f'Checksum mismatch: {name}')
    entries[name] = expected
actual = {str(p.relative_to(root)) for p in root.rglob('*')
          if p.is_file() and '__pycache__' not in p.parts and p.name != 'SHA256SUMS'}
if actual != set(entries):
    raise SystemExit(f'Unlisted or missing files: {sorted(actual ^ set(entries))}')
series = (root / 'kernel/series').read_text().splitlines()
patches = {p.name for p in (root / 'kernel').glob('*.patch')}
if len(series) != len(set(series)) or set(series) != patches:
    raise SystemExit('Kernel series has missing or duplicate entries')
sources = json.loads((root / 'sources.json').read_text())
if sources['kernel']['patch_count'] != len(series):
    raise SystemExit('Source manifest kernel patch count mismatch')
for item in (*sources['kernel']['followups'], *sources['kernel']['local_followups']):
    if entries.get(item['file']) != item['sha256']:
        raise SystemExit(f'Source manifest checksum mismatch: {item["file"]}')
for name in actual:
    if '.omarchy' in Path(name).parts or Path(name).suffix in ('.ko', '.bin', '.dll', '.sys', '.efi'):
        raise SystemExit(f'Non-source payload in portable export: {name}')
print(f'PASS: {len(entries)} source hashes; {len(series)} ordered kernel patches')
