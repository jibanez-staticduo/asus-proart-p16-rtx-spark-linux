# SPDX-License-Identifier: GPL-2.0-only
KDIR ?= /lib/modules/$(shell uname -r)/build

.PHONY: all modules modules_install clean
all: modules

modules modules_install clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) CONFIG_N1X_ADSP=m CONFIG_N1X_SCP=m CONFIG_N1X_PLUTON=m $@
