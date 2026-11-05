/* SPDX-License-Identifier: GPL-2.0 WITH Linux-syscall-note */
#ifndef N1X_PLUTON_UAPI_H
#define N1X_PLUTON_UAPI_H

#include <linux/ioctl.h>
#include <linux/types.h>

struct n1x_pluton_info {
	__u32 abi_version;
	__u32 boot_status;
	__u32 register_stride;
	__u32 transport_enabled;
	__u32 initialized;
	__u32 poisoned;
	__u32 last_pfi_status;
	__u32 reserved;
};

struct n1x_pluton_channels {
	__u32 count;
	__u32 reserved;
	struct {
		__u8 id;
		__u8 flags;
	} channel[16];
};

struct n1x_pluton_exchange {
	__u32 channel;
	__u32 tx_size;
	__u32 rx_size;
	__u32 rx_actual;
	__aligned_u64 tx_ptr;
	__aligned_u64 rx_ptr;
	__u32 remote_status;
	__u32 reserved;
};

#define N1X_PLUTON_INFO _IOR('P', 0, struct n1x_pluton_info)
#define N1X_PLUTON_CHANNELS _IOR('P', 1, struct n1x_pluton_channels)
#define N1X_PLUTON_EXCHANGE _IOWR('P', 2, struct n1x_pluton_exchange)

#endif
