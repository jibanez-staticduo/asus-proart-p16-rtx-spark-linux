#!/usr/bin/env python3
"""Run actual DSP PM/inspection callbacks with PM faults and boot contention."""
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    source = Path(sys.argv[1] if len(sys.argv) > 1 else
                  Path(__file__).with_name('n1x_adsp.c')).read_text()
    callbacks = source[source.index('static int n1x_adsp_runtime_suspend('):
                       source.index('static const struct dev_pm_ops n1x_adsp_pm')]
    inspect = source[source.index('static ssize_t inspect_show('):
                     source.index('static struct device_attribute dev_attr_inspect')]
    assert '.prepare = pm_sleep_ptr(n1x_adsp_prepare)' in source
    assert '.complete = pm_sleep_ptr(n1x_adsp_complete)' in source
    assert 'SYSTEM_SLEEP_PM_OPS(pm_runtime_force_suspend, pm_runtime_force_resume)' in source
    code = r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <semaphore.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/types.h>
struct device {int unused;};
struct device_attribute {int unused;};
struct n1x_adsp {
 pthread_mutex_t lock;
 bool published, sleep_prepared;
 void *pwrap;
 uint8_t *mailbox, *shared;
};
#define READ_ONCE(x) (x)
#define ADSP_OUT_STATUS 0x104
#define ADSP_CONFIG_OFFSET 0x3a0
#define CAP_SYS_RAWIO 17
static struct device dev;
static uint8_t registers[0x400];
static struct n1x_adsp dsp = {.lock=PTHREAD_MUTEX_INITIALIZER};
static _Thread_local bool locked, signal_lock;
static sem_t entering;
static unsigned warnings, suspends, resumes, gets, pm_puts, reads;
static int suspend_result, resume_result, get_result;
static bool privileged = true;
static void mutex_lock(pthread_mutex_t *lock)
{
 assert(lock==&dsp.lock && !locked);
 if(signal_lock) {signal_lock=false;assert(!sem_post(&entering));}
 assert(!pthread_mutex_lock(lock));locked=true;
}
static void mutex_unlock(pthread_mutex_t *lock)
{assert(lock==&dsp.lock && locked);locked=false;assert(!pthread_mutex_unlock(lock));}
static void *dev_get_drvdata(struct device *d) {assert(d==&dev);return &dsp;}
static void warning(struct device *d, const char *message)
{assert(d==&dev && message && locked);warnings++;}
#define dev_warn_ratelimited(d, message) warning(d, message)
static int mtk_pwrap_dev_suspend(void *p, int flags)
{assert(p==&dsp && !flags);suspends++;return suspend_result;}
static int mtk_pwrap_dev_resume(void *p, int flags)
{assert(p==&dsp && !flags);resumes++;return resume_result;}
static bool capable(int capability) {assert(capability==CAP_SYS_RAWIO);return privileged;}
static int pm_runtime_resume_and_get(struct device *d)
{assert(d==&dev && locked && !dsp.sleep_prepared);gets++;return get_result;}
static void pm_runtime_mark_last_busy(struct device *d) {assert(d==&dev && locked);}
static void pm_runtime_put_autosuspend(struct device *d)
{assert(d==&dev && locked);pm_puts++;}
static uint32_t readl(const void *p) {assert(p && locked);reads++;return 0;}
static int sysfs_emit(char *buffer, const char *format, ...)
{assert(buffer && format && locked);return 1;}
''' + callbacks + inspect + r'''
static void reset(void)
{
 assert(!locked);dsp.published=dsp.sleep_prepared=false;
 dsp.pwrap=&dsp;dsp.mailbox=dsp.shared=registers;
 warnings=suspends=resumes=gets=pm_puts=reads=0;
 suspend_result=resume_result=get_result=0;privileged=true;
}
static int sleep_cycle(void)
{
 int err=n1x_adsp_prepare(&dev);
 if(err) return err; /* PM core does not suspend ANY device on prepare failure. */
 err=n1x_adsp_runtime_suspend(&dev);
 if(!err) err=n1x_adsp_runtime_resume(&dev);
 n1x_adsp_complete(&dev);
 return err;
}
static void *prepare_thread(void *result)
{signal_lock=true;*(int *)result=n1x_adsp_prepare(&dev);return NULL;}
int main(void)
{
 reset();assert(!sleep_cycle() && suspends==1 && resumes==1 && !dsp.sleep_prepared);
 reset();suspend_result=-EIO;
 assert(sleep_cycle()==-EIO && suspends==1 && !resumes && !dsp.sleep_prepared);
 reset();resume_result=-ETIMEDOUT;
 assert(sleep_cycle()==-ETIMEDOUT && !dsp.sleep_prepared);

 reset();dsp.published=true;
 for(unsigned i=0;i<32;i++) {
  assert(sleep_cycle()==-EBUSY && !dsp.sleep_prepared);
  assert(!suspends && !resumes && !gets && !pm_puts && !reads);
 }
 assert(warnings==32);
 /* Retain the runtime protection as an independent safety net. */
 assert(n1x_adsp_runtime_suspend(&dev)==-EBUSY && !suspends);
 /* A ready timeout is still a publication: no readiness-based exemption. */
 assert(dsp.published);

 reset();assert(!n1x_adsp_prepare(&dev) && dsp.sleep_prepared);
 char buffer[1024];
 assert(inspect_show(&dev,NULL,buffer)==-EBUSY && !gets && !reads);
 n1x_adsp_complete(&dev); /* Includes another device failing prepare/suspend. */
 assert(!dsp.sleep_prepared);
 get_result=-EIO;
 assert(inspect_show(&dev,NULL,buffer)==-EIO && gets==1 && !pm_puts && !reads);
 privileged=false;
 assert(inspect_show(&dev,NULL,buffer)==-EPERM && gets==1);
 privileged=true;dsp.published=true;
 assert(inspect_show(&dev,NULL,buffer)==-EBUSY && gets==1);

 /* prepare must wait for an in-flight boot holding the publication lock. */
 reset();assert(!sem_init(&entering,0,0));
 for(unsigned i=0;i<32;i++) {
  pthread_t thread;int result=0;
  mutex_lock(&dsp.lock);dsp.published=false;
  assert(!pthread_create(&thread,NULL,prepare_thread,&result));
  while(sem_wait(&entering)) assert(errno==EINTR);
  dsp.published=true;mutex_unlock(&dsp.lock);
  assert(!pthread_join(thread,NULL));
  assert(result==-EBUSY && !dsp.sleep_prepared && !suspends);
 }
 assert(!sem_destroy(&entering));
 puts("DSP PM callbacks: PASS (early rejection, abort unwind, runtime faults, boot contention)");
}
'''
    with tempfile.TemporaryDirectory(prefix='n1x-adsp-pm-') as temporary:
        test, binary = Path(temporary) / 'pm.c', Path(temporary) / 'pm'
        test.write_text(code)
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-fsanitize=address,undefined',
                        '-g', '-pthread', str(test), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=30)


if __name__ == '__main__':
    main()
