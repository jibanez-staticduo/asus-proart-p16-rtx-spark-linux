# RTX Spark telemetry on Linux 7.3

This patch updates Johann Hartmann's `spark_hwmon` driver for Linux 7.3's
platform-driver ACPI binding. It admits the exact ASUS ProArt P16 H7607BA on
any BIOS version, while retaining ACPI schema, platform-ID, MMIO bounds and
temperature-contract checks. Absence of the ACPI temperature zones can use the
validated P16 MMIO backend. It does not fabricate missing ACPI sensors.

## Build against your running kernel

Install your distribution's matching kernel headers, C compiler, make and git.
As a regular user:

```sh
git clone https://github.com/johannhartmann/spark_hwmon.git
cd spark_hwmon
git checkout --detach ef94faace8a44380e8ba8c8bd8cffd185c82af67
patch --batch --fuzz=0 -p1 < /path/to/this-fork/improvements/telemetry/0001-p16-platform-and-bios-support.patch
make -C /lib/modules/"$(uname -r)"/build M="$PWD" modules
```

Use your distribution's module packaging/signing procedure to install the
resulting `spbm.ko`, or DKMS with the source's `dkms.conf`. It is not tied to a
kernel release suffix. On a Secure Boot system sign it with a key trusted by
your kernel before loading. Do not install a competing copy of the same module.

After installation, `sudo modprobe spbm` loads the driver. `sensors` discovers
`spbm_n1x`; desktop tools such as Vitals can read its hwmon channels. Fan RPM
comes from ASUS WMI (kernel patch 9100), not this driver. Names and available
channels depend on firmware. H7607BA BIOS 303 has been tested; other BIOS
versions are eligible but untested.

The inherited driver also exposes power-limit controls. Nothing in these
instructions writes them; normal monitoring only reads attributes. Their
presence is not a recommendation to alter platform limits.

## Source tests without hardware access

From the patched driver directory:

```sh
cc -std=c11 -Wall -Wextra -Werror -I. \
  /path/to/this-fork/improvements/telemetry/test_probe_policy.c -o /tmp/p16-probe-test
/tmp/p16-probe-test
cc -std=gnu11 -Wall -Wextra -Werror -Wno-sign-compare \
  -Wno-unused-function -Wno-unused-variable -I. \
  /path/to/this-fork/improvements/telemetry/test_n1x_mmio.c -o /tmp/p16-mmio-test
/tmp/p16-mmio-test
```

The MMIO test compiles production callbacks against RAM, never physical
registers. Upstream `python3 -m unittest discover -s tests -p test_driver_helpers.py -v`
exercises the firmware parser too. Kernel build
compatibility and physical validation remain separate checks.

Source: https://github.com/johannhartmann/spark_hwmon, pinned commit above.
Driver and patch: GPL-2.0-only; preserved source notices and `LICENSE` apply.
