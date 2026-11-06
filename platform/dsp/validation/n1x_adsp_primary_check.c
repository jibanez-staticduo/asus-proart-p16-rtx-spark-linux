// SPDX-License-Identifier: GPL-2.0-only
#include <linux/module.h>
#include <linux/platform_device.h>

#include "n1x_adsp_client.h"

static int __init n1x_adsp_primary_check_init(void)
{
	struct device *dsp;
	int err;

	dsp = bus_find_device_by_name(&platform_bus_type, NULL, "NVDA8303:00");
	if (!dsp)
		return -ENODEV;
	err = n1x_adsp_prepare_primary(dsp);
	if (!err)
		dev_info(dsp, "primary playback message buffers registered\n");
	put_device(dsp);
	return err;
}

static void __exit n1x_adsp_primary_check_exit(void)
{
}

module_init(n1x_adsp_primary_check_init);
module_exit(n1x_adsp_primary_check_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("P16 DSP primary message-buffer registration validation");
