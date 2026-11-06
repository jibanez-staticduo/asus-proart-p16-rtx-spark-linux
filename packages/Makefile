# SPDX-License-Identifier: MIT
ROOT := $(abspath ..)
OUT ?= $(ROOT)/build/packages
MAKEPKG ?= makepkg
PACKAGES := p16-platform-policy p16-acpi-firmware p16-speaker-firmware

.PHONY: all $(PACKAGES)
all: $(PACKAGES)

$(PACKAGES):
	@mkdir -p "$(OUT)/$@"
	@set -eu; while IFS= read -r file; do \
		[ -n "$$file" ] || continue; \
		cp "$(ROOT)/$$file" "$(OUT)/$@/$${file##*/}"; \
	done < "$@/sources"
	cp "$@/PKGBUILD" "$(OUT)/$@/PKGBUILD"
	cd "$(OUT)/$@" && $(MAKEPKG) --force --noconfirm
