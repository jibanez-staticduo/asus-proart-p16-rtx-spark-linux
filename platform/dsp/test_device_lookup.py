#!/usr/bin/env python3
"""Run the real firmware-scoped device lookup with reference-counted ACPI edges."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def main():
    source = (ROOT/'n1x_adsp_client.h').read_text()
    function = source[source.index('static inline struct device *n1x_adsp_find_device('):source.index('#endif')]
    code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
struct acpi_device;
struct device {struct acpi_device *acpi;unsigned refs;};
struct acpi_device {struct acpi_device *parent;struct device *dev;const char *hid;unsigned refs;bool listed;};
static struct acpi_device nodes[4];
static int platform_bus_type;
#define ACPI_COMPANION(d) ((d)->acpi)
static struct acpi_device *acpi_dev_parent(struct acpi_device *d){return d->parent;}
static struct acpi_device *acpi_dev_get(struct acpi_device *d){d->refs++;return d;}
static void acpi_dev_put(struct acpi_device *d){if(d){assert(d->refs>1);d->refs--;}}
static struct acpi_device *acpi_dev_get_next_match_dev(struct acpi_device *prev,const char *hid,const char *uid,int hrv)
{assert(!uid && hrv==-1);unsigned start=prev?(unsigned)(prev-nodes+1):0;acpi_dev_put(prev);
 for(unsigned i=start;i<4;i++)if(nodes[i].listed && !strcmp(nodes[i].hid,hid))return acpi_dev_get(&nodes[i]);return NULL;}
static struct device *bus_find_device_by_acpi_dev(int *bus,struct acpi_device *a)
{assert(bus==&platform_bus_type);if(a->dev)a->dev->refs++;return a->dev;}
''' + function + r'''
static void check_refs(void){for(unsigned i=0;i<4;i++)assert(nodes[i].refs==1);}
int main(void)
{
 struct acpi_device parent={0},other={0},consumer_node={.parent=&parent};
 struct device consumer={&consumer_node,1},missing={0},target={&nodes[0],1};
 for(unsigned i=0;i<4;i++)nodes[i]=(struct acpi_device){.parent=&parent,.hid="NVDA8303",.refs=1};
 assert(!n1x_adsp_find_device(&missing,"NVDA8303"));
 assert(!n1x_adsp_find_device(&consumer,"NVDA8303"));check_refs();
 nodes[0].listed=true;nodes[0].dev=&target;
 assert(n1x_adsp_find_device(&consumer,"NVDA8303")==&target);assert(target.refs--==2);check_refs();
 assert(n1x_adsp_find_device(NULL,"NVDA8303")==&target);assert(target.refs--==2);check_refs();
 nodes[1].listed=true;nodes[1].parent=&other;
 assert(n1x_adsp_find_device(&consumer,"NVDA8303")==&target);assert(target.refs--==2);check_refs();
 assert(!n1x_adsp_find_device(NULL,"NVDA8303"));check_refs();
 nodes[1].parent=&parent;assert(!n1x_adsp_find_device(&consumer,"NVDA8303"));check_refs();
 nodes[1].hid="NVDA9022";assert(n1x_adsp_find_device(&consumer,"NVDA8303")==&target);assert(target.refs--==2);check_refs();
 nodes[0].dev=NULL;assert(!n1x_adsp_find_device(&consumer,"NVDA8303"));check_refs();
 puts("DSP/AFE lookup: firmware scope, global ambiguity, missing physical device and ACPI/device references: PASS");
}
'''
    with tempfile.TemporaryDirectory(prefix='n1x-lookup-') as tmp:
        test, binary = Path(tmp)/'test.c', Path(tmp)/'test'
        test.write_text(code)
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-misleading-indentation', '-fsanitize=address,undefined',
                        str(test), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    main()
