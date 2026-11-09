#!/usr/bin/env python3
"""Test the actual decoded input-body structure and unaligned wire parser."""
from pathlib import Path
import subprocess
import tempfile


def main():
    source = Path(__file__).with_name('spi-hid-core.c').read_text()
    start = source.index('struct spi_hid_input_body {')
    structure = source[start:source.index('\n};', start) + 3]
    start = source.index('static void spi_hid_populate_input_body(')
    function = source[start:source.index('\n}\n', start) + 3]
    code = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
typedef uint8_t u8;typedef uint16_t u16;
static u16 get_unaligned_le16(const void *data)
{const u8 *p=data;return (u16)p[0]|(u16)p[1]<<8;}
''' + structure + function + r'''
int main(void)
{
 u8 storage[5]={0};u8 *wire=storage+1;
 struct spi_hid_input_body body;
 for(unsigned value=0;value<=65535;value++) {
  wire[0]=0x17;wire[1]=value&255;wire[2]=value>>8;wire[3]=0x23;
  spi_hid_populate_input_body(wire,&body);
  assert(body.input_report_type==0x17 && body.content_id==0x23 && body.content_len==value);
 }
 puts("SPI HID: unaligned little-endian wire lengths decode into native u16 for all 65536 values: PASS");
}
'''
    with tempfile.TemporaryDirectory(prefix='spi-hid-input-body-') as temporary:
        test, binary = Path(temporary) / 'body.c', Path(temporary) / 'body'
        test.write_text(code)
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', str(test), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=30)


if __name__ == '__main__':
    main()
