#!/usr/bin/env python3
"""Reproduce reply replacement in the prerequisite driver and check the fix."""
import argparse
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def function(source, name):
    start = source.index('static int ' + name + '(')
    return source[start:source.index('\n}\n', start) + 3]


def harness(source, legacy):
    names = ['spi_hid_sync_request', 'spi_hid_get_request', 'spi_hid_ll_raw_request']
    if not legacy:
        names.insert(0, '__spi_hid_sync_request')
    functions = '\n'.join(function(source, name) for name in names)
    return r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;typedef uint8_t __u8;
#define HID_INPUT_REPORT 0
#define HID_FEATURE_REPORT 2
#define GET_FEATURE 4
#define GET_INPUT_REPORT 5
#define GET_FEATURE_RESPONSE 6
#define GET_INPUT_REPORT_RESPONSE 7
#define HID_REQ_SET_REPORT 8
#define HID_REQ_GET_REPORT 9
#define SPI_HID_ERROR 1
#define SPI_HID_RESP_TIMEOUT 1000
#define min_t(type,a,b) ((type)(a)<(type)(b)?(type)(a):(type)(b))
#define dev_err(dev,...) ((void)(dev))
struct device {int unused;};
struct spi_device {struct device dev;};
struct hid_device {void *driver_data;};
struct completion {int done;};
struct response {u8 body[4],content[16];};
struct spi_hid {
 struct spi_device *spi;
 bool input_stream_only;
 pthread_mutex_t output_lock;
 struct completion output_done;
 struct response *response;
 size_t response_length;
 unsigned long flags;
 int reset_work;
};
struct spi_hid_output_report {u8 report_type,content_id;size_t content_length;u8 *content;};
static struct spi_device spi;
static struct hid_device hid={.driver_data=&spi};
static struct response response;
static struct spi_hid shid={.spi=&spi,.response=&response,.output_lock=PTHREAD_MUTEX_INITIALIZER};
static pthread_mutex_t events=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t condition=PTHREAD_COND_INITIALIZER;
static bool copy_waiting,second_lock_attempted,second_finished,release_copy;
static unsigned calls;
static _Thread_local int caller;
static void *spi_get_drvdata(struct spi_device *s) {assert(s==&spi);return &shid;}
static void set_bit(unsigned bit,unsigned long *flags) {assert(bit==SPI_HID_ERROR);*flags|=1UL<<bit;}
static void schedule_work(int *work) {assert(work==&shid.reset_work);(*work)++;}
static void reinit_completion(struct completion *done) {done->done=0;}
static int msecs_to_jiffies(int ms) {assert(ms==SPI_HID_RESP_TIMEOUT);return ms;}
static int wait_for_completion_interruptible_timeout(struct completion *done,int ms)
{assert(ms==SPI_HID_RESP_TIMEOUT && done->done);return 1;}
static pthread_mutex_t *lock_output(pthread_mutex_t *mutex)
{
 if(caller==2){
  pthread_mutex_lock(&events);second_lock_attempted=true;
  pthread_cond_broadcast(&condition);pthread_mutex_unlock(&events);
 }
 assert(!pthread_mutex_lock(mutex));return mutex;
}
static void unlock_output(pthread_mutex_t **mutex) {assert(!pthread_mutex_unlock(*mutex));}
#define guard(kind) pthread_mutex_t *held __attribute__((cleanup(unlock_output)))=lock_output
static int __spi_hid_send_output_report(struct spi_hid *s,struct spi_hid_output_report *report)
{
 assert(s==&shid && pthread_mutex_trylock(&s->output_lock)==EBUSY);
 assert(report->report_type==GET_FEATURE && !report->content && !report->content_length);
 calls++;response.body[0]=GET_FEATURE_RESPONSE;response.body[3]=report->content_id;
 for(unsigned i=0;i<sizeof(response.content);i++)response.content[i]=report->content_id+i;
 s->response_length=sizeof(response.content);s->output_done.done=1;return 0;
}
static int spi_hid_set_request(struct spi_hid *s,u8 *buf,size_t len,u8 id)
{(void)s;(void)buf;(void)len;(void)id;assert(0);return -EOPNOTSUPP;}
static void *checked_copy(void *destination,const void *input,size_t length)
{
 if(caller==1){
  pthread_mutex_lock(&events);copy_waiting=true;pthread_cond_broadcast(&condition);
  while(!release_copy)pthread_cond_wait(&condition,&events);
  pthread_mutex_unlock(&events);
 }
 return memcpy(destination,input,length);
}
#define memcpy checked_copy
''' + functions + r'''
static u8 buffers[2][17];
static void *query_thread(void *argument)
{
 caller=(int)(intptr_t)argument;
 int result=spi_hid_ll_raw_request(&hid,caller,buffers[caller-1],17,HID_FEATURE_REPORT,HID_REQ_GET_REPORT);
 assert(result==17);
 if(caller==2){
  pthread_mutex_lock(&events);second_finished=true;pthread_cond_broadcast(&condition);
  pthread_mutex_unlock(&events);
 }
 return NULL;
}
int main(void)
{
 pthread_t first,second;
 assert(!pthread_create(&first,NULL,query_thread,(void *)(intptr_t)1));
 pthread_mutex_lock(&events);
 while(!copy_waiting)pthread_cond_wait(&condition,&events);
 pthread_mutex_unlock(&events);
 assert(!pthread_create(&second,NULL,query_thread,(void *)(intptr_t)2));
 pthread_mutex_lock(&events);
#if LEGACY
 while(!second_finished)pthread_cond_wait(&condition,&events);
 assert(calls==2);
#else
 while(!second_lock_attempted)pthread_cond_wait(&condition,&events);
 assert(calls==1);
#endif
 release_copy=true;pthread_cond_broadcast(&condition);pthread_mutex_unlock(&events);
 assert(!pthread_join(first,NULL) && !pthread_join(second,NULL) && calls==2);
 for(unsigned i=0;i<2;i++)for(unsigned j=0;j<17;j++){
  unsigned expected=j?i+j:i+1;
  if(buffers[i][j]!=expected){
   fprintf(stderr,"caller %u byte %u: expected %u, received %u (shared reply replaced)\n",
           i+1,j,expected,buffers[i][j]);return 2;
  }
 }
 puts("SPI HID concurrent raw requests retain their own complete replies: PASS");return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-source', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=ROOT / 'spi-hid-core.c')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='p16-hid-race-') as temporary:
        for name, path, legacy in [('baseline', args.baseline_source, True),
                                   ('fixed', args.source, False)]:
            code, binary = Path(temporary) / (name + '.c'), Path(temporary) / name
            code.write_text(harness(path.read_text(), legacy))
            subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                            '-Wno-unused-function', '-Wno-unused-parameter',
                            '-fsanitize=address,undefined',
                            '-g', '-pthread', '-DLEGACY=' + str(int(legacy)),
                            str(code), '-o', str(binary)], check=True)
            result = subprocess.run([str(binary)], text=True, capture_output=True, timeout=20)
            if legacy:
                if result.returncode != 2 or 'shared reply replaced' not in result.stderr:
                    raise RuntimeError('The baseline did not reproduce reply replacement: ' +
                                       result.stdout + result.stderr)
                print('Prerequisite baseline reproduced: ' + result.stderr.strip())
            else:
                if result.returncode:
                    raise RuntimeError('Fixed callback failed: ' + result.stdout + result.stderr)
                print(result.stdout.strip())


if __name__ == '__main__':
    main()
