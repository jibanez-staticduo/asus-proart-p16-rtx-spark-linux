#!/usr/bin/env python3
"""Read ROT1 status, or explicitly query PFI channel availability."""
import argparse
import fcntl
import json
import os
import struct


def read_ioctl(fd, number, size):
    buf = bytearray(size)
    fcntl.ioctl(fd, (2 << 30) | (size << 16) | (ord("P") << 8) | number, buf, True)
    return buf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/n1x-pluton")
    parser.add_argument("--discover", action="store_true",
                        help="perform PFI control-channel transactions; requires enable_transport=1")
    args = parser.parse_args()
    result = {"device": args.device}
    try:
        fd = os.open(args.device, os.O_RDONLY | os.O_CLOEXEC)
        try:
            values = struct.unpack("<8I", read_ioctl(fd, 0, 32))
            keys = ("abi_version", "boot_status", "register_stride", "transport_enabled",
                    "initialized", "poisoned", "last_pfi_status", "reserved")
            result.update(zip(keys, values))
            if result["abi_version"] != 1:
                raise ValueError("unsupported driver ABI")
            if args.discover:
                buf = read_ioctl(fd, 1, 40)
                count, reserved = struct.unpack_from("<II", buf)
                if count > 16 or reserved:
                    raise ValueError("invalid channel response")
                result["channels"] = [{"id": buf[8 + i * 2], "flags": buf[9 + i * 2],
                                       "write_ready": bool(buf[9 + i * 2] & 1),
                                       "read_ready": bool(buf[9 + i * 2] & 2)}
                                      for i in range(count)]
        finally:
            os.close(fd)
    except (OSError, ValueError) as error:
        result["error"] = str(error)
        print(json.dumps(result, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
