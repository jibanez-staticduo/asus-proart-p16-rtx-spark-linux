#!/usr/bin/env python3
"""Compile the DSP request, reply and client API against fault-injected OS edges."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def main():
    source = (ROOT / 'n1x_adsp.c').read_text()
    constants = source[source.index('#define ADSP_RX_OFFSET '):
                       source.index('static bool enable_boot;')]
    state = source[source.index('struct n1x_adsp_target {'):
                   source.index('static int n1x_adsp_group_member(')]
    transport = source[source.index('static void n1x_adsp_ipc_fail('):
                       source.index('static void n1x_adsp_power_work(')]
    client = source[source.index('int n1x_adsp_transfer(struct device *dev,'):
                    source.index('EXPORT_SYMBOL_GPL(n1x_adsp_transfer);')]
    harness = r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "n1x_adsp_client.h"
#include "n1x_sroot_client.h"
typedef uint64_t u64;typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;
typedef uint64_t dma_addr_t;
typedef struct {pthread_mutex_t mutex;} spinlock_t;
typedef int atomic_t;
struct device_driver {int unused;};
struct device {struct device_driver *driver;pthread_mutex_t mutex;void *data;};
struct platform_driver {struct device_driver driver;};
struct mutex {pthread_mutex_t mutex;};
struct completion {bool done;};
struct work_struct {int unused;};
#define DECLARE_KFIFO(name,type,size) struct {type data[size];} name
#define __iomem
#define BIT(n) (1U<<(n))
#define READ_ONCE(x) (x)
static _Thread_local unsigned irq_depth;
#define spin_lock_irqsave(lock,flags) do {(flags)=0;assert(!pthread_mutex_lock(&(lock)->mutex));irq_depth++;} while(0)
#define spin_unlock_irqrestore(lock,flags) do {(void)(flags);assert(irq_depth);irq_depth--;assert(!pthread_mutex_unlock(&(lock)->mutex));} while(0)
#define read_poll_timeout(op,val,cond,delay,timeout,before,arg) ({ \
 int rc=-ETIMEDOUT;(void)(delay);(void)(timeout);(void)(before); \
 for(unsigned attempt=0;attempt<3;attempt++){(val)=op(arg);if(cond){rc=0;break;}} rc;})
static int atomic_read(atomic_t *p) {return *p;}
static void complete(struct completion *c) {c->done=true;}
static void reinit_completion(struct completion *c) {c->done=false;}
static unsigned long usecs_to_jiffies(unsigned us) {return us;}
static struct platform_driver n1x_adsp_driver;
static struct device device;
static int lock_attempts;
static int lock_error;
static void device_lock(struct device *dev)
{__atomic_add_fetch(&lock_attempts,1,__ATOMIC_SEQ_CST);assert(!pthread_mutex_lock(&dev->mutex));}
static void device_unlock(struct device *dev) {assert(!pthread_mutex_unlock(&dev->mutex));}
static void *dev_get_drvdata(struct device *dev) {return dev->data;}
static int mutex_lock_interruptible(struct mutex *lock)
{return lock_error?lock_error:-pthread_mutex_lock(&lock->mutex);}
static void mutex_unlock(struct mutex *lock) {assert(!pthread_mutex_unlock(&lock->mutex));}
static u32 readl(const void *address);
static void writel(u32 value,void *address);
static void memcpy_toio(void *dest,const void *src,size_t bytes);
static void wmb(void);
static unsigned long wait_for_completion_timeout(struct completion *c,unsigned long ticks);
''' + constants + state + r'''
static struct n1x_adsp dsp;
''' + transport + client + r'''
enum fault {NONE,PRE_BUSY,PRE_DOORBELL,NO_ACK,WRONG_TOKEN,NOTIFICATION_ONLY,
 DUPLICATE_ACK,ACK_BUSY,ACK_DOORBELL,POWER_ERROR,BOUNDARY_ACK,HOLD_ACK};
static enum fault fault;
static u32 shared[256],mailbox[1024];
static unsigned reads,writes,stage,publications;
static bool initialized,first_waiting,release_first;
static pthread_mutex_t rendezvous=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t changed=PTHREAD_COND_INITIALIZER;
static u32 readl(const void *address)
{
 assert(address==(u8 *)shared+ADSP_TX_BUSY || address==(u8 *)mailbox+ADSP_IN_SET);
 reads++;return *(const u32 *)address;
}
static void writel(u32 value,void *address)
{
 assert(irq_depth==1 && value==1);
 if(address==(u8 *)shared+ADSP_TX_BUSY){assert(stage==0);stage=1;}
 else {assert(address==(u8 *)mailbox+ADSP_IN_SET && stage==3);stage=4;publications++;}
 *(u32 *)address=value;writes++;
}
static void memcpy_toio(void *dest,const void *src,size_t bytes)
{
 assert(irq_depth==1 && stage==1 && bytes==288 && dest==(u8 *)shared+ADSP_TX_OFFSET);
 assert(shared[ADSP_TX_BUSY/4]==1);memcpy(dest,src,bytes);stage=2;
}
static void wmb(void) {assert(irq_depth==1 && stage==2);stage=3;}
static unsigned long wait_for_completion_timeout(struct completion *c,unsigned long ticks)
{
 u8 ack[ADSP_RX_BYTES]={0};const u8 *tx=(u8 *)shared+ADSP_TX_OFFSET;
 assert(c==&dsp.ipc_done && ticks==ADSP_IPC_TIMEOUT_US && stage==4 && !irq_depth);
 assert(dsp.ipc_pending && !c->done && n1x_adsp_ipc_get16(tx+16)==dsp.ipc_token);
 assert(n1x_adsp_ipc_get32(tx)==2 && tx[19]==0x10 && tx[21]==1);
 if(fault==HOLD_ACK && dsp.ipc_token==1){
  assert(!pthread_mutex_lock(&rendezvous));first_waiting=true;
  assert(!pthread_cond_broadcast(&changed));
  while(!release_first)assert(!pthread_cond_wait(&changed,&rendezvous));
  assert(!pthread_mutex_unlock(&rendezvous));
 }
 if(fault==NO_ACK)return 0;
 if(fault==POWER_ERROR){dsp.power_error=-EIO;n1x_adsp_ipc_fail(&dsp,-EIO);return 1;}
 shared[ADSP_TX_BUSY/4]=fault==ACK_BUSY;
 mailbox[ADSP_IN_SET/4]=fault==ACK_DOORBELL;
 n1x_adsp_ipc_put16(ack+16,dsp.ipc_token+(fault==WRONG_TOKEN));
 ack[19]=0x20;ack[21]=2;ack[22]=tx[22];ack[23]=tx[23];
 /* Even a notification carrying our token must not satisfy the ACK wait. */
 assert(!n1x_adsp_ipc_receive(&dsp,ack) && dsp.ipc_pending && !c->done);
 if(fault==NOTIFICATION_ONLY)return 0;
 ack[19]=0;assert(n1x_adsp_ipc_receive(&dsp,ack));
 if(fault==DUPLICATE_ACK)assert(n1x_adsp_ipc_receive(&dsp,ack));
 stage=0;return fault==BOUNDARY_ACK?0:1;
}
static void reset(void)
{
 if(initialized){pthread_mutex_destroy(&dsp.ipc_lock.mutex);pthread_mutex_destroy(&dsp.lock.mutex);
  pthread_mutex_destroy(&device.mutex);}
 memset(&dsp,0,sizeof(dsp));memset(&device,0,sizeof(device));
 memset(shared,0,sizeof(shared));memset(mailbox,0,sizeof(mailbox));
 assert(!pthread_mutex_init(&dsp.ipc_lock.mutex,NULL));assert(!pthread_mutex_init(&dsp.lock.mutex,NULL));
 assert(!pthread_mutex_init(&device.mutex,NULL));initialized=true;
 device.driver=&n1x_adsp_driver.driver;device.data=&dsp;dsp.dev=&device;dsp.target=&targets[0];
 dsp.shared=shared;dsp.mailbox=mailbox;dsp.published=true;dsp.boot_ready=true;dsp.loader.accepted=1;
 fault=NONE;reads=writes=stage=publications=0;lock_attempts=lock_error=0;first_waiting=release_first=false;
}
static struct n1x_adsp_ipc_request request={.channel=3,.command=0x20a,.ack_mode=1};
static void expect_failure(int expected)
{
 struct n1x_adsp_ipc_reply reply,before;unsigned old_writes;
 memset(&reply,0xa5,sizeof(reply));before=reply;
 assert(n1x_adsp_transfer(&device,&request,&reply)==expected);
 assert(!memcmp(&reply,&before,sizeof(reply)) && dsp.published && dsp.loader.accepted);
 old_writes=writes;
 assert(n1x_adsp_transfer(&device,&request,&reply)<0 && writes==old_writes);
 assert(!memcmp(&reply,&before,sizeof(reply)));
}
struct thread_result {int error;struct n1x_adsp_ipc_reply reply;};
static void *caller(void *context)
{
 struct thread_result *result=context;
 result->error=n1x_adsp_transfer(&device,&request,&result->reply);return NULL;
}
int main(void)
{
 struct n1x_adsp_ipc_reply reply,before;struct n1x_adsp_ipc_request invalid;
 const struct n1x_adsp_ipc_request original=request;
 reset();invalid=request;invalid.channel=ADSP_PRIMARY_TASK;
 memset(&reply,0xa5,sizeof(reply));before=reply;
 assert(n1x_adsp_transfer(&device,&invalid,&reply)==-EOPNOTSUPP && !reads && !writes);
 invalid.channel=ADSP_PROCESSING_TASK;
 assert(n1x_adsp_transfer(&device,&invalid,&reply)==-EOPNOTSUPP && !reads && !writes);
 assert(!memcmp(&reply,&before,sizeof(reply)));
 reset();assert(!n1x_adsp_transfer(&device,&request,&reply));
 assert(writes==2 && publications==1 && !dsp.ipc_pending && !dsp.ipc_poisoned);
 assert(n1x_adsp_ipc_get16(reply.packet+16)==1 && !memcmp(&request,&original,sizeof(request)));
 assert(!n1x_adsp_transfer(&device,&request,&reply) && n1x_adsp_ipc_get16(reply.packet+16)==2);
 reset();u8 payload[256];for(unsigned i=0;i<sizeof(payload);i++)payload[i]=i;
 invalid=request;invalid.data_mode=1;invalid.parameter1=sizeof(payload);invalid.payload=payload;
 assert(!n1x_adsp_transfer(&device,&invalid,&reply));
 assert(!memcmp((u8 *)shared+ADSP_TX_OFFSET+32,payload,sizeof(payload)));
 assert(!shared[ADSP_CONFIG_OFFSET/4] && !shared[ADSP_CONFIG_OFFSET/4+2]);
 reset();dsp.ipc_token=0xffff;assert(!n1x_adsp_transfer(&device,&request,&reply));
 assert(n1x_adsp_ipc_get16(reply.packet+16)==1);
 reset();fault=BOUNDARY_ACK;assert(!n1x_adsp_transfer(&device,&request,&reply));
 for(unsigned f=PRE_BUSY;f<=POWER_ERROR;f++){
  reset();fault=f;if(f==PRE_BUSY)shared[ADSP_TX_BUSY/4]=1;
  if(f==PRE_DOORBELL)mailbox[ADSP_IN_SET/4]=1;
  expect_failure(f==WRONG_TOKEN||f==DUPLICATE_ACK?-EPROTO:f==POWER_ERROR?-EIO:-ETIMEDOUT);
  assert(dsp.ipc_poisoned && !dsp.ipc_pending);
  assert(writes==(f<=PRE_DOORBELL?0:2));
  /* A late correct ACK cannot clear a terminal error or authorize another TX. */
  u8 late[ADSP_RX_BYTES]={0};int error=dsp.ipc_error;
  n1x_adsp_ipc_put16(late+16,dsp.ipc_token);late[21]=2;
  assert(n1x_adsp_ipc_receive(&dsp,late) && dsp.ipc_poisoned && dsp.ipc_error==error);
 }
 for(unsigned guard=0;guard<7;guard++){
  reset();if(guard==0)dsp.published=false;if(guard==1)dsp.boot_ready=false;
  if(guard==2)dsp.loader.accepted=false;if(guard==3)dsp.error=-EIO;
  if(guard==4)dsp.power_error=-EIO;if(guard==5)dsp.ipc_poisoned=true;
  if(guard==6)device.driver=NULL;
  memset(&reply,0xa5,sizeof(reply));before=reply;
  assert(n1x_adsp_transfer(&device,&request,&reply)<0 && !reads && !writes);
  assert(!memcmp(&reply,&before,sizeof(reply)));
 }
 for(unsigned bad=0;bad<5;bad++){
  reset();invalid=request;if(bad==0)invalid.token=3;if(bad==1)invalid.ack_mode=0;
  if(bad==2)invalid.data_mode=2;if(bad==3){invalid.data_mode=1;invalid.parameter1=1;}
  if(bad==4){invalid.data_mode=1;invalid.payload=(const u8 *)&request;invalid.parameter1=257;}
  assert(n1x_adsp_transfer(&device,&invalid,&reply)<0 && !reads && !writes && !dsp.ipc_poisoned);
 }
 assert(n1x_adsp_transfer(NULL,&request,&reply)==-EINVAL);
 assert(n1x_adsp_transfer(&device,NULL,&reply)==-EINVAL);
 assert(n1x_adsp_transfer(&device,&request,NULL)==-EINVAL);
 reset();lock_error=-EINTR;memset(&reply,0xa5,sizeof(reply));before=reply;
 assert(n1x_adsp_transfer(&device,&request,&reply)==-EINTR && !reads && !writes);
 assert(!memcmp(&reply,&before,sizeof(reply)));lock_error=0;
 assert(!n1x_adsp_transfer(&device,&request,&reply));
 reset();fault=HOLD_ACK;pthread_t first,second;struct thread_result a={0},b={0};
 assert(!pthread_create(&first,NULL,caller,&a));assert(!pthread_mutex_lock(&rendezvous));
 while(!first_waiting)assert(!pthread_cond_wait(&changed,&rendezvous));
 assert(!pthread_create(&second,NULL,caller,&b));
 for(unsigned i=0;__atomic_load_n(&lock_attempts,__ATOMIC_SEQ_CST)!=2 && i<1000;i++)usleep(1000);
 assert(__atomic_load_n(&lock_attempts,__ATOMIC_SEQ_CST)==2 && publications==1 && writes==2);
 release_first=true;assert(!pthread_cond_broadcast(&changed));assert(!pthread_mutex_unlock(&rendezvous));
 assert(!pthread_join(first,NULL) && !pthread_join(second,NULL));
 assert(!a.error && !b.error && publications==2 && writes==4);
 assert(n1x_adsp_ipc_get16(a.reply.packet+16)==1 && n1x_adsp_ipc_get16(b.reply.packet+16)==2);
 puts("DSP transport MMIO ordering, ACK/notification separation, terminal faults and concurrent clients: PASS");
}
'''
    with tempfile.TemporaryDirectory(prefix='p16-dsp-transport-') as temporary:
        code, binary = Path(temporary) / 'test.c', Path(temporary) / 'test'
        code.write_text(harness)
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-misleading-indentation', '-fsanitize=address,undefined',
                        '-pthread', '-g', '-I' + str(ROOT), str(code), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=20)


if __name__ == '__main__':
    main()
