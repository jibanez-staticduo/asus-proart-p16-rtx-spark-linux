#!/usr/bin/env python3
"""Exercise upstream ALS parsing and scale reads against P16 descriptor units."""
import argparse
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'hid-sensor-als.c')
    args = parser.parse_args()
    source = args.source.read_text()
    state = source[source.index('enum {'):source.index('/* The order of usage ids')]
    reader = source[source.index('static int als_read_raw('):source.index('/* Channel write_raw handler */')]
    parse = source[source.index('static int als_parse_report('):
                   source.index('/* Function to initialize the processing for usage id */')]
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;typedef int32_t s32;typedef int64_t s64,aligned_s64;
#define BIT(n) (1UL<<(n))
#define BYTES_TO_BITS(n) ((n)*8)
#define BITS_PER_TYPE(type) (sizeof(type)*8)
#define HID_INPUT_REPORT 0
#define HID_USAGE_SENSOR_LIGHT_ILLUM 0x2004d1
#define HID_USAGE_SENSOR_LIGHT_COLOR_TEMPERATURE 0x2004d2
#define HID_USAGE_SENSOR_LIGHT_CHROMATICITY_X 0x2004d4
#define HID_USAGE_SENSOR_LIGHT_CHROMATICITY_Y 0x2004d5
#define IIO_CHAN_INFO_RAW 0
#define IIO_CHAN_INFO_SCALE 1
#define IIO_CHAN_INFO_OFFSET 2
#define IIO_CHAN_INFO_SAMP_FREQ 3
#define IIO_CHAN_INFO_HYSTERESIS 4
#define IIO_CHAN_INFO_HYSTERESIS_RELATIVE 5
#define IIO_VAL_INT 1
#define IIO_VAL_INT_PLUS_NANO 3
#define SENSOR_HUB_SYNC 1
#define dev_dbg(dev,...) ((void)(dev))
struct device {int unused;};
struct platform_device {struct device dev;};
struct hid_sensor_hub_device {u32 usage;};
struct hid_sensor_hub_callbacks {int unused;};
struct hid_sensor_common {struct hid_sensor_hub_device *hsdev;};
struct hid_sensor_hub_attribute_info {int report_id,logical_minimum,size,index,units,unit_expo;};
struct iio_scan_type {char format;unsigned realbits,storagebits;};
struct iio_chan_spec {int scan_index;struct iio_scan_type scan_type;};
struct iio_dev {void *private;};
#define iio_priv(dev) ((dev)->private)
''' + state + r'''
static const u32 als_usage_ids[]={0x2004d1,0x2004d1,0x2004d2,0x2004d4,0x2004d5};
static const struct iio_chan_spec als_channels[]={
 {.scan_index=0},{.scan_index=1},{.scan_index=2},{.scan_index=3},{.scan_index=4},{.scan_index=5}};
static bool only_color,no_fields;
static unsigned formatting_calls;
static int sensor_hub_input_get_attribute_info(struct hid_sensor_hub_device *hub,
 int report,u32 usage,u32 address,struct hid_sensor_hub_attribute_info *attr)
{
 assert(hub && report==HID_INPUT_REPORT && usage==0x200041);
 if(no_fields || (only_color && address==0x2004d1))return -ENODEV;
 int exponent=address==0x2004d1?-1:address==0x2004d2?0:-4;
 *attr=(struct hid_sensor_hub_attribute_info){.size=address==0x2004d1?4:2,
  .report_id=1,.unit_expo=exponent};return 0;
}
static int hid_sensor_format_scale(u32 usage,struct hid_sensor_hub_attribute_info *attr,
 int *whole,int *fraction)
{
 assert(usage==0x200041);formatting_calls++;
 *whole=attr->unit_expo==0?1:0;
 *fraction=attr->unit_expo==-1?100000000:attr->unit_expo==-4?100000:0;
 return IIO_VAL_INT_PLUS_NANO;
}
static int hid_sensor_power_state(struct hid_sensor_common *common,bool on)
{(void)common;(void)on;assert(false);return -EIO;}
static int sensor_hub_input_attr_get_raw_value(struct hid_sensor_hub_device *hub,
 u32 usage,u32 address,int report,int sync,bool sign)
{(void)hub;(void)usage;(void)address;(void)report;(void)sync;(void)sign;assert(false);return -EIO;}
static int unused_query(struct hid_sensor_common *common,int *v,int *v2)
{(void)common;(void)v;(void)v2;assert(false);return -EIO;}
#define hid_sensor_read_samp_freq_value unused_query
#define hid_sensor_read_raw_hyst_value unused_query
#define hid_sensor_read_raw_hyst_rel_value unused_query
''' + reader + parse + r'''
int main(void)
{
 struct platform_device pdev={};struct hid_sensor_hub_device hub={.usage=0x200041};
 struct als_state state;struct iio_dev device={.private=&state};
 const int expected_whole[]={0,0,1,0,0};
 const int expected_fraction[]={100000000,100000000,0,100000,100000};
 for(unsigned scenario=0;scenario<2;scenario++) {
  memset(&state,0,sizeof(state));state.common_attributes.hsdev=&hub;
  only_color=scenario==1;formatting_calls=0;
  assert(!als_parse_report(&pdev,&hub,hub.usage,&state));
  assert(state.num_channels==(only_color?3:5));
  assert(formatting_calls==(unsigned)state.num_channels);
  for(int i=0;i<state.num_channels;i++) {
   struct iio_chan_spec *channel=&state.channels[i];int whole=-1,fraction=-1;
   assert(channel->scan_index==i+(only_color?2:0));
   assert(als_read_raw(&device,channel,&whole,&fraction,IIO_CHAN_INFO_SCALE)==IIO_VAL_INT_PLUS_NANO);
   assert(whole==expected_whole[channel->scan_index]);
   assert(fraction==expected_fraction[channel->scan_index]);
  }
  int a,b;struct iio_chan_spec invalid={.scan_index=CHANNEL_SCAN_INDEX_MAX};
  assert(als_read_raw(&device,&invalid,&a,&b,IIO_CHAN_INFO_SCALE)==-EINVAL);
 }
 memset(&state,0,sizeof(state));formatting_calls=0;no_fields=true;
 assert(als_parse_report(&pdev,&hub,hub.usage,&state)==-ENODEV);
 assert(state.num_channels==0 && formatting_calls==0);
 puts("HID ALS descriptor-specific scales, sparse channels and invalid channels: PASS");
}
'''
    with tempfile.TemporaryDirectory(prefix='p16-als-scale-') as directory:
        code, binary = Path(directory) / 'scale.c', Path(directory) / 'scale'
        code.write_text(harness)
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-g', str(code), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    main()
