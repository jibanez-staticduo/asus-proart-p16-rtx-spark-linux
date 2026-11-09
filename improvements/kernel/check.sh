#!/usr/bin/env bash
# Source harnesses only: does not install a kernel or access hardware.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_tree=$(realpath -- "${1:?Usage: check.sh /path/to/prepared/linux-7.3}")
for test in "$here"/test_*.py; do
  if [[ $test == */test_gate.py ]]; then
    python3 "$test" --processor-source "$source_tree/drivers/acpi/processor_idle.c" \
      --pci-source "$source_tree/drivers/pci/hotplug/pciehp_core.c"
  elif [[ $test == */test_fallback_latency.py ]]; then
    python3 "$test" "$source_tree/drivers/acpi/processor_idle.c" \
      "$source_tree/drivers/cpuidle/cpuidle.c" --sanitize
  else
    python3 "$test" "$source_tree"
  fi
done
