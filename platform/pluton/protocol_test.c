// SPDX-License-Identifier: GPL-2.0-only
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "n1x_pluton_protocol.h"
#include "n1x_pluton_uapi.h"

_Static_assert(sizeof(struct n1x_pluton_info) == 32, "info ABI");
_Static_assert(sizeof(struct n1x_pluton_channels) == 40, "channels ABI");
_Static_assert(sizeof(struct n1x_pluton_exchange) == 40, "exchange ABI");

int main(void)
{
	const __u8 query[] = {0, 0, 0, 0};
	const __u8 write_expected[] = {0, 0, 2, 0, 0, 0, 4, 0, 0, 0, 0, 0};
	const __u8 read_expected[] = {0, 7, 1, 0, 40, 0, 0, 0};
	__u8 packet[52], data[44], reply[16] = {0};
	__u32 status = 0, seed = 0x86018901;
	__u16 count = 0;
	int more = 0;
	size_t i, n;

	assert(n1x_pfi_write_packet(packet, 0, query, sizeof(query), 0) == 12);
	assert(memcmp(packet, write_expected, sizeof(write_expected)) == 0);
	for (i = 12; i < sizeof(packet); i++)
		assert(packet[i] == 0);
	memset(data, 0xa5, sizeof(data));
	assert(n1x_pfi_write_packet(packet, 255, data, 44, 1) == 52);
	assert(packet[1] == 255 && packet[2] == 10 && packet[6] == 44);
	assert(memcmp(packet + 8, data, 44) == 0);
	assert(n1x_pfi_write_packet(packet, 0, data, 45, 0) == -EMSGSIZE);
	assert(n1x_pfi_write_packet(packet, 0, NULL, 1, 0) == -EMSGSIZE);
	n1x_pfi_read_packet(packet, 7);
	assert(memcmp(packet, read_expected, sizeof(read_expected)) == 0);

	/* Read and write replies deliberately put status at different offsets. */
	reply[0] = 0x78; reply[1] = 0x56; reply[2] = 0x34; reply[3] = 0x12;
	reply[4] = 4;
	assert(n1x_pfi_decode_write(reply, sizeof(reply), &status) == 0 && status == 4);
	reply[8] = 1; reply[10] = 4;
	assert(n1x_pfi_decode_read(reply, sizeof(reply), &status, &count, &more) == 0);
	assert(status == 0x12345678 && count == 4 && more == 1);
	reply[10] = 5;
	assert(n1x_pfi_decode_read(reply, sizeof(reply), &status, &count, &more) == -EPROTO);
	reply[10] = 0; reply[11] = 1;
	assert(n1x_pfi_decode_read(reply, sizeof(reply), &status, &count, &more) == -EPROTO);

	/* Exact-size allocations make truncated-packet reads visible to ASan.
	 * Exercise every header truncation and every possible fragment length.
	 */
	for (n = 0; n <= 16; n++) {
		__u8 *short_packet = malloc(n ? n : 1);

		assert(short_packet);
		for (i = 0; i < 4096; i++) {
			size_t j;
			int ret;

			for (j = 0; j < n; j++) {
				seed = seed * 1664525U + 1013904223U;
				short_packet[j] = seed >> 24;
			}
			ret = n1x_pfi_decode_write(short_packet, n, &status);
			assert(ret == (n < 12 ? -EPROTO : 0));
			ret = n1x_pfi_decode_read(short_packet, n, &status, &count, &more);
			if (n < 12)
				assert(ret == -EPROTO);
			else
				assert(ret == (n1x_pfi_le16(short_packet + 10) > n - 12 ? -EPROTO : 0));
		}
		free(short_packet);
	}
	puts("PFI wire vectors, truncated packets and ioctl layouts: PASS");
	return 0;
}
