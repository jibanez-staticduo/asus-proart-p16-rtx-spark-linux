// SPDX-License-Identifier: GPL-2.0-only
/* Experimental P16 MT8901 secure boot and ready-notification driver.
 * Power management adapted from MediaTek's sof/mt8901/mt8901.c:
 * Copyright (c) 2026 MediaTek Inc. (GPL-2.0-only OR BSD-3-Clause).
 * The OEM audio/compute service ABI is not implemented here.
 */
#include <crypto/sha2.h>
#include <linux/acpi.h>
#include <linux/capability.h>
#include <linux/completion.h>
#include <linux/dma-map-ops.h>
#include <linux/dma-mapping.h>
#include <linux/dmi.h>
#include <linux/firmware.h>
#include <linux/hex.h>
#include <linux/interrupt.h>
#include <linux/io.h>
#include <linux/iommu.h>
#include <linux/iommu-dma.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/platform_device.h>
#include <linux/pm_runtime.h>
#include <linux/soc/mediatek/mtk-pwrap.h>
#include <linux/unaligned.h>
#include "n1x_sroot_client.h"

#define ADSP_RX_OFFSET 0x20
#define ADSP_CONFIG_OFFSET 0x3a0
#define ADSP_RX_BYTES 0x120
#define ADSP_OUT_STATUS 0x104
#define ADSP_OUT_CLEAR 0x10c

static bool enable_boot;
module_param(enable_boot, bool, 0400);
MODULE_PARM_DESC(enable_boot, "Permit explicit one-shot secure boot; experimental, disabled by default");

struct n1x_adsp_target {
	const char *hid, *firmware, *sha256;
	u32 image_id, config, shared_end, mailbox, dram_bytes, firmware_bytes;
};

static const struct n1x_adsp_target targets[] = {
	{"NVDA8301", "nvidia/n1x/hifi5-0-mt8901-signed.bin",
	 "350e0d40b2f8a6dfb486d8dfd3362fb5b02ceb90da372176b008498adbca8cc4",
	 0xc0304, 0x1a000000, 0x1a160000, 0x1a350000, 24U << 20, 631436},
	{"NVDA8302", "nvidia/n1x/hifi5-1-mt8901-signed.bin",
	 "517ef0d447ca80b7cb88ec0589cda816d9b20c844800ca95ccf7cbe96fc7d40e",
	 0xc0305, 0x1a001000, 0x1a1a0000, 0x1a360000, 24U << 20, 157996},
	{"NVDA8303", "nvidia/n1x/hifi1-0-mt8901-signed.bin",
	 "434660b36f4e21cb794bc50e10a47b91882710cbe90cb60620111a2a753c5b94",
	 0xc0303, 0x1a400000, 0x1a5e0000, 0x1a750000, 16U << 20, 363816},
};

struct n1x_adsp {
	struct device *dev;
	const struct n1x_adsp_target *target;
	void *pwrap, *dram;
	dma_addr_t dma;
	void __iomem *shared, *mailbox;
	struct mutex lock;
	struct completion ready;
	struct n1x_sroot_client_result loader;
	u32 notifications;
	u16 last_command;
	int error;
	bool attempted, published, boot_ready;
};

static int n1x_adsp_group_member(struct device *member, void *data)
{
	return member == data ? 0 : -EOPNOTSUPP;
}

static int n1x_adsp_dma_gate(struct device *dev)
{
	struct acpi_device *adev = ACPI_COMPANION(dev);
	struct iommu_domain *domain = iommu_get_domain_for_dev(dev);
	struct iommu_fwspec *spec = dev_iommu_fwspec_get(dev);
	struct iommu_group *group;
	int err;

	if (!adev || !adev->flags.cca_seen || !adev->flags.coherent_dma ||
	    !dev_is_dma_coherent(dev) || !dev->dma_mask || !domain ||
	    !iommu_is_dma_domain(domain) || !use_dma_iommu(dev) ||
	    !spec || spec->num_ids < 1)
		return -EOPNOTSUPP;
	group = iommu_group_get(dev);
	if (!group)
		return -EOPNOTSUPP;
	err = iommu_group_for_each_dev(group, dev, n1x_adsp_group_member);
	iommu_group_put(group);
	return err;
}

static irqreturn_t n1x_adsp_irq(int irq, void *data)
{
	struct n1x_adsp *dsp = data;
	u8 packet[ADSP_RX_BYTES];
	u32 pending, bit;
	u16 command;

	if (!READ_ONCE(dsp->published))
		return IRQ_NONE;
	pending = readl(dsp->mailbox + ADSP_OUT_STATUS);
	if (!pending)
		return IRQ_NONE;
	/* OEM ISR acknowledges the first pending mailbox bit before its DPC
	 * copies the 0x120-byte receive buffer. Do not clear unrelated bits.
	 */
	bit = BIT(__ffs(pending));
	writel(bit, dsp->mailbox + ADSP_OUT_CLEAR);
	memcpy_fromio(packet, dsp->shared + ADSP_RX_OFFSET, sizeof(packet));
	command = get_unaligned_le16(packet + 22);
	WRITE_ONCE(dsp->last_command, command);
	WRITE_ONCE(dsp->notifications, dsp->notifications + 1);
	/* Release the firmware's receive slot, as in ADSP's OEM DPC. */
	writel(0, dsp->shared + 0x10);
	if ((packet[19] >> 4) == 2 && command == 0xf002) {
		WRITE_ONCE(dsp->boot_ready, true);
		complete(&dsp->ready);
	}
	return IRQ_HANDLED;
}

static int n1x_adsp_boot(struct n1x_adsp *dsp)
{
	typeof(n1x_sroot_upload_dsp_v1) *upload;
	const struct firmware *firmware;
	u8 digest[SHA256_DIGEST_SIZE], expected[SHA256_DIGEST_SIZE];
	u64 end;
	u32 pending, previous_dma, previous_bytes;
	int err;

	if (!enable_boot || !capable(CAP_SYS_RAWIO))
		return -EPERM;
	if (dsp->attempted)
		return -EALREADY;
	/* HiFi5-1 shares its control block with HiFi5-0 in the OEM driver.
	 * Keep the experimental secondary-core boot behind primary readiness.
	 */
	if (dsp->target->image_id == 0xc0305) {
		struct device *primary = bus_find_device_by_name(&platform_bus_type,
							NULL, "NVDA8301:00");
		struct n1x_adsp *first = NULL;
		bool ready;

		ready = false;
		if (primary) {
			device_lock(primary);
			if (primary->driver && !strcmp(primary->driver->name, "n1x-adsp"))
				first = dev_get_drvdata(primary);
			ready = first && READ_ONCE(first->boot_ready) && READ_ONCE(first->loader.accepted);
			device_unlock(primary);
		}
		put_device(primary);
		if (!ready)
			return -EHOSTDOWN;
	}
	/* Old NPU modules do not export this broker. Fail before power/DMA/MMIO,
	 * without loading or replacing the working NPU module automatically.
	 */
	upload = symbol_get(n1x_sroot_upload_dsp_v1);
	if (!upload)
		return -EOPNOTSUPP;
	err = request_firmware_direct(&firmware, dsp->target->firmware, dsp->dev);
	if (err)
		goto put_symbol;
	if (firmware->size != dsp->target->firmware_bytes) {
		err = -EBADMSG;
		goto release;
	}
	sha256(firmware->data, firmware->size, digest);
	if (hex2bin(expected, dsp->target->sha256, sizeof(expected)) ||
	    memcmp(digest, expected, sizeof(digest))) {
		err = -EBADMSG;
		goto release;
	}
	err = n1x_adsp_dma_gate(dsp->dev);
	if (err)
		goto release;
	err = dma_set_mask_and_coherent(dsp->dev, DMA_BIT_MASK(32));
	if (err)
		goto release;
	err = pm_runtime_resume_and_get(dsp->dev);
	if (err < 0)
		goto release;
	dsp->dram = dma_alloc_coherent(dsp->dev, dsp->target->dram_bytes,
					&dsp->dma, GFP_KERNEL);
	if (!dsp->dram) {
		err = -ENOMEM;
		goto put_pm;
	}
	end = (u64)dsp->dma + dsp->target->dram_bytes - 1;
	if (dsp->dma < 0xe0000000 || end > 0xffffffff || (dsp->dma & 255)) {
		err = -ERANGE;
		goto free;
	}
	/* Powered-down SRAM is uninitialized on this P16, not zero-filled.
	 * The OEM overwrites these two config words after power-up. Preserve a
	 * pending notification or a valid existing DMA-window descriptor, rather
	 * than treating arbitrary SRAM contents as a live firmware publication.
	 */
	pending = readl(dsp->mailbox + ADSP_OUT_STATUS);
	previous_dma = readl(dsp->shared + ADSP_CONFIG_OFFSET);
	previous_bytes = readl(dsp->shared + ADSP_CONFIG_OFFSET + 8);
	if (pending || (previous_dma >= 0xe0000000 && !(previous_dma & 255) &&
		       previous_bytes && (u64)previous_dma + previous_bytes <= 0x100000000ULL)) {
		dev_info(dsp->dev, "boot deferred: pending=%#x config0=%#x config8=%#x\n",
			 pending, previous_dma, previous_bytes);
		err = -EBUSY;
		goto free;
	}
	memset(dsp->dram, 0, dsp->target->dram_bytes);
	if (!try_module_get(THIS_MODULE)) {
		err = -ENODEV;
		goto free;
	}
	reinit_completion(&dsp->ready);
	dma_wmb();
	/* From this point no failure may free DMA or power down this DSP. */
	dsp->attempted = true;
	WRITE_ONCE(dsp->published, true);
	writel(lower_32_bits(dsp->dma), dsp->shared + ADSP_CONFIG_OFFSET);
	writel(dsp->target->dram_bytes, dsp->shared + ADSP_CONFIG_OFFSET + 8);
	err = upload(dsp->target->image_id, firmware->data, firmware->size, &dsp->loader);
	if (!err && !wait_for_completion_timeout(&dsp->ready, msecs_to_jiffies(30000)))
		err = -ETIMEDOUT;
	dsp->error = err;
	dev_info(dsp->dev,
		"secure boot error=%d accepted=%u ready=%u last_command=%#x; DMA/power retained until reboot\n",
		err, dsp->loader.accepted, dsp->boot_ready, dsp->last_command);
	goto release;
free:
	dma_free_coherent(dsp->dev, dsp->target->dram_bytes, dsp->dram, dsp->dma);
	dsp->dram = NULL;
	dsp->dma = 0;
put_pm:
	pm_runtime_put(dsp->dev);
release:
	release_firmware(firmware);
put_symbol:
	symbol_put(n1x_sroot_upload_dsp_v1);
	return err;
}

static ssize_t boot_store(struct device *dev, struct device_attribute *attribute,
			 const char *buffer, size_t count)
{
	struct n1x_adsp *dsp = dev_get_drvdata(dev);
	int err;

	if (!sysfs_streq(buffer, "start"))
		return -EINVAL;
	mutex_lock(&dsp->lock);
	err = n1x_adsp_boot(dsp);
	if (err && !dsp->attempted)
		dsp->error = err;
	mutex_unlock(&dsp->lock);
	return err ?: count;
}
static DEVICE_ATTR_WO(boot);

static ssize_t status_show(struct device *dev, struct device_attribute *attribute, char *buffer)
{
	struct n1x_adsp *dsp = dev_get_drvdata(dev);
	ssize_t bytes;

	mutex_lock(&dsp->lock);
	bytes = sysfs_emit(buffer,
		"image=%#x attempted=%u published=%u loader_published=%u loader_accepted=%u ready=%u error=%d notifications=%u last_command=%#x services=0\n",
		dsp->target->image_id, dsp->attempted, dsp->published, dsp->loader.published,
		dsp->loader.accepted, dsp->boot_ready, dsp->error, dsp->notifications, dsp->last_command);
	mutex_unlock(&dsp->lock);
	return bytes;
}
static DEVICE_ATTR_RO(status);

/* Explicit root-only diagnostic, separate from metadata-only status. Reading
 * these mailbox/config words never acknowledges a notification or publishes
 * a DMA window. Access is bracketed by the existing SSPM runtime power path.
 */
static ssize_t inspect_show(struct device *dev, struct device_attribute *attribute, char *buffer)
{
	struct n1x_adsp *dsp = dev_get_drvdata(dev);
	ssize_t bytes;
	int err;

	if (!capable(CAP_SYS_RAWIO))
		return -EPERM;
	mutex_lock(&dsp->lock);
	if (dsp->published) {
		bytes = -EBUSY;
		goto unlock;
	}
	err = pm_runtime_resume_and_get(dev);
	if (err < 0) {
		bytes = err;
		goto unlock;
	}
	bytes = sysfs_emit(buffer, "out=%#x config0=%#x config4=%#x config8=%#x configc=%#x\n",
		readl(dsp->mailbox + ADSP_OUT_STATUS),
		readl(dsp->shared + ADSP_CONFIG_OFFSET),
		readl(dsp->shared + ADSP_CONFIG_OFFSET + 4),
		readl(dsp->shared + ADSP_CONFIG_OFFSET + 8),
		readl(dsp->shared + ADSP_CONFIG_OFFSET + 12));
	pm_runtime_mark_last_busy(dev);
	pm_runtime_put_autosuspend(dev);
unlock:
	mutex_unlock(&dsp->lock);
	return bytes;
}
static struct device_attribute dev_attr_inspect = __ATTR(inspect, 0400, inspect_show, NULL);

static struct attribute *n1x_adsp_attrs[] = {
	&dev_attr_boot.attr, &dev_attr_status.attr, &dev_attr_inspect.attr, NULL
};
ATTRIBUTE_GROUPS(n1x_adsp);

static int n1x_adsp_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct acpi_device *adev = ACPI_COMPANION(dev);
	struct acpi_buffer path = {ACPI_ALLOCATE_BUFFER, NULL};
	struct resource *config, *shared, *mailbox, tail;
	struct n1x_adsp *dsp;
	unsigned int i;
	int irq, err;

	if (!dmi_match(DMI_PRODUCT_NAME, "ProArt P16 H7607BA") ||
	    !dmi_match(DMI_BIOS_VERSION, "H7607BA.300") || !adev)
		return -ENODEV;
	for (i = 0; i < ARRAY_SIZE(targets); i++)
		if (!strcmp(acpi_device_hid(adev), targets[i].hid))
			break;
	if (i == ARRAY_SIZE(targets))
		return -ENODEV;
	config = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	shared = platform_get_resource(pdev, IORESOURCE_MEM, 4);
	mailbox = platform_get_resource(pdev, IORESOURCE_MEM, 5);
	if (!config || !shared || !mailbox || config->start != targets[i].config ||
	    shared->end != (u64)targets[i].shared_end - 1 || resource_size(shared) < 0x400 ||
	    mailbox->start != targets[i].mailbox || resource_size(mailbox) != 0x1000)
		return -ENODEV;
	dsp = devm_kzalloc(dev, sizeof(*dsp), GFP_KERNEL);
	if (!dsp)
		return -ENOMEM;
	dsp->dev = dev;
	dsp->target = &targets[i];
	mutex_init(&dsp->lock);
	init_completion(&dsp->ready);
	tail = *shared;
	tail.start = tail.end + 1 - 0x400;
	dsp->shared = devm_ioremap_resource(dev, &tail);
	if (IS_ERR(dsp->shared))
		return PTR_ERR(dsp->shared);
	dsp->mailbox = devm_platform_ioremap_resource(pdev, 5);
	if (IS_ERR(dsp->mailbox))
		return PTR_ERR(dsp->mailbox);
	irq = platform_get_irq(pdev, 0);
	if (irq < 0)
		return irq;
	err = devm_request_irq(dev, irq, n1x_adsp_irq, 0, dev_name(dev), dsp);
	if (err)
		return err;
	if (ACPI_FAILURE(acpi_get_name(adev->handle, ACPI_FULL_PATHNAME, &path)))
		return -EINVAL;
	dsp->pwrap = mtk_pwrap_dev_probe(path.pointer);
	kfree(path.pointer);
	if (!dsp->pwrap)
		return -ENODEV;
	platform_set_drvdata(pdev, dsp);
	pm_runtime_set_autosuspend_delay(dev, 100);
	pm_runtime_use_autosuspend(dev);
	err = pm_runtime_set_active(dev);
	if (err) {
		pm_runtime_dont_use_autosuspend(dev);
		mtk_pwrap_dev_remove(dsp->pwrap);
		return err;
	}
	pm_runtime_enable(dev);
	pm_runtime_mark_last_busy(dev);
	pm_runtime_idle(dev);
	dev_info(dev, "secure-boot experiment available; enable_boot=%u; no firmware started\n", enable_boot);
	return 0;
}

static void n1x_adsp_remove(struct platform_device *pdev)
{
	struct n1x_adsp *dsp = platform_get_drvdata(pdev);
	int err = pm_runtime_resume_and_get(&pdev->dev);

	/* Published instances pin the module and suppress manual sysfs unbind. */
	pm_runtime_dont_use_autosuspend(&pdev->dev);
	pm_runtime_disable(&pdev->dev);
	mtk_pwrap_dev_remove(dsp->pwrap);
	if (err >= 0)
		pm_runtime_put_noidle(&pdev->dev);
}

static int n1x_adsp_runtime_suspend(struct device *dev)
{
	struct n1x_adsp *dsp = dev_get_drvdata(dev);

	if (READ_ONCE(dsp->published))
		return -EBUSY;
	return mtk_pwrap_dev_suspend(dsp->pwrap, 0);
}

static int n1x_adsp_runtime_resume(struct device *dev)
{
	struct n1x_adsp *dsp = dev_get_drvdata(dev);

	return mtk_pwrap_dev_resume(dsp->pwrap, 0);
}

static const struct dev_pm_ops n1x_adsp_pm = {
	SYSTEM_SLEEP_PM_OPS(pm_runtime_force_suspend, pm_runtime_force_resume)
	RUNTIME_PM_OPS(n1x_adsp_runtime_suspend, n1x_adsp_runtime_resume, NULL)
};

static const struct acpi_device_id n1x_adsp_ids[] = {
	{"NVDA8301"}, {"NVDA8302"}, {"NVDA8303"}, {}
};
MODULE_DEVICE_TABLE(acpi, n1x_adsp_ids);

static struct platform_driver n1x_adsp_driver = {
	.probe = n1x_adsp_probe,
	.remove = n1x_adsp_remove,
	.driver = {
		.name = "n1x-adsp",
		.acpi_match_table = n1x_adsp_ids,
		.pm = pm_ptr(&n1x_adsp_pm),
		.dev_groups = n1x_adsp_groups,
		.suppress_bind_attrs = true,
	}
};
module_platform_driver(n1x_adsp_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Experimental P16 MT8901 secure DSP boot; no audio/compute services");
MODULE_VERSION("0.1.0");
