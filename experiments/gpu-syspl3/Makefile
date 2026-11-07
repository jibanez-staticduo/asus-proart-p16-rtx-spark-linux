# SPDX-License-Identifier: MIT
CUDA_HOME ?= /opt/cuda
NVCC ?= $(CUDA_HOME)/bin/nvcc
CFLAGS ?= -O2 -Wall -Wextra -Werror
CPPFLAGS += -I$(CUDA_HOME)/include
LDLIBS += -lnvidia-ml
OUT := out

.PHONY: all check analyze clean
all: $(OUT)/study $(OUT)/study-confirm $(OUT)/gemm-stream

$(OUT):
	mkdir -p $@

$(OUT)/study: study.c | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LDLIBS) -o $@

$(OUT)/study-confirm: study.c | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -DCONFIRM_RUN -DBASELINE_SECONDS=6 \
		-DLIMITED_SECONDS=4 -DRECOVERY_SECONDS=6 $< $(LDLIBS) -o $@

$(OUT)/gemm-stream: gemm-stream.cu | $(OUT)
	$(NVCC) -O2 -std=c++17 -arch=sm_121 $< -lcublas -o $@

$(OUT)/check-kernel-guard: check-kernel-guard.c study.c | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LDLIBS) -o $@

check: $(OUT)/study $(OUT)/check-kernel-guard
	$(OUT)/study --self-test
	$(OUT)/check-kernel-guard

analyze: | $(OUT)
	python3 analyze.py --output $(OUT)/results.json

clean:
	rm -rf $(OUT)
