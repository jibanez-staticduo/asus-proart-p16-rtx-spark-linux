// SPDX-License-Identifier: GPL-2.0-only
#include <linux/module.h>
#include <linux/platform_device.h>

#include "n1x_adsp_client.h"

static int __init n1x_adsp_primary_check_init(void)
{
	struct device *dsp;
	int err;

	dsp = n1x_adsp_find_device(NULL, "NVDA8303");
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
MODULE_DESCRIPTION("MT8901 DSP primary message-buffer registration validation");
