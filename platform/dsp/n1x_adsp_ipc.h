/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef N1X_ADSP_IPC_H
#define N1X_ADSP_IPC_H

#include <linux/errno.h>
#include <linux/types.h>
#ifdef __KERNEL__
#include <linux/string.h>
#else
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#endif

#define N1X_ADSP_IPC_BYTES 288U
#define N1X_ADSP_IPC_PAYLOAD_BYTES 256U

/* Internal packet representation; this is not a userspace ABI. */
struct n1x_adsp_ipc_request {
	__u32 parameter1, parameter2;
	__u16 token, command;
	__u8 channel, data_mode, ack_mode;
	const __u8 *payload;
};

struct n1x_adsp_ipc_header {
	__u32 word0, length, parameter1, parameter2;
	__u16 token, command;
	__u8 channel, message_class, flags, data_mode, ack_mode;
};

static inline __u16 n1x_adsp_ipc_get16(const __u8 *p)
{
	return (__u16)p[0] | (__u16)p[1] << 8;
}

static inline __u32 n1x_adsp_ipc_get32(const __u8 *p)
{
	return (__u32)p[0] | (__u32)p[1] << 8 | (__u32)p[2] << 16 | (__u32)p[3] << 24;
}

static inline void n1x_adsp_ipc_put16(__u8 *p, __u16 value)
{
	p[0] = value;
	p[1] = value >> 8;
}

static inline void n1x_adsp_ipc_put32(__u8 *p, __u32 value)
{
	p[0] = value;
	p[1] = value >> 8;
	p[2] = value >> 16;
	p[3] = value >> 24;
}

/* nvasd's constructor supports scalar parameters and an inline data payload.
 * Build privately so rejected inputs cannot partly alter a published packet.
 * The transport owns token allocation and must serialize publication.
 */
static inline int n1x_adsp_ipc_encode(__u8 *packet, size_t capacity,
				    const struct n1x_adsp_ipc_request *request)
{
	__u8 prepared[N1X_ADSP_IPC_BYTES] = {};

	if (!packet || !request)
		return -EINVAL;
	if (capacity < sizeof(prepared))
		return -EMSGSIZE;
	if (request->data_mode > 1 || request->ack_mode > 1)
		return -EOPNOTSUPP;
	if (request->data_mode == 1 &&
	    (!request->payload || request->parameter1 > N1X_ADSP_IPC_PAYLOAD_BYTES))
		return -EMSGSIZE;
	n1x_adsp_ipc_put32(prepared, 2);
	n1x_adsp_ipc_put32(prepared + 4, 16 +
			  (request->data_mode == 1 ? request->parameter1 : 0));
	n1x_adsp_ipc_put16(prepared + 16, request->token);
	prepared[18] = request->channel;
	prepared[19] = 0x10;
	prepared[20] = request->data_mode;
	prepared[21] = request->ack_mode;
	n1x_adsp_ipc_put16(prepared + 22, request->command);
	n1x_adsp_ipc_put32(prepared + 24, request->parameter1);
	n1x_adsp_ipc_put32(prepared + 28, request->parameter2);
	if (request->data_mode == 1)
		memcpy(prepared + 32, request->payload, request->parameter1);
	memcpy(packet, prepared, sizeof(prepared));
	return 0;
}

/* Decode only observed fields. Receive packets are not required to have the
 * transmit constructor's word0 or length values without firmware evidence.
 */
static inline int n1x_adsp_ipc_decode(struct n1x_adsp_ipc_header *header,
				    const __u8 *packet, size_t bytes)
{
	if (!header || !packet)
		return -EINVAL;
	if (bytes != N1X_ADSP_IPC_BYTES)
		return -EMSGSIZE;
	*header = (struct n1x_adsp_ipc_header) {
		.word0 = n1x_adsp_ipc_get32(packet),
		.length = n1x_adsp_ipc_get32(packet + 4),
		.token = n1x_adsp_ipc_get16(packet + 16),
		.channel = packet[18],
		.message_class = packet[19] >> 4,
		.flags = packet[19] & 0xf,
		.data_mode = packet[20],
		.ack_mode = packet[21],
		.command = n1x_adsp_ipc_get16(packet + 22),
		.parameter1 = n1x_adsp_ipc_get32(packet + 24),
		.parameter2 = n1x_adsp_ipc_get32(packet + 28),
	};
	return 0;
}

static inline bool n1x_adsp_ipc_ack_matches(const struct n1x_adsp_ipc_header *header,
					 __u16 token)
{
	return header && token && header->message_class == 0 &&
	       header->ack_mode == 2 && header->token == token;
}

#endif
