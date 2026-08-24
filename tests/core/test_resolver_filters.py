"""Unit tests for empty-name / degenerate-bbox rejection."""

import unittest

from mimicagent.core.element_ref import A11yRef, ElementRef, SemanticRef
from mimicagent.core.resolver import (
    bbox_is_degenerate,
    _format_som_candidates,
    _name_filters_match,
    _parse_json_object,
    _som_pick_from_obj,
)


class TestBboxAndNameFilters(unittest.TestCase):
    def test_zero_size_is_degenerate(self):
        self.assertTrue(bbox_is_degenerate(0, 0, 0, 0))
        self.assertTrue(bbox_is_degenerate(10, 10, 10, 20))
        self.assertTrue(bbox_is_degenerate(10, 10, 20, 10))

    def test_center_at_origin_is_degenerate(self):
        self.assertTrue(bbox_is_degenerate(-1, -1, 1, 1))

    def test_real_box_is_ok(self):
        self.assertFalse(bbox_is_degenerate(100, 200, 180, 240))

    def test_empty_control_name_never_matches(self):
        self.assertFalse(
            _name_filters_match("", name=None, name_contains=None, name_regex=None)
        )
        self.assertFalse(
            _name_filters_match("  ", name="To", name_contains=None, name_regex=None)
        )

    def test_name_contains_and_regex(self):
        self.assertTrue(
            _name_filters_match(
                "Ada Lovelace",
                name=None,
                name_contains=None,
                name_regex=r"[A-Za-z]+\s+[A-Za-z]+",
            )
        )
        self.assertTrue(
            _name_filters_match(
                "a@b.com",
                name=None,
                name_contains="@",
                name_regex=r"[^@]+@[^@]+\.[A-Za-z]{2,}",
            )
        )
        self.assertFalse(
            _name_filters_match(
                "Headline without at",
                name=None,
                name_contains=" at ",
                name_regex=None,
            )
        )

    def test_a11y_ref_accepts_new_fields(self):
        ref = ElementRef(
            a11y=A11yRef(
                control_type="Text",
                name_contains="@",
                name_regex=r".+@.+",
                nth_of_type=1,
            ),
            semantic=SemanticRef(
                description="email line",
                app="chrome.exe",
                window_title_hint="LinkedIn",
            ),
        )
        self.assertEqual(ref.a11y.name_contains, "@")
        self.assertEqual(ref.a11y.nth_of_type, 1)

    def test_parse_json_strips_fences_and_prose(self):
        obj = _parse_json_object('```json\n{"id": 3, "confidence": 0.9}\n```')
        self.assertEqual(obj["id"], 3)
        obj = _parse_json_object('Sure.\n{"id": null, "why": "none"}\n')
        self.assertIsNone(obj.get("id"))
        self.assertIsNone(_parse_json_object("just prose, no json"))

    def test_som_pick_rejects_out_of_range_id(self):
        elements = [
            {"id": 1, "cx": 10, "cy": 10, "control_type": "Button", "name": "A", "rect": (0, 0, 20, 20)},
            {"id": 2, "cx": 30, "cy": 10, "control_type": "Text", "name": "B", "rect": (20, 0, 40, 20)},
        ]
        listing = _format_som_candidates(elements)
        self.assertIn("id=1", listing)
        self.assertIn("bbox=", listing)
        el, cid, _why, _conf, bad = _som_pick_from_obj({"id": 510}, elements)
        self.assertIsNone(el)
        self.assertEqual(bad, 510)
        el, cid, _why, _conf, bad = _som_pick_from_obj({"id": 2, "confidence": 0.9}, elements)
        self.assertEqual(cid, 2)
        self.assertIsNone(bad)
        self.assertEqual(el["name"], "B")


if __name__ == "__main__":
    unittest.main()
