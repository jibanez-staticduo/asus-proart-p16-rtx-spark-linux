/* SPDX-License-Identifier: GPL-2.0 */
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>

#include "n1x-probe-policy.h"

int main(void)
{
	assert(n1x_probe_has_supported_sensors(true, false, false, true));
	assert(!n1x_probe_has_supported_sensors(true, false, false, false));
	assert(!n1x_probe_has_supported_sensors(false, false, false, false));
	assert(!n1x_probe_has_supported_sensors(false, false, false, true));
	assert(n1x_probe_has_supported_sensors(false, false, true, false));
	assert(n1x_probe_has_supported_sensors(false, true, false, false));
	puts("PASS: missing ACPI zones require validated P16 MMIO fallback");
	return 0;
}
