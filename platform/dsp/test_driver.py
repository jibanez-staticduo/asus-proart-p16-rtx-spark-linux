#!/usr/bin/env python3
"""Compile the actual DSP boot/IRQ handlers against fault-injected OS edges."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def main():
    source = (ROOT / 'n1x_adsp.c').read_text()
    state = source[source.index('struct n1x_adsp_target {'):source.index('static int n1x_adsp_group_member(')]
    handlers = source[source.index('static irqreturn_t n1x_adsp_irq('):source.index('static ssize_t boot_store(')]
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include "n1x_sroot_client.h"
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64,dma_addr_t;
typedef int irqreturn_t;
#define __iomem
#define SHA256_DIGEST_SIZE 32
#define ADSP_RX_OFFSET 0x20
#define ADSP_CONFIG_OFFSET 0x3a0
#define ADSP_RX_BYTES 0x120
#define ADSP_OUT_STATUS 0x104
#define ADSP_OUT_CLEAR 0x10c
#define CAP_SYS_RAWIO 17
#define THIS_MODULE ((void *)1)
#define DMA_BIT_MASK(n) ((1ULL<<(n))-1)
#define GFP_KERNEL 0
#define IRQ_NONE 0
#define IRQ_HANDLED 1
#define BIT(n) (1U<<(n))
#define __ffs(n) __builtin_ctz(n)
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define lower_32_bits(n) ((u32)(n))
#define msecs_to_jiffies(n) (n)
#define dev_info(...) ((void)0)
struct device_driver { const char *name; };
struct device { struct device_driver *driver; } device;
struct completion { bool done; };
struct mutex { int unused; };
struct firmware { const u8 *data;size_t size; };
static bool enable_boot=true,privileged=true,provider=true,valid_hash=true;
static int fault,reads,writes,pm_refs,module_refs,symbol_refs,uploads,frees,waits;
static unsigned long platform_bus_type;
static u8 firmware_bytes[631436];
static struct firmware firmware={firmware_bytes,sizeof(firmware_bytes)};
static void *allocation;
static u32 shared_memory[256],mailbox_memory[1024];
static int n1x_adsp_dma_gate(struct device *d) {assert(d==&device);return fault==2?-EOPNOTSUPP:0;}
static bool capable(int cap) {assert(cap==CAP_SYS_RAWIO);return privileged;}
static struct device *bus_find_device_by_name(void *bus,void *from,const char *name) {
 assert(bus==&platform_bus_type && !from && !strcmp(name,"NVDA8301:00"));return NULL;
}
static void device_lock(struct device *d) {(void)d;assert(0);}
static void device_unlock(struct device *d) {(void)d;assert(0);}
static void *dev_get_drvdata(struct device *d) {(void)d;assert(0);return NULL;}
static void put_device(struct device *d) {assert(!d);}
static int request_firmware_direct(const struct firmware **fw,const char *name,struct device *d) {
 assert(d==&device && !strcmp(name,"nvidia/n1x/hifi5-0-mt8901-signed.bin"));
 assert(symbol_refs==1 && !reads && !writes);if(fault==1)return -ENOENT;*fw=&firmware;return 0;
}
static void release_firmware(const struct firmware *fw) {assert(fw==&firmware);}
static int hex2bin(u8 *out,const char *in,size_t count) {
 for(size_t i=0;i<count;i++){unsigned value;assert(sscanf(in+2*i,"%2x",&value)==1);out[i]=value;}return 0;
}
static void sha256(const u8 *data,size_t bytes,u8 *out) {
 assert(data==firmware.data && bytes==firmware.size);
 hex2bin(out,"350e0d40b2f8a6dfb486d8dfd3362fb5b02ceb90da372176b008498adbca8cc4",32);
 if(!valid_hash)out[0]^=1;
}
static int dma_set_mask_and_coherent(struct device *d,u64 mask) {
 assert(d==&device && mask==0xffffffff);return fault==3?-EIO:0;
}
static int pm_runtime_resume_and_get(struct device *d) {
 assert(d==&device);if(fault==4)return -EIO;pm_refs++;return 0;
}
static void pm_runtime_put(struct device *d) {assert(d==&device && pm_refs==1);pm_refs--;}
static void *dma_alloc_coherent(struct device *d,size_t bytes,dma_addr_t *dma,int flag) {
 assert(d==&device && bytes==24U<<20 && flag==GFP_KERNEL && pm_refs==1 && !module_refs);
 if(fault==5)return NULL;allocation=malloc(bytes);assert(allocation);*dma=fault==6?0xd0000000:0xe0000000;
 return allocation;
}
static void dma_free_coherent(struct device *d,size_t bytes,void *cpu,dma_addr_t dma) {
 assert(d==&device && bytes==24U<<20 && cpu==allocation && dma && !writes && !module_refs);
 free(cpu);allocation=NULL;frees++;
}
static bool try_module_get(void *m) {assert(m==THIS_MODULE && pm_refs==1);if(fault==9)return false;module_refs++;return true;}
static void dma_wmb(void) {assert(pm_refs==1 && module_refs==1 && !writes);}
static u32 readl(const void *address) {assert(pm_refs==1);reads++;return *(const u32 *)address;}
static void writel(u32 value,void *address) {assert(pm_refs==1 && module_refs==1);writes++;*(u32 *)address=value;}
static void memcpy_fromio(void *dest,const void *src,size_t count) {assert(pm_refs==1 && count==ADSP_RX_BYTES);memcpy(dest,src,count);}
static u16 get_unaligned_le16(const u8 *p) {return p[0]|(u16)p[1]<<8;}
static void complete(struct completion *c) {c->done=true;}
static void reinit_completion(struct completion *c) {assert(!c->done);c->done=false;}
static unsigned long wait_for_completion_timeout(struct completion *c,unsigned long ticks) {
 assert(ticks==30000);waits++;return c->done?1:0;
}
static int upload_stub(u32 id,const u8 *image,size_t bytes,struct n1x_sroot_client_result *result);
static typeof(n1x_sroot_upload_dsp_v1) *get_provider(void) {if(!provider)return NULL;symbol_refs++;return upload_stub;}
static void put_provider(void) {assert(symbol_refs==1);symbol_refs--;}
#define symbol_get(name) get_provider()
#define symbol_put(name) put_provider()
''' + state + r'''
static struct n1x_adsp dsp;
''' + handlers + r'''
static int upload_stub(u32 id,const u8 *image,size_t bytes,struct n1x_sroot_client_result *result) {
 assert(id==0xc0304 && image==firmware.data && bytes==firmware.size);
 assert(module_refs==1 && pm_refs==1 && symbol_refs==1 && dsp.published && dsp.attempted && writes==2);
 assert(shared_memory[ADSP_CONFIG_OFFSET/4]==0xe0000000 && shared_memory[ADSP_CONFIG_OFFSET/4+2]==24U<<20);
 uploads++;result->published=1;if(fault==10)return -ETIMEDOUT;result->accepted=1;
 if(fault!=11) {
  u8 *packet=(u8 *)shared_memory+ADSP_RX_OFFSET;
  packet[19]=fault==12?0:0x20;packet[22]=2;packet[23]=0xf0;
  mailbox_memory[ADSP_OUT_STATUS/4]=8;
  assert(n1x_adsp_irq(0,&dsp)==IRQ_HANDLED);
  assert(mailbox_memory[ADSP_OUT_CLEAR/4]==8 && shared_memory[4]==0);
 }
 return 0;
}
static void reset(void) {
 free(allocation);allocation=NULL;memset(&dsp,0,sizeof(dsp));
 memset(shared_memory,0,sizeof(shared_memory));memset(mailbox_memory,0,sizeof(mailbox_memory));
 enable_boot=privileged=provider=valid_hash=true;fault=reads=writes=pm_refs=module_refs=symbol_refs=uploads=frees=waits=0;
 dsp.dev=&device;dsp.target=&targets[0];dsp.shared=shared_memory;dsp.mailbox=mailbox_memory;
 firmware.size=sizeof(firmware_bytes);
}
int main(void) {
 for(int f=1;f<=12;f++) {
  reset();fault=f;
  if(f==7)mailbox_memory[ADSP_OUT_STATUS/4]=1;
  if(f==8){shared_memory[ADSP_CONFIG_OFFSET/4]=0xe0000000;shared_memory[ADSP_CONFIG_OFFSET/4+2]=24U<<20;}
  assert(n1x_adsp_boot(&dsp)<0);assert(!symbol_refs);
  if(f<10)assert(!writes && !pm_refs && !module_refs && !dsp.published && !allocation && !uploads);
  else {
   assert(dsp.published && dsp.attempted && pm_refs==1 && module_refs==1 && allocation && !frees && uploads==1);
   assert(!dsp.boot_ready && n1x_adsp_boot(&dsp)==-EALREADY);
  }
 }
 reset();assert(!n1x_adsp_boot(&dsp));assert(dsp.boot_ready && dsp.loader.accepted && dsp.notifications==1);
 assert(module_refs==1 && pm_refs==1 && !symbol_refs && !frees && waits==1);
 reset();shared_memory[ADSP_CONFIG_OFFSET/4]=0xbf0676f6;shared_memory[ADSP_CONFIG_OFFSET/4+2]=0xb6dd3e39;
 assert(!n1x_adsp_boot(&dsp) && dsp.boot_ready); /* Observed uninitialized SRAM is not a DMA window. */
 for(int guard=0;guard<6;guard++) {
  reset();if(guard==0)enable_boot=false;if(guard==1)privileged=false;if(guard==2)provider=false;
  if(guard==3)valid_hash=false;if(guard==4)firmware.size--;if(guard==5)dsp.target=&targets[1];
  assert(n1x_adsp_boot(&dsp)<0);assert(!reads && !writes && !pm_refs && !module_refs && !symbol_refs);
 }
 reset();assert(n1x_adsp_irq(0,&dsp)==IRQ_NONE && !reads && !writes);
 reset();puts("DSP boot/IRQ guards, DMA retention, notification class and no-retry checks passed");
}
'''
    with tempfile.TemporaryDirectory() as temporary:
        code, binary = Path(temporary) / 'test.c', Path(temporary) / 'test'
        code.write_text(harness)
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-Wno-misleading-indentation',
                        '-fsanitize=address,undefined', '-g', '-I' + str(ROOT),
                        str(code), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    main()
