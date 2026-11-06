// SPDX-License-Identifier: GPL-2.0-only
#include <linux/err.h>
#include <linux/module.h>
#include <linux/platform_device.h>

#include "n1x_adsp_client.h"

static int __init n1x_adsp_task_check_init(void)
{
	struct n1x_adsp_primary *primary;
	struct device *dsp;
	int err;

	dsp = bus_find_device_by_name(&platform_bus_type, NULL, "NVDA8303:00");
	if (!dsp)
		return -ENODEV;
	primary = n1x_adsp_primary_open(dsp);
	if (IS_ERR(primary)) {
		err = PTR_ERR(primary);
		goto put;
	}
	err = n1x_adsp_primary_close(primary);
	if (!err)
		dev_info(dsp, "primary task OPEN/HWFREE/CLOSE completed\n");
put:
	put_device(dsp);
	return err;
}

static void __exit n1x_adsp_task_check_exit(void)
{
}

module_init(n1x_adsp_task_check_init);
module_exit(n1x_adsp_task_check_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("P16 DSP primary OPEN/HWFREE/CLOSE validation without playback");
