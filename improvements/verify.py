#!/usr/bin/env python3
"""Verify the curated export without building, installing or accessing hardware."""
import hashlib
from pathlib import Path

root = Path(__file__).resolve().parent
entries = {}
for line in (root / 'SHA256SUMS').read_text().splitlines():
    expected, name = line.split('  ', 1)
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
print(f'PASS: {len(entries)} source hashes; {len(series)} ordered kernel patches')
