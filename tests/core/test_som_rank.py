"""SoM ranking: overlays beat background page when the cap truncates."""

import unittest

from mimicagent.core.capture import _is_overlay_container, rank_som_candidates
from mimicagent.core.element_ref import A11yRef, ElementRef, SemanticRef


class TestSomRank(unittest.TestCase):
    def test_overlay_kept_over_background_when_capped(self):
        sw, sh = 1920, 1080
        overlay = {"rect": (1000, 200, 1700, 900), "_dfs_index": 50, "name": "Apollo"}
        background = {
            "name": "My Network",
            "cx": 400,
            "cy": 400,
            "rect": (0, 0, 800, 800),
            "_dfs_index": 10,
        }
        panel = {
            "name": "name@company.com",
            "cx": 1200,
            "cy": 500,
            "rect": (1100, 400, 1400, 520),
            "_dfs_index": 80,
        }
        ranked = rank_som_candidates([background, panel], [overlay], sw, sh)
        self.assertEqual(ranked[0]["name"], "name@company.com")
        kept = ranked[:1]
        self.assertEqual(kept[0]["name"], "name@company.com")

    def test_later_dfs_beats_earlier_when_same_overlay(self):
        sw, sh = 1920, 1080
        overlay = {"rect": (0, 0, 1920, 1080), "_dfs_index": 1}
        a = {"name": "early", "cx": 960, "cy": 540, "rect": (10, 10, 20, 20), "_dfs_index": 2}
        b = {"name": "late", "cx": 960, "cy": 540, "rect": (10, 10, 20, 20), "_dfs_index": 99}
        ranked = rank_som_candidates([a, b], [overlay], sw, sh)
        self.assertEqual(ranked[0]["name"], "late")

    def test_unnamed_midsize_pane_is_not_overlay(self):
        sw, sh = 1920, 1080
        rect = (100, 100, 700, 700)  # ~600x600, would have matched the old size heuristic
        self.assertFalse(_is_overlay_container("Pane", "Feed", rect, sw, sh))
        self.assertTrue(_is_overlay_container("Dialog", "", rect, sw, sh))
        self.assertTrue(_is_overlay_container("Pane", "Apollo.io panel", rect, sw, sh))

    def test_subtree_root_field_exists(self):
        ref = ElementRef(
            a11y=A11yRef(control_type="Edit", subtree_root="New Message"),
            semantic=SemanticRef(description="To", app="chrome.exe"),
        )
        self.assertEqual(ref.a11y.subtree_root, "New Message")


if __name__ == "__main__":
    unittest.main()
