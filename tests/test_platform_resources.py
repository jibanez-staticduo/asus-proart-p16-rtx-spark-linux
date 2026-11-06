"""Exercise driver probes with relocated resources and firmware-sized SRAM."""
import ctypes
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PlatformResourceTests(unittest.TestCase):
    def test_actual_probe_checks_accept_relocation_and_reject_short_resources(self):
        dsp = (ROOT / 'platform/dsp/n1x_adsp.c').read_text()
        pluton = (ROOT / 'platform/pluton/n1x_pluton.c').read_text()
        scp = (ROOT / 'platform/scp/n1x_scp.c').read_text()

        def prefix(source, name, allocation):
            start = source.index('static int ' + name + '(')
            end = source.index(allocation, start)
            return source[start:end] + '\treturn 0;\n}\n'

        target = dsp[dsp.index('struct n1x_adsp_target {'):dsp.index('struct n1x_adsp_primary {')]
        targets = dsp[dsp.index('static const struct n1x_adsp_target targets[]'):dsp.index('struct n1x_adsp {')]
        scp_state = scp[scp.index('struct n1x_scp {'):scp.index('static int n1x_scp_query(')]
        constants = '\n'.join(line for line in scp.splitlines() if line.startswith('#define N1X_'))
        code = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;
typedef uint64_t resource_size_t;
#define __iomem
#define IORESOURCE_MEM 0
#define GFP_KERNEL 0
#define ACPI_ALLOCATE_BUFFER 0
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define ADSP_OUT_CLEAR 0x10c
#define N1X_ROT_ERROR_CLEAR1 0xd0
#define ACPI_FAILURE(x) ((x)!=0)
#define ACPI_COMPANION(d) (&mock_adev)
#define ACPI_HANDLE(d) (&mock_adev)
#define IS_ERR(p) ((uintptr_t)(p)>=(uintptr_t)-4095)
#define PTR_ERR(p) ((int)(intptr_t)(p))
struct acpi_device {const char *hid;};static struct acpi_device mock_adev={"NVDA8301"};
struct acpi_buffer {size_t length;void *pointer;};
struct device {int unused;};struct platform_device {struct device dev;};
struct resource {uint64_t start,end;unsigned flags;};
struct mutex {int unused;};struct dentry;struct n1x_adsp;
static struct resource resources[6];static bool present[6];static unsigned long long revision;
static unsigned char sram_memory[0x80000];
static bool enable_mailbox=true,firmware_valid=true;
static unsigned mappings;
static uint64_t resource_size(const struct resource *r) {return r->end-r->start+1;}
static struct resource *platform_get_resource(struct platform_device *p,int type,unsigned i)
{(void)p;assert(type==IORESOURCE_MEM && i<6);return present[i]?&resources[i]:NULL;}
static const char *acpi_device_hid(struct acpi_device *a) {return a->hid;}
static int acpi_evaluate_integer(void *handle,const char *key,void *args,unsigned long long *out)
{assert(handle==&mock_adev && !strcmp(key,"_HRV") && !args);*out=revision;return 0;}
#define dev_err_probe(dev,err,...) ((void)(dev),(err))
''' + target + targets + constants + '\n' + scp_state + r'''
static struct n1x_scp scp_instance;
static void *devm_kzalloc(struct device *dev,size_t bytes,int flags)
{(void)dev;assert(bytes==sizeof(scp_instance) && flags==GFP_KERNEL);memset(&scp_instance,0,bytes);return &scp_instance;}
static void mutex_init(struct mutex *m) {(void)m;}
static void *devm_ioremap_resource(struct device *dev,struct resource *r)
{(void)dev;assert(r);mappings++;return sram_memory;}
static int n1x_log_header(struct n1x_scp *scp,u32 *count,u16 *capacity)
{assert(scp->size>=N1X_LOG_OFFSET+N1X_LOG_HEADER_SIZE+N1X_LOG_RECORD_SIZE);*count=*capacity=1;return 0;}
static void memcpy_fromio(void *out,const void *in,size_t bytes)
{assert(in==sram_memory+0x41ce && bytes==16);const u8 dispatch[]={0x63,0x88,0x2a,0x46,0x39,0x46,0x02,0x20,0x03,0xf0,0x5b,0xfe,0xb4,0xf8,0x02,0x80};memcpy(out,dispatch,bytes);if(!firmware_valid)((u8 *)out)[0]^=1;}
''' + prefix(dsp, 'n1x_adsp_probe', '\tdsp = devm_kzalloc') + \
            prefix(pluton, 'n1x_pluton_probe', '\tp = kzalloc_obj') + \
            prefix(scp, 'n1x_scp_probe', '\tplatform_set_drvdata') + r'''
static void reset(uint64_t base)
{for(unsigned i=0;i<6;i++){present[i]=true;resources[i]=(struct resource){base+i*0x100000,base+i*0x100000+0xfff,0};}
 resources[1].end=resources[1].start+sizeof(sram_memory)-1;revision=1;mappings=0;firmware_valid=true;}
int main(void)
{
 struct platform_device p={0};
 for(unsigned relocation=0;relocation<8;relocation++){
  uint64_t base=0x20000000ULL+relocation*0x10000000ULL;reset(base);
  assert(!n1x_adsp_probe(&p));assert(!n1x_scp_probe(&p) && mappings==3);
  resources[1]=resources[0];resources[1].end=resources[1].start+15;
  assert(!n1x_pluton_probe(&p));
 }
 for(unsigned i=0;i<3;i++){
  unsigned slot=(unsigned[]){0,4,5}[i];reset(0x30000000);present[slot]=false;assert(n1x_adsp_probe(&p)<0);
  reset(0x30000000);resources[slot].end=resources[slot].start+(unsigned[]){3,0x3ff,0x10f}[i]-1;
  assert(n1x_adsp_probe(&p)<0);
 }
 reset(0x30000000);resources[1].end=resources[1].start+N1X_LOG_OFFSET;assert(n1x_scp_probe(&p)<0 && !mappings);
 for(unsigned slot=3;slot<5;slot++){
  reset(0x30000000);present[slot]=false;assert(n1x_scp_probe(&p)<0);
  reset(0x30000000);resources[slot].end=resources[slot].start+N1X_MBOX_OUT_CLEAR+2;assert(n1x_scp_probe(&p)<0);
 }
 reset(0x30000000);firmware_valid=false;assert(n1x_scp_probe(&p)<0 && mappings==1);
 reset(0x30000000);resources[1]=resources[0];resources[1].end=resources[1].start+15;
 resources[0].end=resources[0].start+0xd2;assert(n1x_pluton_probe(&p)<0);
 reset(0x30000000);assert(n1x_pluton_probe(&p)<0); /* wrong alias */
 reset(0x30000000);revision=2;assert(n1x_pluton_probe(&p)<0);
}
'''
        with tempfile.TemporaryDirectory() as temporary:
            source, binary = Path(temporary) / 'resources.c', Path(temporary) / 'resources'
            source.write_text(code)
            subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                            '-Wno-unused-variable', '-Wno-missing-field-initializers',
                            '-fsanitize=address,undefined', str(source), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_signed_envelope_bounds_without_firmware_build_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, binary = Path(temporary) / 'image.c', Path(temporary) / 'image.so'
            source.write_text('#include "n1x_sroot_firmware.h"\n'
                              'int validate(const unsigned char *p,size_t n,unsigned id) '
                              '{return n1x_sroot_validate_image(p,n,id);}\n')
            subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                            '-I' + str(ROOT / 'platform/dsp'), str(source), '-o', str(binary)], check=True)
            check = ctypes.CDLL(str(binary)).validate
            check.argtypes = ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint
            check.restype = ctypes.c_int
            for size in (256, 8192):
                data = bytearray(size)
                data[:3] = b'\x59\x00\x00'
                struct.pack_into('<3I', data, 3, 1, 2, 0xefbeadde)
                struct.pack_into('<8I', data, 15, 0xefbeadde, 0xc0304, 0, 0, 0, 80, 0, 0)
                struct.pack_into('<8I', data, 47, 0xefbeadde, 0xc0304, 1, 80, 0, size-83, 0, 0)
                struct.pack_into('<I', data, 79, 0xefbeadde)
                self.assertEqual(check(bytes(data), len(data), 0xc0304), 0)
                for length in range(len(data)):
                    self.assertNotEqual(check(bytes(data), length, 0xc0304), 0)
                for offset in (3, 7, 11, 15, 19, 27, 31, 35, 47, 51, 59, 63, 67, 79):
                    bad = data.copy()
                    struct.pack_into('<I', bad, offset, 0xffffffff)
                    self.assertNotEqual(check(bytes(bad), len(bad), 0xc0304), 0)
                self.assertNotEqual(check(bytes(data), len(data), 0xc0305), 0)
                data[-1] ^= 1
                self.assertEqual(check(bytes(data), len(data), 0xc0304), 0)
                # Payload authenticity is checked by secure firmware, not this bounds helper.

    def test_sensor_quirk_matches_controller_ids_without_host_identity(self):
        source = (ROOT / 'platform/sensorhub/spi-hid-core.c').read_text()
        expression = source.split('shid->input_stream_only = ', 1)[1].split(';', 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            source, binary = Path(temporary) / 'quirk.c', Path(temporary) / 'quirk.so'
            source.write_text('''#include <stdbool.h>
#include <string.h>
struct device {const char *hid;};
struct hid_device {unsigned vendor,product;};
#define ACPI_COMPANION(dev) ((dev)->hid)
static bool acpi_dev_hid_uid_match(const char *hid,const char *expected,void *uid)
{(void)uid;return !strcmp(hid,expected);}
int match(const char *id,unsigned vendor,unsigned product)
{struct device device={id},*dev=&device;struct hid_device h={vendor,product},*hid=&h;
return ''' + expression + ';}\n')
            subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                            str(source), '-o', str(binary)], check=True)
            match = ctypes.CDLL(str(binary)).match
            match.argtypes = ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint
            self.assertEqual(match(b'MSFW1111', 0, 0x100), 1)
            for identifier, vendor, product in ((None, 0, 0x100), (b'OTHER000', 0, 0x100),
                                               (b'MSFW1111', 1, 0x100), (b'MSFW1111', 0, 0x101)):
                self.assertEqual(match(identifier, vendor, product), 0)

    def test_hwmon_layout_selector_rejects_unknown_firmware_abis(self):
        source = ''.join(line[1:] for line in
                         (ROOT / 'platform/hwmon/n1x-platform.patch').read_text().splitlines(True)
                         if line.startswith('+') and not line.startswith('+++'))
        start = source.index('static bool n1x_has_power_telemetry(')
        function = source[start:source.index('\n}\n', start) + 3]
        with tempfile.TemporaryDirectory() as temporary:
            source, binary = Path(temporary) / 'hwmon.c', Path(temporary) / 'hwmon.so'
            source.write_text('''#include <assert.h>
#include <stdbool.h>
#include <string.h>
#include <stddef.h>
typedef void *acpi_handle;
#define ACPI_SUCCESS(x) ((x)==0)
static unsigned long long selector;static int error;
static int acpi_evaluate_integer(acpi_handle h,const char *key,void *args,unsigned long long *out)
{assert(!h && !args && !strcmp(key,"PLID"));*out=selector;return error;}
''' + function + '''int match(unsigned long long id,int err)
{selector=id;error=err;return n1x_has_power_telemetry(NULL);}
''')
            subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                            str(source), '-o', str(binary)], check=True)
            match = ctypes.CDLL(str(binary)).match
            match.argtypes = ctypes.c_ulonglong, ctypes.c_int
            for selector in (0, 1, 2, 3, 0xffffffff, 0x100000002):
                self.assertEqual(match(selector, 0), int(selector == 2))
                self.assertEqual(match(selector, -1), 0)

    def test_scp_snapshot_owns_its_firmware_sized_copy_after_device_removal(self):
        source = (ROOT / 'platform/scp/n1x_scp.c').read_text()
        functions = source[source.index('struct n1x_scp_snapshot {'):
                           source.index('static const struct file_operations n1x_scp_snapshot_fops')]
        code = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
typedef uint8_t u8;
#define __user
#define GFP_KERNEL 0
#define struct_size(p,m,n) (sizeof(*(p))+(n))
struct inode {void *i_private;};struct file {void *private_data;};
struct n1x_scp {void *sram;size_t size;};
static bool fail;static size_t copied;
static void *kvmalloc(size_t bytes,int flags) {assert(flags==GFP_KERNEL);return fail?NULL:malloc(bytes);}
#define kvfree(p) free(p)
static void memcpy_fromio(void *to,const void *from,size_t bytes) {copied=bytes;memcpy(to,from,bytes);}
static ssize_t simple_read_from_buffer(char *to,size_t count,loff_t *pos,const void *from,size_t bytes)
{if((size_t)*pos>=bytes)return 0;size_t n=bytes-*pos;if(n>count)n=count;memcpy(to,(const u8 *)from+*pos,n);*pos+=n;return n;}
''' + functions + r'''
int main(void)
{
 for(unsigned size=4096;size<=1048576;size*=4){
  struct n1x_scp scp={malloc(size),size};assert(scp.sram);memset(scp.sram,0x5a,size);
  struct inode inode={&scp};struct file file={0};fail=true;
  assert(n1x_scp_snapshot_open(&inode,&file)==-ENOMEM && !file.private_data);fail=false;
  assert(!n1x_scp_snapshot_open(&inode,&file) && copied==size);
  memset(scp.sram,0xa5,size);free(scp.sram);scp.sram=NULL;scp.size=0;
  char bytes[777];loff_t pos=0;size_t total=0;ssize_t n;
  while((n=n1x_scp_snapshot_read(&file,bytes,sizeof(bytes),&pos))>0){
   for(ssize_t i=0;i<n;i++)assert(bytes[i]==0x5a);total+=n;
  }
  assert(!n && total==size && !n1x_scp_snapshot_release(&inode,&file));
 }
}
'''
        with tempfile.TemporaryDirectory() as temporary:
            source, binary = Path(temporary) / 'snapshot.c', Path(temporary) / 'snapshot'
            source.write_text(code)
            subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                            '-Wno-unused-parameter', '-Wno-misleading-indentation',
                            '-fsanitize=address,undefined', str(source), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
