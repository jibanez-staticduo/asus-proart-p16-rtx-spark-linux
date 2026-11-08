#!/usr/bin/env python3
"""Check the validation client's bounded OPEN/HWFREE/CLOSE sequence."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def main():
    source = (ROOT / 'validation/n1x_adsp_task_check.c').read_text()
    function = source[source.index('static int __init n1x_adsp_task_check_init('):
                      source.index('static void __exit n1x_adsp_task_check_exit(')]
    code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "n1x_adsp_client.h"
typedef uint16_t u16;
#define __init
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define dev_info(...) ((void)0)
#define ERR_PTR(err) ((void *)(intptr_t)(err))
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
struct device {int unused;};
struct n1x_adsp_primary {int unused;};
static struct device device;
static struct n1x_adsp_primary primary;

static unsigned calls,refs;
static int open_error,close_error;
static bool missing;
static struct device *n1x_adsp_find_device(const char *name)
{assert(!strcmp(name,"NVDA8303"));
 if(missing)return NULL;refs++;return &device;}
static void put_device(struct device *dev) {assert(dev==&device && refs--==1);}
struct n1x_adsp_primary *n1x_adsp_primary_open(struct device *dev)
{
 assert(dev==&device && refs==1 && !calls++);
 return open_error?ERR_PTR(open_error):&primary;
}
int n1x_adsp_primary_close(struct n1x_adsp_primary *client)
{
 assert(client==&primary && refs==1 && calls++==1);return close_error;
}
''' + function + r'''
int main(void)
{
 assert(!n1x_adsp_task_check_init() && calls==2 && !refs);
 calls=0;open_error=-ETIMEDOUT;
 assert(n1x_adsp_task_check_init()==-ETIMEDOUT && calls==1 && !refs);
 calls=0;open_error=0;close_error=-EPROTO;
 assert(n1x_adsp_task_check_init()==-EPROTO && calls==2 && !refs);
 calls=0;open_error=-EHOSTDOWN;
 assert(n1x_adsp_task_check_init()==-EHOSTDOWN && calls==1 && !refs);
 calls=0;missing=true;assert(n1x_adsp_task_check_init()==-ENODEV && !calls && !refs);
 puts("DSP task client: typed open/close, error propagation and device references: PASS");
}
'''
    with tempfile.TemporaryDirectory(prefix='p16-dsp-task-') as temporary:
        path, binary = Path(temporary) / 'test.c', Path(temporary) / 'test'
        path.write_text(code)
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-misleading-indentation', '-fsanitize=address,undefined',
                        '-g', '-I' + str(ROOT), str(path), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=20)


if __name__ == '__main__':
    main()
