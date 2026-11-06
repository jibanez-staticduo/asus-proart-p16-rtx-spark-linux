#!/usr/bin/env python3
"""Test actual coherent producer publication, ordering and IRQ serialization."""
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent


def main():
    source = (HERE / 'n1x_adsp.c').read_text()
    state = source[source.index('struct n1x_adsp_target {'):
                   source.index('static int n1x_adsp_group_member(')]
    commit = source[source.index('int n1x_adsp_primary_commit('):
                    source.index('EXPORT_SYMBOL_GPL(n1x_adsp_primary_commit);')]
    receive = source[source.index('static void n1x_adsp_period_receive('):
                     source.index('static irqreturn_t n1x_adsp_irq(')]
    code = r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "n1x_adsp_client.h"
#include "n1x_sroot_client.h"
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64,dma_addr_t;
typedef pthread_mutex_t spinlock_t;typedef int atomic_t;
#define __iomem
#define DECLARE_KFIFO(name,type,size) struct {type data[size];} name
#define IS_ERR(p) ((uintptr_t)(p)>=(uintptr_t)-4095)
#define IS_ERR_OR_NULL(p) (!(p)||IS_ERR(p))
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define U64_MAX UINT64_MAX
#define ADSP_RX_BYTES 288
struct device_driver {int unused;};struct platform_driver {struct device_driver driver;};
struct device {struct device_driver *driver;};
struct mutex {pthread_mutex_t native;};struct completion {int unused;};struct work_struct {int unused;};
''' + state + r'''
static struct n1x_adsp dsp;
static struct n1x_adsp_primary owner;
static struct device device;
static struct platform_driver n1x_adsp_driver;
static pthread_mutex_t device_mutex=PTHREAD_MUTEX_INITIALIZER,events=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t condition=PTHREAD_COND_INITIALIZER;
static _Thread_local bool dev_locked,request_locked,irq_locked,in_irq;
static bool pause_copy,copy_entered,release_copy,irq_waiting;
static unsigned barriers,wire_writes,late_power_at;
static int lock_error;
static atomic_bool producer_done;
static u8 memory[4096+7680+32],before[sizeof(memory)],packet[288];
static int atomic_read(atomic_t *value) {return *value;}
static void device_lock(struct device *dev)
{assert(dev==&device && !in_irq && !dev_locked);assert(!pthread_mutex_lock(&device_mutex));dev_locked=true;}
static void device_unlock(struct device *dev)
{assert(dev==&device && dev_locked && !request_locked && !irq_locked);dev_locked=false;assert(!pthread_mutex_unlock(&device_mutex));}
static void *dev_get_drvdata(struct device *dev) {assert(dev==&device && dev_locked);return &dsp;}
static int mutex_lock_interruptible(struct mutex *lock)
{assert(lock==&dsp.lock && dev_locked && !request_locked);if(lock_error)return lock_error;
 assert(!pthread_mutex_lock(&lock->native));request_locked=true;return 0;}
static void mutex_unlock(struct mutex *lock)
{assert(lock==&dsp.lock && request_locked && !irq_locked);request_locked=false;assert(!pthread_mutex_unlock(&lock->native));}
static void spin_take(spinlock_t *lock)
{
 assert(lock==&dsp.ipc_lock && !irq_locked);
 if(in_irq){assert(!pthread_mutex_lock(&events));irq_waiting=true;
  assert(!pthread_cond_broadcast(&condition));assert(!pthread_mutex_unlock(&events));}
 assert(!pthread_mutex_lock(lock));irq_locked=true;
}
static void spin_drop(spinlock_t *lock)
{assert(lock==&dsp.ipc_lock && irq_locked);irq_locked=false;assert(!pthread_mutex_unlock(lock));}
#define spin_lock_irqsave(lock,flags) ((flags)=0,spin_take(lock))
#define spin_unlock_irqrestore(lock,flags) ((void)(flags),spin_drop(lock))
static void dma_wmb(void)
{
 assert(dev_locked && request_locked && irq_locked && !in_irq);barriers++;
 for(size_t i=4096;i<4096+7680;i++)assert(memory[i]==0x5a);
 if(late_power_at==barriers)dsp.power_error=-EIO;
}
static void *publish_copy(void *destination,const void *source,size_t bytes)
{
 const u8 *descriptor=source;
 assert(destination==memory && bytes==96 && irq_locked && request_locked && dev_locked);
 assert(barriers%2==1);
 assert(n1x_adsp_pcm_get64(descriptor)==0xe0001000);
 assert(n1x_adsp_pcm_get64(descriptor+8)==0xe0002e00);
 assert(n1x_adsp_pcm_get64(descriptor+16)==dsp.primary_read_address);
 assert(n1x_adsp_pcm_get64(descriptor+24)>=0xe0001000 && n1x_adsp_pcm_get64(descriptor+24)<=0xe0002e00);
 assert(n1x_adsp_ipc_get32(descriptor+84)==dsp.primary_write_counter);
 if(pause_copy){
  assert(!pthread_mutex_lock(&events));copy_entered=true;assert(!pthread_cond_broadcast(&condition));
  while(!release_copy)assert(!pthread_cond_wait(&condition,&events));
  assert(!pthread_mutex_unlock(&events));
 }
 wire_writes++;return __builtin_memcpy(destination,source,bytes);
}
#define memcpy(to,from,bytes) publish_copy((to),(from),(bytes))
''' + commit + receive + r'''
#undef memcpy
static void reset(void)
{
 memset(&dsp,0,sizeof(dsp));assert(!pthread_mutex_init(&dsp.ipc_lock,NULL));
 assert(!pthread_mutex_init(&dsp.lock.native,NULL));owner.dev=&device;owner.dsp=&dsp;
 device.driver=&n1x_adsp_driver.driver;dsp.dev=&device;dsp.primary_owner=&owner;
 dsp.target=&targets[2];dsp.dram=memory;
 dsp.primary_task_active=dsp.primary_pcm_configured=dsp.primary_pcm_prepared=dsp.primary_period_enabled=true;
 dsp.primary_host_pcm=(struct n1x_adsp_pcm_config){.address=0xe0001000,.bytes=7680,.channels=2,.sample_bits=16,
  .rate=48000,.period_frames=480,.periods=4};dsp.primary_read_address=0xe0001000;
 memset(memory,0xa5,sizeof(memory));memset(memory+4096,0x5a,7680);memcpy(before,memory,sizeof(memory));
 memset(packet,0,sizeof(packet));packet[18]=7;packet[20]=1;packet[22]=0x1b;
 n1x_adsp_ipc_put32(packet+24,96);n1x_adsp_pcm_put64(packet+0x30,0xe0002e00);
 pause_copy=copy_entered=release_copy=irq_waiting=false;
 barriers=wire_writes=late_power_at=0;lock_error=0;atomic_store(&producer_done,false);
}
static void cleanup(void)
{assert(!dev_locked && !request_locked && !irq_locked);assert(!pthread_mutex_destroy(&dsp.ipc_lock));
 assert(!pthread_mutex_destroy(&dsp.lock.native));}
static void intact(void)
{
 for(size_t i=96;i<4096;i++)assert(memory[i]==0xa5);
 for(size_t i=4096;i<4096+7680;i++)assert(memory[i]==0x5a);
 for(size_t i=4096+7680;i<sizeof(memory);i++)assert(memory[i]==0xa5);
}
static void *producer(void *offset)
{assert(!n1x_adsp_primary_commit(&owner,(u32)(uintptr_t)offset));atomic_store(&producer_done,true);return NULL;}
static void *interrupt(void *unused)
{(void)unused;in_irq=true;n1x_adsp_period_receive(&dsp,packet);in_irq=false;return NULL;}
int main(void)
{
 reset();assert(!n1x_adsp_primary_commit(&owner,7680));
 assert(barriers==2 && wire_writes==1 && dsp.primary_write_counter==1 && dsp.primary_write_publications==1);
 assert(n1x_adsp_pcm_get64(memory+24)==0xe0002e00 && !n1x_adsp_ipc_get32(memory+84));intact();
 interrupt(NULL);assert(dsp.primary_read_address==0xe0002e00 && !dsp.primary_position.byte_offset);
 assert(!n1x_adsp_primary_commit(&owner,0));
 assert(n1x_adsp_pcm_get64(memory+16)==0xe0002e00 && n1x_adsp_pcm_get64(memory+24)==0xe0001000);
 assert(n1x_adsp_ipc_get32(memory+84)==1);
 assert(!n1x_adsp_primary_commit(&owner,3840));n1x_adsp_ipc_put32(packet+0x74,2);interrupt(NULL);
 n1x_adsp_pcm_put64(packet+0x30,0xe0001004);n1x_adsp_ipc_put32(packet+0x74,1);interrupt(NULL);
 assert(dsp.primary_read_address==0xe0002e00 && dsp.primary_position.counter==2);
 assert(!n1x_adsp_primary_commit(&owner,1920));assert(n1x_adsp_pcm_get64(memory+16)==0xe0002e00);intact();
 dsp.primary_write_counter=0x7fffffff;assert(!n1x_adsp_primary_commit(&owner,7680));
 assert(n1x_adsp_ipc_get32(memory+84)==0x7fffffff && !dsp.primary_write_counter);
 assert(!n1x_adsp_primary_commit(&owner,0) && !n1x_adsp_ipc_get32(memory+84) && dsp.primary_write_counter==1);intact();
 cleanup();
 for(unsigned fault=0;fault<21;fault++){
  reset();u32 offset=3840;
  switch(fault){
  case 0:device.driver=NULL;break;case 1:lock_error=-EINTR;break;case 2:dsp.primary_owner=NULL;break;
  case 3:dsp.primary_task_active=false;break;case 4:dsp.primary_pcm_prepared=false;break;
  case 5:dsp.dram=NULL;break;case 6:dsp.primary_task_error=-EIO;break;
  case 7:dsp.ipc_poisoned=true;dsp.ipc_error=-ETIMEDOUT;break;case 8:dsp.power_error=-EIO;break;
  case 9:dsp.primary_period_error=-ERANGE;break;case 10:dsp.primary_period_enabled=false;break;
  case 11:dsp.primary_write_publications=UINT64_MAX;break;case 12:dsp.primary_read_address--;break;
  case 13:dsp.primary_read_address++;break;case 14:dsp.primary_write_counter=0x80000000;break;
  case 15:offset=7681;break;case 16:offset=3841;break;
  case 17:dsp.primary_host_pcm.hardware=1;break;case 18:dsp.primary_host_pcm.periods=1;break;
  case 19:dsp.primary_pcm_configured=false;break;case 20:dsp.primary_host_pcm.address=UINT64_MAX-4;break;
  }
  u64 publications=dsp.primary_write_publications;u32 counter=dsp.primary_write_counter;
  assert(n1x_adsp_primary_commit(&owner,offset)<0 && !barriers && !wire_writes);
  assert(!memcmp(memory,before,sizeof(memory)) && publications==dsp.primary_write_publications && counter==dsp.primary_write_counter);
  cleanup();
 }
 for(unsigned fence=1;fence<=2;fence++){
  reset();late_power_at=fence;assert(n1x_adsp_primary_commit(&owner,3840)==-EIO);
  assert(barriers==2 && wire_writes==1 && dsp.primary_write_publications==1 && dsp.primary_task_error==-EIO);
  assert(n1x_adsp_pcm_get64(memory+24)==0xe0001f00);
  assert(n1x_adsp_primary_commit(&owner,7680)==-EIO && wire_writes==1 && barriers==2);intact();cleanup();
 }
 reset();pause_copy=true;pthread_t writer,irq_thread;
 assert(!pthread_create(&writer,NULL,producer,(void *)3840));
 assert(!pthread_mutex_lock(&events));while(!copy_entered)assert(!pthread_cond_wait(&condition,&events));
 assert(!pthread_mutex_unlock(&events));
 assert(!pthread_create(&irq_thread,NULL,interrupt,NULL));
 assert(!pthread_mutex_lock(&events));while(!irq_waiting)assert(!pthread_cond_wait(&condition,&events));
 assert(!atomic_load(&producer_done));release_copy=true;assert(!pthread_cond_broadcast(&condition));
 assert(!pthread_mutex_unlock(&events));assert(!pthread_join(writer,NULL));assert(!pthread_join(irq_thread,NULL));
 assert(dsp.primary_read_address==0xe0002e00 && n1x_adsp_pcm_get64(memory+16)==0xe0001000);
 pause_copy=false;assert(!n1x_adsp_primary_commit(&owner,7680));
 assert(n1x_adsp_pcm_get64(memory+16)==0xe0002e00 && dsp.primary_write_publications==2);intact();cleanup();
 reset();pthread_t writers[4];
 for(unsigned i=0;i<4;i++)assert(!pthread_create(&writers[i],NULL,producer,(void *)(uintptr_t)(i*1920)));
 for(unsigned i=0;i<4;i++)assert(!pthread_join(writers[i],NULL));
 assert(dsp.primary_write_publications==4 && dsp.primary_write_counter==4 && wire_writes==4 && barriers==8);
 assert(n1x_adsp_ipc_get32(memory+84)==3);intact();cleanup();
 assert(n1x_adsp_primary_commit(NULL,0)==-EINVAL && n1x_adsp_primary_commit(ERR_PTR(-ENOMEM),0)==-EINVAL);
 puts("DSP producer: ordered coherent publication, cursor/generation bounds, terminal power faults and IRQ/writer races: PASS");
 return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='dsp-producer-') as directory:
        directory = Path(directory)
        path, binary = directory / 'producer.c', directory / 'producer'
        path.write_text(code)
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-misleading-indentation', '-fsanitize=address,undefined',
                        '-pthread', '-g', '-I', str(HERE), str(path), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=20)


if __name__ == '__main__':
    main()
