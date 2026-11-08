# SPDX-License-Identifier: MIT
"""Validate safetensors inventories and checkpoint hashes without loading tensors."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import os


def checkpoint_file(root, name):
    path = root / name
    if Path(name).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Checkpoint path escapes its directory: {name}")
    if not path.is_file():
        raise ValueError(f"Incomplete checkpoint: {path}")
    return path


def tensor_inventory(root):
    index = root / "model.safetensors.index.json"
    mapping = json.loads(index.read_text())["weight_map"] if index.exists() else None
    names = set(mapping.values()) if mapping else {"model.safetensors"}
    if mapping == {}:
        raise ValueError("Empty checkpoint index")
    tensors = {}
    for name in sorted(names):
        path = checkpoint_file(root, name)
        with path.open("rb") as stream:
            prefix = stream.read(8)
            if len(prefix) != 8:
                raise ValueError(f"Truncated safetensors header: {path}")
            length, = struct.unpack("<Q", prefix)
            if not 2 <= length <= min(100_000_000, path.stat().st_size - 8):
                raise ValueError(f"Invalid safetensors header length: {path}")
            header = json.loads(stream.read(length))
        data_start = 8 + length
        spans = []
        for key, info in header.items():
            if key == "__metadata__":
                continue
            start, end = info["data_offsets"]
            if (type(start) is not int or type(end) is not int or
                    not 0 <= start <= end <= path.stat().st_size - data_start or
                    key in tensors):
                raise ValueError(f"Invalid tensor offsets or duplicate tensor: {key}")
            if mapping is not None and mapping.get(key) != name:
                raise ValueError(f"Tensor index disagrees with shard: {key}")
            tensors[key] = (path, data_start + start, end - start, info["dtype"], info["shape"])
            spans.append((start, end))
        cursor = 0
        for start, end in sorted(spans):
            if start != cursor:
                raise ValueError(f"Noncontiguous tensor data: {path}")
            cursor = end
        if data_start + cursor != path.stat().st_size:
            raise ValueError(f"Safetensors file size disagrees with header: {path}")
    if not tensors or (mapping is not None and tensors.keys() != mapping.keys()):
        raise ValueError("Incomplete tensor inventory")
    return tensors


def verify_tensor_bytes(source, target):
    original, prepared = tensor_inventory(source), tensor_inventory(target)
    if original.keys() != prepared.keys():
        raise ValueError("Prepared tensor inventory differs from source")
    for key, (src, offset, size, dtype, shape) in original.items():
        dst, dst_offset, dst_size, dst_dtype, dst_shape = prepared[key]
        if (size, dtype, shape) != (dst_size, dst_dtype, dst_shape):
            raise ValueError(f"Prepared tensor metadata differs: {key}")
        with src.open("rb") as a, dst.open("rb") as b:
            a.seek(offset)
            b.seek(dst_offset)
            remaining = size
            while remaining:
                count = min(remaining, 8 * 1024 * 1024)
                left, right = a.read(count), b.read(count)
                if len(left) != count or left != right:
                    raise ValueError(f"Prepared tensor bytes differ: {key}")
                remaining -= count


def file_manifest(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path != root / "reshard-provenance.json":
            relative = path.relative_to(root).as_posix()
            checkpoint_file(root, relative)
            with path.open("rb") as stream:
                result[relative] = {"size": path.stat().st_size,
                                    "sha256": hashlib.file_digest(stream, "sha256").hexdigest()}
    return result


def verify_manifest(root, provenance):
    tensor_inventory(root)
    expected = provenance.get("files")
    if not expected or expected != file_manifest(root):
        raise ValueError("Checkpoint integrity manifest missing or files changed; run models preparation")


def write_provenance(root, provenance):
    provenance = {**provenance, "files": file_manifest(root)}
    with tempfile.NamedTemporaryFile(mode="w", dir=root.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(provenance, stream, indent=2)
            stream.write("\n")
            stream.close()
            os.replace(temporary, root / "reshard-provenance.json")
        finally:
            temporary.unlink(missing_ok=True)
