/* SPDX-License-Identifier: GPL-2.0 */
/* Execute production MMIO callbacks against RAM, never physical hardware. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <limits.h>
#include <errno.h>
#include <sys/types.h>
#define __iomem
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define BIT(n) (1UL << (n))
#define min(a,b) ((a) < (b) ? (a) : (b))
#define HWMON_P_INPUT 1
#define HWMON_P_LABEL 2
#define HWMON_P_CAP 4
#define HWMON_P_MIN 8
#define HWMON_P_MAX 16
#define SPBM_SIZE 4096
typedef uint32_t u32;
typedef unsigned short umode_t;
enum hwmon_sensor_types { hwmon_power, hwmon_temp };
enum { hwmon_power_input, hwmon_power_label, hwmon_power_cap, hwmon_power_min, hwmon_power_max };
struct mutex { bool locked; };
static void mutex_lock(struct mutex *m) { assert(!m->locked); m->locked = true; }
static void mutex_unlock(struct mutex *m) { assert(m->locked); m->locked = false; }
struct n1x_priv { void *base; struct mutex lock; u32 owned[4]; unsigned long owned_mask; };
struct device { struct n1x_priv *data; };
typedef void *acpi_handle;
static int acpi_object;
static void *dev_get_drvdata(struct device *d) { return d->data; }
static u32 memory[1024];
static u32 ioread32(void *p) { return *(u32 *)p; }
static void iowrite32(u32 v, void *p) { *(u32 *)p = v; }
#define wmb() ((void)0)
struct attribute { int unused; };
struct device_attribute { struct attribute attr; };
struct sensor_device_attribute { struct device_attribute dev_attr; int index; };
#define to_sensor_dev_attr(a) ((struct sensor_device_attribute *)(a))
#define SENSOR_DEVICE_ATTR(n,m,s,w,i) struct sensor_device_attribute sensor_dev_attr_##n = { .index = (i) }
struct attribute_group { struct attribute **attrs; };
#define sysfs_emit(buf,fmt,...) snprintf(buf,4096,fmt,__VA_ARGS__)
#define ACPI_FAILURE(s) ((s) != 0)
static int acpi_evaluate_integer(acpi_handle d, const char *n, void *a, unsigned long long *v)
{ (void)n; (void)a; if (d != &acpi_object) return 1; *v = 2; return 0; }
#define dev_err_probe(dev,err,fmt) ((void)(dev), (void)(fmt), (err))
#define devm_ioremap(dev,addr,size) ((void)(dev), (void)(addr), (void)(size), memory)
static int devm_add_action_or_reset(struct device *d, void (*action)(void *), void *data)
{ (void)d; (void)action; (void)data; return 0; }
static const int n1x_power[2];
#include "n1x-mmio.h"
#include "n1x-probe-policy.h"

int main(void)
{
    struct n1x_priv p = { .base = memory };
    struct device dev = { .data = &p };
    long value;
    unsigned int i;
    for (i = 0; i < 4; i++) {
        unsigned int slot = n1x_limit_slots[i];
        unsigned int bound = n1x_limit_bounds[i] / 4;
        memory[bound] = 100;
        memory[bound + 1] = i < 2 ? 160000 : 250000;
        memory[0x120 / 4 + slot] = i < 2 ? 65000 : 120000;
        memory[0x160 / 4 + slot] = memory[0x120 / 4 + slot];
    }
    memory[0x818 / 4] = 3300;
    assert(n1x_mmio_init(&dev, &p, NULL) == -ENODEV);
    assert(n1x_mmio_init(&dev, &p, &acpi_object) == 0);
    for (i = 0; i < 4; i++) {
        int ch = 2 + N1X_FIRST_LIMIT + i;
        u32 slot = n1x_limit_slots[i];
        assert(n1x_mmio_write(&dev, hwmon_power, hwmon_power_cap, ch, -1000) == -EINVAL);
        assert(n1x_mmio_write(&dev, hwmon_power, hwmon_power_cap, ch, 35000001) == -EINVAL);
        assert(n1x_mmio_write(&dev, hwmon_power, hwmon_power_cap, ch, LONG_MAX) == -EINVAL);
        assert(n1x_mmio_write(&dev, hwmon_power, hwmon_power_cap, ch, 99000) == -EINVAL);
        assert(n1x_mmio_write(&dev, hwmon_power, hwmon_power_cap, ch, 200000000) == -EINVAL);
        assert(memory[0x100 / 4 + slot] == 0 && memory[0] == 0);
        assert(n1x_mmio_write(&dev, hwmon_power, hwmon_power_cap, ch, 35000000) == 0);
        assert(memory[0x100 / 4 + slot] == 35000 && memory[0] == 1);
        assert(n1x_mmio_read(&p, hwmon_power_cap, N1X_FIRST_LIMIT + i, &value) == 0);
        assert(value == (long)memory[0x160 / 4 + slot] * 1000);
        assert(n1x_mmio_write(&dev, hwmon_power, hwmon_power_cap, ch, 0) == 0);
        assert(memory[0x100 / 4 + slot] == 0 && !(p.owned_mask & BIT(i)));
        memory[0] = 0;
    }
    assert(n1x_mmio_write(&dev, hwmon_power, hwmon_power_cap, 2, 35000000) == -EOPNOTSUPP);
    assert(n1x_mmio_write(&dev, hwmon_temp, hwmon_power_cap, 11, 35000000) == -EOPNOTSUPP);
    assert(n1x_mmio_write(&dev, hwmon_power, hwmon_power_cap, 11, 35000000) == 0);
    assert(n1x_mmio_write(&dev, hwmon_power, hwmon_power_cap, 12, 35000000) == 0);
    memory[0x104 / 4] = 40000; /* External writer: do not reset its request. */
    n1x_reset_owned(&p);
    assert(memory[0x100 / 4] == 0 && memory[0x104 / 4] == 40000);
    memory[0x708 / 4] = 0; /* Firmware sentinels/invalid bounds reject probe. */
    {
        struct n1x_priv invalid = { 0 };
        struct device invalid_dev = { .data = &invalid };

        assert(n1x_mmio_init(&invalid_dev, &invalid, &acpi_object) == -ENODEV);
        assert(invalid.base == NULL);
        assert(n1x_probe_has_supported_sensors(true, false, true, false));
        assert(n1x_mmio_visible(&invalid, hwmon_power_input, 0) == 0);
        assert(!n1x_probe_has_supported_sensors(true, false, false, false));
        assert(!n1x_probe_has_supported_sensors(false, false, false, true));
    }
    p.base = NULL;
    assert(n1x_mmio_visible(&p, hwmon_power_cap, N1X_FIRST_LIMIT) == 0);
    assert(n1x_mmio_write(&dev, hwmon_power, hwmon_power_cap, 11, 35000000) == -EOPNOTSUPP);
    puts("PASS: production N1X cap callbacks reject invalid requests and restore owned overrides");
    return 0;
}
