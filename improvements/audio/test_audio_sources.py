#!/usr/bin/env python3
"""Guard the public audio contribution against OEM assets and private paths."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class PublicAudioTests(unittest.TestCase):
    def test_driver_patch_is_hardware_gated_and_preserves_upstream_identity(self):
        patch = (ROOT / "route-h7607ba.patch").read_text()
        self.assertIn("DMI_EXACT_MATCH(DMI_SYS_VENDOR, \"ASUS\")", patch)
        self.assertIn("DMI_EXACT_MATCH(DMI_PRODUCT_NAME, \"ProArt P16 H7607BA\")", patch)
        self.assertIn("DMI_EXACT_MATCH(DMI_BOARD_NAME, \"H7607BA\")", patch)
        self.assertIn('acpi_dev_present("NVDA6210", NULL, -1)', patch)
        self.assertEqual(patch.count('"cs42l43 AMP'), 4)
        self.assertIn("cs42l43 AMP1_OUT_P", patch)
        base = (ROOT / "mt8901-acp-sdw-legacy-mach.c").read_text()
        self.assertIn("SPDX-License-Identifier: GPL-2.0", base)
        self.assertIn("Copyright (c) 2026 MediaTek Inc.", base)

    def test_graph_has_only_open_pipewire_and_lsp_nodes(self):
        graph = (ROOT / "p16-two-way.conf").read_text()
        self.assertIn('plugin = "http://lsp-plug.in/plugins/lv2/limiter_stereo"', graph)
        self.assertEqual(graph.count('label = bq_lowpass'), 4)
        self.assertEqual(graph.count('label = bq_highpass'), 4)
        self.assertEqual(graph.count('"Freq" = 3000.0'), 8)
        self.assertEqual(graph.count('"Q" = 0.7071'), 8)
        self.assertIn("th = 0.8912509381", graph)
        self.assertIn('node.name = "p16_two_way_split"', graph)
        self.assertIn('node.name = "p16_two_way"', graph)
        self.assertIn('media.class = "Audio/Sink/Internal"', graph)
        self.assertIn("combine.audio.position = [ FL FR ]", graph)
        self.assertIn("combine.audio.position = [ RL RR ]", graph)
        self.assertIn('target.object = "p16_two_way_split"', graph)
        self.assertNotIn("p16_dolby_speakers", graph)
        self.assertNotIn("/home/", graph)

    def test_graph_and_helpers_have_no_private_distribution_paths(self):
        for path in ROOT.glob("*"):
            if (not path.is_file() or path.name == "test_audio_sources.py"
                    or path.name == "README.md"
                    or path.suffix not in {".md", ".conf", ".sh", ".py"}):
                continue
            text = path.read_text()
            self.assertNotIn("/home/", text)

if __name__ == "__main__":
    unittest.main()
