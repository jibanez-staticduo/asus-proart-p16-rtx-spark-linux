#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Split checkpoint files on the CPU, preserving and comparing tensor bytes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
from checkpoint import verify_manifest, verify_tensor_bytes, write_provenance


def prepare(source, target, pins):
    if source.resolve() == target.resolve():
        raise ValueError("Source and output must differ")
    if target.exists():
        provenance = json.loads((target / "reshard-provenance.json").read_text())
        index = json.loads((target / "model.safetensors.index.json").read_text())
        if (provenance.get("revision") != pins["target"]["revision"] or
                provenance.get("source") != pins["target"]["repo"] or
                provenance.get("all_tensor_bytes_verified_identical") is not True or
                not index["weight_map"] or
                not all((target / f).is_file() for f in index["weight_map"].values())):
            raise ValueError("Existing checkpoint is incomplete or has different provenance")
        if provenance.get("files"):
            verify_manifest(target, provenance)
        else:
            revision = (source / ".cache/huggingface/download/config.json.metadata").read_text().splitlines()[0]
            if revision != pins["target"]["revision"]:
                raise ValueError("Source revision differs from pinned checkpoint")
            verify_tensor_bytes(source, target)
            write_provenance(target, provenance)
        print("Verified prepared-checkpoint integrity; existing weights preserved.")
        return

    revision = (source / ".cache/huggingface/download/config.json.metadata").read_text().splitlines()[0]
    if revision != pins["target"]["revision"]:
        raise ValueError("Source revision differs from the pinned checkpoint")

    import torch
    from huggingface_hub import split_torch_state_dict_into_shards
    from safetensors import safe_open
    from safetensors.torch import save_file

    original = json.loads((source / "model.safetensors.index.json").read_text())
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=target.name + ".", dir=target.parent) as directory:
        output = Path(directory) / "checkpoint"
        output.mkdir()
        mapping = {}
        for input_name in sorted(set(original["weight_map"].values())):
            with safe_open(source / input_name, framework="pt", device="cpu") as saved:
                state = {k: saved.get_tensor(k) for k in saved.keys()}
                if input_name == "model.safetensors":
                    split = split_torch_state_dict_into_shards(state, max_shard_size="1GB")
                    for filename, names in split.filename_to_tensors.items():
                        save_file({k: state[k] for k in names}, output / filename, metadata=saved.metadata())
                        with safe_open(output / filename, framework="pt", device="cpu") as written:
                            for name in names:
                                a, b = state[name], written.get_tensor(name)
                                if a.shape != b.shape or a.dtype != b.dtype or not torch.equal(a.view(torch.uint8), b.view(torch.uint8)):
                                    raise ValueError(f"Tensor bytes changed: {name}")
                        mapping.update({k: filename for k in names})
                else:
                    # Preserve auxiliary weights (including the MTP head) verbatim.
                    shutil.copy2(source / input_name, output / input_name)
                    with safe_open(output / input_name, framework="pt", device="cpu") as written:
                        if set(written.keys()) != set(state):
                            raise ValueError("Auxiliary tensor inventory changed")
                        for name, tensor in state.items():
                            copied = written.get_tensor(name)
                            if tensor.shape != copied.shape or tensor.dtype != copied.dtype or not torch.equal(tensor.view(torch.uint8), copied.view(torch.uint8)):
                                raise ValueError(f"Auxiliary tensor bytes changed: {name}")
                    mapping.update({k: input_name for k in state})
                del state
        if mapping.keys() != original["weight_map"].keys():
            raise ValueError("Tensor inventory changed")
        for path in source.iterdir():
            if path.is_file() and not path.name.endswith(".safetensors") and path.name != "model.safetensors.index.json":
                shutil.copy2(path, output / path.name)
        (output / "model.safetensors.index.json").write_text(json.dumps({"metadata": original["metadata"], "weight_map": mapping}, indent=2) + "\n")
        write_provenance(output, {
            "source": pins["target"]["repo"], "revision": pins["target"]["revision"],
            "max_shard_size": "1GB", "all_tensor_bytes_verified_identical": True,
        })
        # Rename the completed directory only after every tensor has been checked.
        os.rename(output, target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--pins", type=Path, default=Path(__file__).with_name("models.json"))
    args = parser.parse_args()
    prepare(args.source, args.target, json.loads(args.pins.read_text()))
