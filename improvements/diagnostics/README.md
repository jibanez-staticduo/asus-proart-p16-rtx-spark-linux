# Optional SCP and Pluton diagnostics

These are manual diagnostic tools, not requirements for ALS/audio/boot. Instead
of duplicating Johann Hartmann's drivers, the patches adapt the exact source
at P16 PR3 merge `5f3fbe50cd37ffe9ab2c697cb60c37bc01314a99`. GPL source and
protocol attribution are retained upstream; OEM reference binaries stay absent.

```sh
git clone https://github.com/johannhartmann/asus-proart-p16-rtx-spark-linux diagnostics-source
cd diagnostics-source
git checkout --detach 5f3fbe50cd37ffe9ab2c697cb60c37bc01314a99
# Set export_dir to this fork's absolute improvements/diagnostics directory.
patch -p1 --fuzz=0 < "$export_dir/scp/0001-manual-neutral-diagnostics.patch"
patch -p1 --fuzz=0 < "$export_dir/pluton/0001-manual-neutral-diagnostics.patch"
make -C /path/to/kernel/build M="$PWD/platform/scp" W=1 modules
make -C /path/to/kernel/build M="$PWD/platform/pluton" W=1 modules
N1X_DIAGNOSTIC_SOURCE="$PWD/platform/scp" python3 "$export_dir/scp/test_snapshot.py"
N1X_DIAGNOSTIC_SOURCE="$PWD/platform/pluton" python3 "$export_dir/pluton/test_query.py"
cc -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined -g \
  platform/pluton/protocol_test.c -o /tmp/n1x-pluton-protocol
/tmp/n1x-pluton-protocol
```

Package the resulting modules with the distribution's matching headers/signing
policy. Do not install the upstream modules-load/modprobe configuration: these
adaptations intentionally provide no autoload alias or automatic activation.
Root must opt into each operation. There is no reset/start firmware, DSP, ALS,
charge or power-limit operation. No dependency on a private kernel name exists.

SCP matches NVDA8200 and validates resources/firmware dispatch ABI. Passive
`modprobe n1x_scp` provides root-readable debugfs status/log snapshots. Explicit
`enable_mailbox=1` permits bounded version/descriptor queries; the parameter
cannot change after loading. Slot 1 only, timeout/unmatched responses terminal.
It does not replace the existing SPI HID ALS sensor. Snapshot opens retain a
private copy after device removal. The observed firmware was 3.0.1.0.

Pluton matches NVDA8601 and exposes `/dev/n1x-pluton` root-only (0600).
Passive `modprobe n1x_pluton` does not send PCBI queries; enable_transport=1 is
explicit. Run upstream `platform/pluton/pluton-info.py --help`; typed queries
4/5/7 report firmware metadata, services and versions. Requests may initialize
the mailbox transport. No raw payload, external channel discovery, TPM, keys or
firmware update API is exposed. Complete reply bounds and terminal errors are
validated; Python does not retry uncertain requests.

PM serializes diagnostics with sleep and rejects suspended/removed-device
operations. It does not manage firmware power. Close readers before manual
removal. Exact reviewed sources passed status/version/descriptors and typed
queries before/after the local 41.8 ~78 s s2idle cycle; manual public adaptations
need fresh-boot/PM qualification, particularly other firmware/BIOS revisions.
See [validation](../VALIDATION.md) and per-component source-provenance.json.

Distribution review examples: [SCP PR2392](https://github.com/omacom/omarchy-pkgs/pull/2392)
and [Pluton PR2393](https://github.com/omacom/omarchy-pkgs/pull/2393).
