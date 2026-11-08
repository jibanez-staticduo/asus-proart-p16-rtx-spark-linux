// SPDX-License-Identifier: GPL-2.0-only
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

#include "n1x_adsp_ipc.h"

int main(void)
{
	/* Constructor fields from nvasd 0x140002268; 32-byte inline audio data. */
	const __u8 expected[32] = {
		2, 0, 0, 0, 48, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
		0x5a, 0, 3, 0x10, 1, 1, 0x0a, 2, 32, 0, 0, 0, 0, 0, 0, 0,
	};
	__u8 payload[256], packet[288], before[288];
	struct n1x_adsp_ipc_request request = {
		.parameter1 = 32, .token = 0x5a, .command = 0x20a,
		.channel = 3, .data_mode = 1, .ack_mode = 1, .payload = payload,
	};
	struct n1x_adsp_ipc_header header, old;
	size_t i;

	for (i = 0; i < sizeof(payload); i++)
		payload[i] = i;
	assert(!n1x_adsp_ipc_encode(packet, sizeof(packet), &request));
	assert(!memcmp(packet, expected, sizeof(expected)));
	assert(!memcmp(packet + 32, payload, 32));
	for (i = 64; i < sizeof(packet); i++)
		assert(!packet[i]);
	request.parameter1 = 256;
	assert(!n1x_adsp_ipc_encode(packet, sizeof(packet), &request));
	assert(!memcmp(packet + 32, payload, 256));
	assert(n1x_adsp_ipc_get32(packet + 4) == 272);
	assert(!n1x_adsp_ipc_decode(&header, packet, sizeof(packet)));
	assert(header.command == 0x20a && header.token == 0x5a && header.parameter1 == 256);

	/* Rejection leaves the destination untouched, including oversized data. */
	memcpy(before, packet, sizeof(packet));
	request.parameter1 = 257;
	assert(n1x_adsp_ipc_encode(packet, sizeof(packet), &request) == -EMSGSIZE);
	assert(!memcmp(packet, before, sizeof(packet)));
	request.parameter1 = 32;
	request.payload = NULL;
	assert(n1x_adsp_ipc_encode(packet, sizeof(packet), &request) == -EMSGSIZE);
	request.payload = payload;
	request.data_mode = 2;
	assert(n1x_adsp_ipc_encode(packet, sizeof(packet), &request) == -EOPNOTSUPP);
	request.data_mode = 1;
	request.ack_mode = 2;
	assert(n1x_adsp_ipc_encode(packet, sizeof(packet), &request) == -EOPNOTSUPP);
	request.ack_mode = 1;
	assert(n1x_adsp_ipc_encode(packet, sizeof(packet) - 1, &request) == -EMSGSIZE);
	assert(!memcmp(packet, before, sizeof(packet)));

	/* Scalar parameters do not become a length or cause a payload read. */
	request.data_mode = 0;
	request.parameter1 = 0x12345678;
	request.parameter2 = 0x87654321;
	request.payload = NULL;
	assert(!n1x_adsp_ipc_encode(packet, sizeof(packet), &request));
	assert(n1x_adsp_ipc_get32(packet + 4) == 16);
	assert(n1x_adsp_ipc_get32(packet + 24) == 0x12345678);
	assert(n1x_adsp_ipc_get32(packet + 28) == 0x87654321);
	for (i = 32; i < sizeof(packet); i++)
		assert(!packet[i]);

	/* Exact-size allocations make truncated receive buffers visible to ASan. */
	memset(&old, 0xa5, sizeof(old));
	for (i = 0; i < sizeof(packet); i++) {
		__u8 *short_packet = malloc(i ? i : 1);

		assert(short_packet);
		header = old;
		assert(n1x_adsp_ipc_decode(&header, short_packet, i) == -EMSGSIZE);
		assert(!memcmp(&header, &old, sizeof(header)));
		free(short_packet);
	}
	/* Notification and ACK completions have distinct wire classes. */
	memset(packet, 0, sizeof(packet));
	packet[16] = 0x5a;
	packet[19] = 0x20;
	packet[21] = 2;
	assert(!n1x_adsp_ipc_decode(&header, packet, sizeof(packet)));
	assert(!n1x_adsp_ipc_ack_matches(&header, 0x5a));
	packet[19] = 0;
	assert(!n1x_adsp_ipc_decode(&header, packet, sizeof(packet)));
	assert(n1x_adsp_ipc_ack_matches(&header, 0x5a));
	assert(!n1x_adsp_ipc_ack_matches(&header, 0x59));
	assert(!n1x_adsp_ipc_ack_matches(&header, 0));
	assert(!n1x_adsp_ipc_ack_matches(NULL, 0x5a));
	puts("DSP packet wire vectors, bounds and ACK/notification matching: PASS");
	return 0;
}
