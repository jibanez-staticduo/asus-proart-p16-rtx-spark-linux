// SPDX-License-Identifier: GPL-2.0-only
#include <kunit/test.h>
#include <linux/module.h>

#include "../dsp/n1x_adsp_ipc.h"
#include "../dsp/n1x_adsp_pcm.h"
#include "../dsp/n1x_sroot_firmware.h"
#include "../pluton/n1x_pcbi_protocol.h"

static void n1x_ipc_packet_test(struct kunit *test)
{
	const u8 expected[] = {
		2, 0, 0, 0, 48, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
		0x5a, 0, 3, 0x10, 1, 1, 0x0a, 2, 32, 0, 0, 0, 0, 0, 0, 0,
	};
	u8 payload[32] = {1, 2, 3}, packet[N1X_ADSP_IPC_BYTES];
	struct n1x_adsp_ipc_request request = {
		.parameter1 = sizeof(payload), .token = 0x5a, .command = 0x20a,
		.channel = 3, .data_mode = 1, .ack_mode = 1, .payload = payload,
	};
	struct n1x_adsp_ipc_header header;

	KUNIT_ASSERT_EQ(test, n1x_adsp_ipc_encode(packet, sizeof(packet), &request), 0);
	KUNIT_EXPECT_MEMEQ(test, packet, expected, sizeof(expected));
	KUNIT_EXPECT_MEMEQ(test, packet + sizeof(expected), payload, sizeof(payload));
	KUNIT_ASSERT_EQ(test, n1x_adsp_ipc_decode(&header, packet, sizeof(packet)), 0);
	KUNIT_EXPECT_EQ(test, header.token, request.token);
	KUNIT_EXPECT_EQ(test, header.command, request.command);
	KUNIT_EXPECT_FALSE(test, n1x_adsp_ipc_ack_matches(&header, request.token));
	packet[19] = 0;
	packet[21] = 2;
	KUNIT_ASSERT_EQ(test, n1x_adsp_ipc_decode(&header, packet, sizeof(packet)), 0);
	KUNIT_EXPECT_TRUE(test, n1x_adsp_ipc_ack_matches(&header, request.token));
	KUNIT_EXPECT_FALSE(test, n1x_adsp_ipc_ack_matches(&header, request.token + 1));
}

static void n1x_ipc_bounds_test(struct kunit *test)
{
	struct n1x_adsp_ipc_request request = {.data_mode = 0, .ack_mode = 1};
	struct n1x_adsp_ipc_header header, before;
	u8 packet[N1X_ADSP_IPC_BYTES];
	size_t bytes;

	memset(&before, 0xa5, sizeof(before));
	for (bytes = 0; bytes < sizeof(packet); bytes++) {
		header = before;
		KUNIT_EXPECT_EQ(test, n1x_adsp_ipc_decode(&header, packet, bytes), -EMSGSIZE);
		KUNIT_EXPECT_MEMEQ(test, &header, &before, sizeof(header));
	}
	memset(packet, 0xa5, sizeof(packet));
	KUNIT_EXPECT_EQ(test, n1x_adsp_ipc_encode(packet, sizeof(packet) - 1, &request),
			-EMSGSIZE);
	KUNIT_EXPECT_EQ(test, packet[0], (u8)0xa5);
	request.data_mode = 2;
	KUNIT_EXPECT_EQ(test, n1x_adsp_ipc_encode(packet, sizeof(packet), &request),
			-EOPNOTSUPP);
}

static void n1x_pcm_descriptor_test(struct kunit *test)
{
	struct n1x_adsp_pcm_config config = {
		.address = 0xe0001000, .bytes = 7680, .rate = 48000, .channels = 2,
		.sample_bits = 16, .period_frames = 480, .periods = 4,
	};
	u8 packet[N1X_ADSP_PCM_BYTES];

	KUNIT_ASSERT_EQ(test, n1x_adsp_pcm_encode(packet, sizeof(packet), &config), 0);
	KUNIT_EXPECT_EQ(test, n1x_adsp_pcm_get64(packet), config.address);
	KUNIT_EXPECT_EQ(test, n1x_adsp_pcm_get64(packet + 8), config.address + config.bytes);
	KUNIT_EXPECT_EQ(test, n1x_adsp_ipc_get32(packet + 48), config.sample_bits);
	KUNIT_EXPECT_EQ(test, n1x_adsp_ipc_get32(packet + 52), config.rate);
	config.address = U64_MAX - config.bytes + 1;
	KUNIT_EXPECT_EQ(test, n1x_adsp_pcm_encode(packet, sizeof(packet), &config), -ERANGE);
	config.address = 0xe0001000;
	config.sample_bits = 24;
	KUNIT_EXPECT_EQ(test, n1x_adsp_pcm_encode(packet, sizeof(packet), &config), -EINVAL);
	config.sample_bits = 16;
	config.period_frames = U32_MAX;
	config.periods = U32_MAX;
	KUNIT_EXPECT_LT(test, n1x_adsp_pcm_encode(packet, sizeof(packet), &config), 0);
}

static void n1x_pcbi_packet_test(struct kunit *test)
{
	const u8 expected[] = {1, 1, 0, 0, 1, 1, 0, 0, 7, 0, 0, 0};
	u8 reply[] = {1, 1, 2, 0, 1, 2, 0, 0, 7, 0, 0, 0,
		      0xbd, 0xac, 0x86, 0x79, 1, 1};
	u8 request[12];
	size_t bytes;

	KUNIT_ASSERT_EQ(test, n1x_pcbi_query_request(request, 7), 0);
	KUNIT_EXPECT_MEMEQ(test, request, expected, sizeof(request));
	KUNIT_EXPECT_EQ(test, n1x_pcbi_query_request(request, 3), -EINVAL);
	KUNIT_EXPECT_EQ(test, n1x_pcbi_validate_reply(reply, sizeof(reply), 7), 0);
	for (bytes = 0; bytes < sizeof(reply); bytes++)
		KUNIT_EXPECT_EQ(test, n1x_pcbi_validate_reply(reply, bytes, 7), -EPROTO);
	reply[8] = 5;
	KUNIT_EXPECT_EQ(test, n1x_pcbi_validate_reply(reply, sizeof(reply), 7), -EPROTO);
	reply[8] = 7;
	reply[16] = 255;
	KUNIT_EXPECT_EQ(test, n1x_pcbi_validate_reply(reply, sizeof(reply), 7), -EPROTO);
}

static void n1x_pcbi_device_info_test(struct kunit *test)
{
	u8 body[44] = {6, 1};

	memcpy(body + 8, "PltnFull", 8);
	body[40] = 1;
	body[41] = 6;
	KUNIT_EXPECT_EQ(test, n1x_pcbi_validate_device_info(body, sizeof(body)), 0);
	body[42] = 64;
	KUNIT_EXPECT_EQ(test, n1x_pcbi_validate_device_info(body, sizeof(body)), -EPROTO);
	body[42] = 0;
	body[41] = 0xf0;
	KUNIT_EXPECT_EQ(test, n1x_pcbi_validate_device_info(body, sizeof(body)), -EPROTO);
}

static void n1x_sroot_envelope_test(struct kunit *test)
{
	u8 image[128] = {0x59};
	u8 *native = image + 3;
	size_t bytes;

	n1x_adsp_ipc_put32(native, 1);
	n1x_adsp_ipc_put32(native + 4, 2);
	n1x_adsp_ipc_put32(native + 8, 0xefbeadde);
	n1x_adsp_ipc_put32(native + 12, 0xefbeadde);
	n1x_adsp_ipc_put32(native + 16, 0xc0304);
	n1x_adsp_ipc_put32(native + 32, 80);
	n1x_adsp_ipc_put32(native + 44, 0xefbeadde);
	n1x_adsp_ipc_put32(native + 48, 0xc0304);
	n1x_adsp_ipc_put32(native + 56, 80);
	n1x_adsp_ipc_put32(native + 64, sizeof(image) - 83);
	n1x_adsp_ipc_put32(native + 76, 0xefbeadde);
	KUNIT_ASSERT_EQ(test, n1x_sroot_validate_image(image, sizeof(image), 0xc0304), 0);
	for (bytes = 0; bytes < sizeof(image); bytes++)
		KUNIT_EXPECT_EQ(test, n1x_sroot_validate_image(image, bytes, 0xc0304), -EBADMSG);
	KUNIT_EXPECT_EQ(test, n1x_sroot_validate_image(image, sizeof(image), 0xc0303), -EBADMSG);
	n1x_adsp_ipc_put32(native + 4, U32_MAX);
	KUNIT_EXPECT_EQ(test, n1x_sroot_validate_image(image, sizeof(image), 0xc0304), -EBADMSG);
}

static struct kunit_case n1x_protocol_cases[] = {
	KUNIT_CASE(n1x_ipc_packet_test),
	KUNIT_CASE(n1x_ipc_bounds_test),
	KUNIT_CASE(n1x_pcm_descriptor_test),
	KUNIT_CASE(n1x_pcbi_packet_test),
	KUNIT_CASE(n1x_pcbi_device_info_test),
	KUNIT_CASE(n1x_sroot_envelope_test),
	{}
};

static struct kunit_suite n1x_protocol_suite = {
	.name = "n1x-platform-protocol",
	.test_cases = n1x_protocol_cases,
};

kunit_test_suite(n1x_protocol_suite);

MODULE_LICENSE("GPL");
