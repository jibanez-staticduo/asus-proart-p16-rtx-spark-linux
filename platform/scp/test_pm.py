#!/usr/bin/env python3
"""Exercise actual SCP debugfs callbacks against suspend and reader races."""
from pathlib import Path
import subprocess
import tempfile


def main():
    source = Path(__file__).with_name('n1x_scp.c').read_text()
    reads = source[source.index('static int n1x_scp_lock_live('):
                   source.index('static void n1x_scp_debugfs_remove(')]
    pm = source[source.index('static int n1x_scp_suspend('):
                source.index('static DEFINE_SIMPLE_DEV_PM_OPS')]
    constants = '\n'.join(s for s in source.splitlines() if s.startswith('#define N1X_'))
    code = r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <semaphore.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;
#define __iomem
#define __user
#define GFP_KERNEL 0
#define THIS_MODULE NULL
#define READ_ONCE(x) (x)
#define struct_size(p,m,n) (sizeof(*(p))+(n))
#define DEFINE_SHOW_ATTRIBUTE(name)
struct n1x_scp {u8 *sram;size_t size;pthread_mutex_t query_lock;bool suspended,mailbox_poisoned;};
struct seq_file {struct n1x_scp *private;};
struct inode {void *i_private;};struct file {void *private_data;};
struct device {struct n1x_scp *data;};
struct file_operations {
 void *owner;
 int (*open)(struct inode *, struct file *);
 ssize_t (*read)(struct file *,char *,size_t,loff_t *);
 int (*release)(struct inode *,struct file *);
 void *llseek;
};
#define default_llseek NULL
static struct n1x_scp scp={.query_lock=PTHREAD_MUTEX_INITIALIZER};
static bool enable_mailbox, bad_header, pause_copy;
static int lock_error, accesses, allocations;
static _Thread_local bool locked, notify_lock;
static sem_t copy_started, copy_continue, suspend_started;
static void mutex_lock(pthread_mutex_t *lock)
{
 assert(lock==&scp.query_lock && !locked);
 if(notify_lock) {assert(!sem_post(&suspend_started));notify_lock=false;}
 assert(!pthread_mutex_lock(lock));locked=true;
}
static int mutex_lock_interruptible(pthread_mutex_t *lock)
{if(lock_error)return lock_error;mutex_lock(lock);return 0;}
static void mutex_unlock(pthread_mutex_t *lock)
{assert(locked);locked=false;assert(!pthread_mutex_unlock(lock));}
static u16 ioread16(void *address)
{assert(locked && !scp.suspended);accesses++;return (u8 *)address==scp.sram+N1X_LOG_OFFSET?(bad_header?0:0x100):2;}
static u32 ioread32(void *address)
{assert(locked && !scp.suspended);accesses++;return (u8 *)address==scp.sram+N1X_LOG_OFFSET+4?N1X_LOG_MAGIC:2;}
static void wait_sem(sem_t *s) {while(sem_wait(s))assert(errno==EINTR);}
static void memcpy_fromio(void *to,const void *from,size_t bytes)
{
 assert(locked && !scp.suspended);accesses++;
 if(pause_copy) {assert(!sem_post(&copy_started));wait_sem(&copy_continue);}
 memcpy(to,from,bytes);
}
static void seq_printf(struct seq_file *s,const char *format,...)
{assert(s->private==&scp && format && locked);}
#define seq_puts seq_printf
static void seq_write(struct seq_file *s,const void *data,size_t bytes)
{assert(s->private==&scp && data && bytes<=N1X_LOG_RECORD_SIZE && locked);}
static void *kvmalloc(size_t bytes,int flags)
{assert(!flags);void *p=malloc(bytes);if(p)allocations++;return p;}
static void kvfree(void *p) {if(p){allocations--;free(p);}}
static ssize_t simple_read_from_buffer(char *to,size_t count,loff_t *pos,const void *from,size_t bytes)
{size_t n=bytes-*pos;if(n>count)n=count;memcpy(to,(const u8 *)from+*pos,n);*pos+=n;return n;}
static void *dev_get_drvdata(struct device *dev) {return dev->data;}
''' + reads + pm + r'''
static struct inode inode={&scp};
static struct file file;
static struct device dev={&scp};
static void *reader(void *unused) {(void)unused;assert(!n1x_scp_snapshot_fops.open(&inode,&file));return NULL;}
static void *suspender(void *unused) {(void)unused;notify_lock=true;assert(!n1x_scp_suspend(&dev));return NULL;}
int main(void)
{
 scp.size=N1X_LOG_OFFSET+N1X_LOG_HEADER_SIZE+2*N1X_LOG_RECORD_SIZE;
 scp.sram=calloc(1,scp.size);assert(scp.sram);
 struct seq_file seq={&scp};
 assert(!n1x_scp_status_show(&seq,NULL) && !locked);
 assert(!n1x_scp_log_show(&seq,NULL) && !locked);
 bad_header=true;assert(n1x_scp_status_show(&seq,NULL)==-ENODATA && !locked);
 assert(n1x_scp_log_show(&seq,NULL)==-ENODATA && !locked);bad_header=false;
 lock_error=-EINTR;accesses=0;
 assert(n1x_scp_status_show(&seq,NULL)==-EINTR);
 assert(n1x_scp_log_show(&seq,NULL)==-EINTR);
 assert(n1x_scp_snapshot_fops.open(&inode,&file)==-EINTR && !allocations);
 assert(!accesses && !locked);lock_error=0;
 assert(!n1x_scp_suspend(&dev));accesses=0;
 assert(n1x_scp_status_show(&seq,NULL)==-EHOSTDOWN);
 assert(n1x_scp_log_show(&seq,NULL)==-EHOSTDOWN);
 assert(n1x_scp_snapshot_fops.open(&inode,&file)==-EHOSTDOWN && !allocations);
 assert(!accesses && !locked);assert(!n1x_scp_resume(&dev));
 assert(!sem_init(&copy_started,0,0) && !sem_init(&copy_continue,0,0) && !sem_init(&suspend_started,0,0));
 pause_copy=true;pthread_t read_thread,pm_thread;
 assert(!pthread_create(&read_thread,NULL,reader,NULL));wait_sem(&copy_started);
 assert(!pthread_create(&pm_thread,NULL,suspender,NULL));wait_sem(&suspend_started);
 assert(!scp.suspended); /* MMIO copy owns the same lock needed by suspend. */
 assert(!sem_post(&copy_continue));
 assert(!pthread_join(read_thread,NULL) && !pthread_join(pm_thread,NULL));
 assert(scp.suspended && allocations==1);accesses=0;
 char bytes[32];loff_t position=0;
 assert(n1x_scp_snapshot_fops.read(&file,bytes,sizeof(bytes),&position)==sizeof(bytes));
 assert(!accesses && !n1x_scp_snapshot_fops.release(&inode,&file) && !allocations);
 assert(!sem_destroy(&copy_started) && !sem_destroy(&copy_continue) && !sem_destroy(&suspend_started));
 free(scp.sram);
 puts("SCP PM: log/status/snapshot gating, interruption, invalid header, read/suspend race: PASS");
}
'''
    # Constants must precede the shims that check the actual register accesses.
    code = constants + '\n' + code
    with tempfile.TemporaryDirectory(prefix='n1x-scp-pm-') as temporary:
        test, binary = Path(temporary) / 'pm.c', Path(temporary) / 'pm'
        test.write_text(code)
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-fsanitize=address,undefined',
                        '-pthread', str(test), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=30)


if __name__ == '__main__':
    main()
