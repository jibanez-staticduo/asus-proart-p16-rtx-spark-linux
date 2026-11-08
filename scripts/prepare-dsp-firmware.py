#!/usr/bin/env python3
"""Select intact MT8901 signed images from the pinned ASUS multi-SoC DSP bundles."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT.parent / "n1x-lab/platform"
IMAGES = {
    "hifi5_a_cose_sign_image.bin": ("hifi5-0-mt8901-signed.bin", "350e0d40b2f8a6dfb486d8dfd3362fb5b02ceb90da372176b008498adbca8cc4"),
    "hifi5_b_cose_sign_image.bin": ("hifi5-1-mt8901-signed.bin", "517ef0d447ca80b7cb88ec0589cda816d9b20c844800ca95ccf7cbe96fc7d40e"),
    "hifi1_cose_sign_image.bin": ("hifi1-0-mt8901-signed.bin", "434660b36f4e21cb794bc50e10a47b91882710cbe90cb60620111a2a753c5b94"),
}
IMAGE_IDS = {"hifi1-0-mt8901-signed.bin": 0xc0303,
             "hifi5-0-mt8901-signed.bin": 0xc0304,
             "hifi5-1-mt8901-signed.bin": 0xc0305}


class CBOR:
    """Bounded definite-length CBOR subset used by the pinned COSE metadata.

    Parses structure only; it does not authenticate a COSE signature.
    """
    def __init__(self, data):
        self.data, self.pos, self.budget = data, 0, 512

    def take(self, count):
        if count > len(self.data) - self.pos:
            raise ValueError("Truncated DSP CBOR metadata")
        result = self.data[self.pos:self.pos + count]
        self.pos += count
        return result

    def item(self, depth=0):
        self.budget -= 1
        if depth > 12 or self.budget < 0:
            raise ValueError("DSP CBOR nesting/item limit exceeded")
        initial = self.take(1)[0]
        major, additional = initial >> 5, initial & 31
        if additional < 24:
            size = additional
        elif additional in (24, 25, 26, 27):
            size = int.from_bytes(self.take(1 << (additional - 24)), "big")
        else:
            raise ValueError("Unsupported/indefinite DSP CBOR item")
        if major in (0, 1):
            return size if major == 0 else -1 - size
        if major == 2:
            return self.take(size)
        if major in (4, 5):
            if size > 64:
                raise ValueError("DSP CBOR container limit exceeded")
            if major == 4:
                return [self.item(depth + 1) for _ in range(size)]
            result = {}
            for _ in range(size):
                key = self.item(depth + 1)
                if not isinstance(key, int) or key in result:
                    raise ValueError("Invalid/duplicate DSP CBOR map key")
                result[key] = self.item(depth + 1)
            return result
        if major == 6:
            return (size, self.item(depth + 1))
        raise ValueError("Unsupported DSP CBOR major type")

    def complete(self):
        result = self.item()
        if self.pos != len(self.data):
            raise ValueError("Trailing DSP CBOR metadata")
        return result


def inspect_image(image, expected_id):
    """Validate the observed signed container and its six section digests.

    Keep the COSE prefix and all section headers intact for SROOT. Matching
    digests establish container consistency, not authenticity or execution.
    """
    if len(image) < 0x710 + 208 or image[:3] != b"\x59\x07\x0d":
        raise ValueError("Unexpected DSP signed-container header")
    certificate = CBOR(image[3:0x710]).complete()
    if not isinstance(certificate, list) or len(certificate) != 3 or certificate[2] != bytes(11):
        raise ValueError("Unexpected DSP COSE certificate container")
    signed_metadata = []
    for chain in certificate[:2]:
        if not (isinstance(chain, list) and len(chain) == 1 and
                isinstance(chain[0], list) and len(chain[0]) == 1):
            raise ValueError("Unexpected DSP certificate chain")
        tagged = chain[0][0]
        if not isinstance(tagged, tuple) or tagged[0] != 98:
            raise ValueError("Expected DSP COSE_Sign tag")
        body = tagged[1]
        if not (isinstance(body, list) and len(body) == 4 and body[:2] == [b"", {}] and
                isinstance(body[2], bytes) and isinstance(body[3], list) and len(body[3]) == 1):
            raise ValueError("Unexpected DSP COSE_Sign body")
        signer = body[3][0]
        if not (isinstance(signer, list) and len(signer) == 3 and
                isinstance(signer[0], bytes) and signer[1] == {} and isinstance(signer[2], bytes)):
            raise ValueError("Unexpected DSP COSE signer")
        signed_metadata.append(CBOR(body[2]).complete())
    if not all(isinstance(m, dict) for m in signed_metadata) or any(
            signed_metadata[0].get(key) != signed_metadata[1].get(key)
            for key in (-1294334, -1310719)):
        raise ValueError("DSP certificate section metadata differs")
    metadata = signed_metadata[0]
    if not isinstance(metadata, dict) or metadata.get(-1310719) != 208:
        raise ValueError("Unexpected DSP native-header size")
    digests = metadata.get(-1294334)
    if not isinstance(digests, list) or len(digests) != 6:
        raise ValueError("Expected six signed DSP section digests")
    native = image[0x710:]
    if struct.unpack_from("<III", native) != (1, 6, 0xefbeadde) or \
            struct.unpack_from("<I", native, 204)[0] != 0xefbeadde:
        raise ValueError("Unexpected DSP native section table")
    end, sections = 0, []
    for index, section_id in enumerate((0, 0x1000, 0x2100, 0x2200, 0x2300, 0x2000)):
        marker, image_id, section, offset, header, payload, reserved1, reserved2 = \
            struct.unpack_from("<8I", native, 12 + index * 32)
        if (marker, image_id, section, offset, header, reserved1, reserved2) != \
                (0xefbeadde, expected_id, section_id, end, 0 if index == 0 else 208, 0, 0):
            raise ValueError("Unexpected DSP section identity/layout")
        if not payload or (index == 0 and payload != 208) or header + payload > len(native) - offset:
            raise ValueError("DSP section extends beyond container")
        end = offset + header + payload
        digest = hashlib.sha384(native[offset:end]).digest()
        if digests[index] != [-43, digest]:
            raise ValueError("DSP section SHA-384 mismatch")
        sections.append({"id": section, "offset": offset, "header_bytes": header,
                         "payload_bytes": payload, "sha384": digest.hex()})
    if end != len(native):
        raise ValueError("Unaccounted trailing DSP image bytes")
    return {"image_id": expected_id, "cose_bytes": 0x710, "sections": sections,
            "section_digests_verified": True, "signatures_authenticated": False}


def variants(data):
    """Observed format: LE32 count, count * {SoC[6], LE32 size}, image bytes."""
    data = bytes(data)
    if len(data) < 4:
        raise ValueError("Truncated multi-SoC header")
    count = struct.unpack_from("<I", data)[0]
    if not 1 <= count <= 16 or 4 + count * 10 > len(data):
        raise ValueError("Invalid variant count/header length")
    offset = 4 + count * 10
    result = {}
    for index in range(count):
        entry = 4 + 10 * index
        soc = data[entry:entry + 6]
        size = struct.unpack_from("<I", data, entry + 6)[0]
        if not re.fullmatch(b"mt[0-9]{4}", soc) or soc in result:
            raise ValueError("Invalid or duplicate SoC variant")
        if not size or size > len(data) - offset:
            raise ValueError("Variant extends beyond its verified container")
        result[soc] = (offset, data[offset:offset + size])
        offset += size
    if offset != len(data):
        raise ValueError("Unaccounted trailing bytes in DSP bundle")
    return result


def prepare(vendor, destination):
    lock = json.loads((ROOT / "config/platform-oem.json").read_text())
    staged = []
    # Verify every input/output before publishing any image.
    for spec in lock["files"]:
        name = Path(spec["path"]).name
        if name not in IMAGES:
            continue
        source = vendor / "platform-drivers" / spec["path"]
        data = source.read_bytes()
        if len(data) != spec["bytes"] or hashlib.sha256(data).hexdigest() != spec["sha256"]:
            raise ValueError(f"Unverified OEM DSP bundle: {source}")
        images = variants(data)
        if b"mt8901" not in images:
            raise ValueError(f"No MT8901 variant in {source}")
        offset, image = images[b"mt8901"]
        output, expected = IMAGES[name]
        if hashlib.sha256(image).hexdigest() != expected:
            raise ValueError(f"MT8901 image hash mismatch: {source}")
        container = inspect_image(image, IMAGE_IDS[output])
        target = destination / output
        if target.is_symlink() or (target.exists() and target.read_bytes() != image):
            raise ValueError(f"Preserving conflicting firmware output: {target}")
        staged.append((target, image, {"name": output, "bytes": len(image),
                       "sha256": expected, "bundle": name, "bundle_offset": offset,
                       "container": container}))
    if len(staged) != len(IMAGES):
        raise ValueError("Missing pinned DSP bundle entries")
    destination.mkdir(parents=True, exist_ok=True)
    for target, image, _ in staged:
        if not target.exists():
            with target.open("xb") as stream:
                stream.write(image)
    receipt = {"schema": 1, "soc": "mt8901", "images": [s[2] for s in staged],
               "signature_verification": "pending secure firmware service; original signed bytes preserved",
               "installed_for_execution": False}
    (destination / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", type=Path, default=LAB / "sources/vendor")
    parser.add_argument("--dest", type=Path, default=LAB / "firmware")
    args = parser.parse_args()
    result = prepare(args.vendor, args.dest)
    print(f"Staged {len(result['images'])} verified MT8901 DSP images in {args.dest}; execution not enabled")


if __name__ == "__main__":
    main()
