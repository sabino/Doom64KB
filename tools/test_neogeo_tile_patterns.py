#!/usr/bin/env python3
"""Small fixtures for the offline tile-pattern analysis; no game assets needed."""

from collections import Counter
from pathlib import Path
import subprocess
import sys
import unittest

import analyze_neogeo_tile_patterns as study


SCRIPT = Path(study.__file__).resolve()


class TilePatternTests(unittest.TestCase):
    def test_groups_and_slots(self):
        result = study.summarize(Counter({
            (0, 0): 2, (0, 14): 1, (14, 15): 1, (255, 255): 1,
        }))
        self.assertEqual(result["same_group_blocks"], 4)
        self.assertEqual(result["uniform_blocks"], 3)
        self.assertEqual(result["same_group_nonuniform_fraction"], 0.5)
        self.assertEqual(result["unique_same_group_slot_patterns"], 2)

    def test_static_union_coverage(self):
        views = [Counter({(14, 15): 3, (29, 30): 1}),
                 Counter({(0, 255): 4, (15, 30): 1})]
        union = sum(views, Counter())
        extra = study.palettes(union)
        self.assertTrue(all(len(p) <= 15 for p in extra))
        for view in views:
            self.assertTrue(all(
                any(set(p) <= palette for palette in study.FIXED_PALETTES + extra)
                for p in view
            ))
        reversed_input = Counter(dict(reversed(list(union.items()))))
        self.assertEqual(extra, study.palettes(reversed_input))

    def test_alignment(self):
        pixels = bytes(range(9))
        self.assertEqual(sum(study.blocks(pixels, 3, 3, 2, 2).values()), 1)
        self.assertEqual(sum(study.blocks(pixels, 3, 3, 2, 2, True).values()), 4)

    def test_report_has_no_patterns_or_palettes(self):
        result = study.summarize(Counter({(14, 15): 1}))
        self.assertNotIn("greedy_palette_color_sets", result)
        self.assertEqual(result["static_dictionary_uncovered_patterns"], 0)
        self.assertTrue(all(not isinstance(v, (set, tuple)) for v in result.values()))

    def test_cli_rejects_optimized_python(self):
        result = subprocess.run([sys.executable, "-O", str(SCRIPT)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("do not use Python -O", result.stderr)

    def test_cli_requires_paths(self):
        result = subprocess.run([sys.executable, str(SCRIPT)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--input", result.stderr)
        self.assertIn("--output", result.stderr)


if __name__ == "__main__":
    unittest.main()
