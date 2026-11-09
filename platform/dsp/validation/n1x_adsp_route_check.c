// SPDX-License-Identifier: GPL-2.0-only
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/property.h>
#include "n1x_adsp_client.h"

static int n1x_adsp_sram_check(struct device *asd)
{
	static const char * const properties[] = {
		"acpi-asd-sram-size", "acpi-asd-sram-cpu-view",
		"acpi-asd-sram-vad-view", "acpi-asd-sram-adsp-view",
	};
	struct fwnode_handle *node;
	u32 count, values[ARRAY_SIZE(properties)];
	unsigned int i;
	int err;

	err = device_property_read_u32(asd, "acpi-asd-sram-resource-list", &count);
	if (err)
		return err;
	if (count != 1)
		return -ENODEV;
	node = device_get_named_child_node(asd, "acpi-asd-afe-sram-subproperties");
	if (!node)
		return -ENODEV;
	for (i = 0; i < ARRAY_SIZE(properties); i++) {
		err = fwnode_property_read_u32(node, properties[i], &values[i]);
		if (err)
			goto put;
		if (!values[i] || !IS_ALIGNED(values[i], 16)) {
			err = -ENODEV;
			goto put;
		}
	}
	for (i = 1; i < ARRAY_SIZE(values); i++)
		if (values[0] - 1 > U32_MAX - values[i]) {
			err = -ERANGE;
			goto put;
		}
	dev_info(asd, "AFE SRAM properties checked: size=%#x cpu=%#x vad=%#x adsp=%#x\n",
		 values[0], values[1], values[2], values[3]);
put:
	fwnode_handle_put(node);
	return err;
}

static int __init n1x_adsp_route_check_init(void)
{
	static const char * const names[] = {
		"acpi-asd-speaker-subproperties",
		"acpi-asd-headphone-subproperties",
	};
	struct device *asd;
	u32 version, mask, route;
	unsigned int i;
	int err;

	asd = n1x_adsp_find_device(NULL, "NVDA9022");
	if (!asd)
		return -ENODEV;
	err = device_property_read_u32(asd, "acpi-asd-hw-ver", &version);
	if (err)
		goto put;
	err = device_property_read_u32(asd, "acpi-asd-endpoint-list", &mask);
	if (err)
		goto put;
	if (version != 1 || (mask & 5) != 5) {
		err = -ENODEV;
		goto put;
	}
	for (i = 0; i < ARRAY_SIZE(names); i++) {
		struct fwnode_handle *node = device_get_named_child_node(asd, names[i]);

		if (!node) {
			err = -ENODEV;
			goto put;
		}
		err = fwnode_property_read_u32(node, "acpi-asd-endpoint-path-config", &route);
		fwnode_handle_put(node);
		if (err)
			goto put;
		if (route != 2) {
			err = -ENODEV;
			goto put;
		}
	}
	dev_info(asd, "primary playback firmware routes checked: speaker=2 headphone=2\n");
	err = n1x_adsp_sram_check(asd);
put:
	put_device(asd);
	return err;
}

static void __exit n1x_adsp_route_check_exit(void)
{
}

module_init(n1x_adsp_route_check_init);
module_exit(n1x_adsp_route_check_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("MT8901 DSP firmware-route and SRAM-property validation; no firmware requests");
