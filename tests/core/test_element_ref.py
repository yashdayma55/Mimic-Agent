"""Unit tests for ElementRef validation (stdlib unittest — no pytest dep)."""

import unittest

from pydantic import ValidationError

from mimicagent.core.element_ref import A11yRef, ElementRef, SemanticRef, VisualRef


def _semantic() -> SemanticRef:
    return SemanticRef(
        description="Extensions puzzle-piece icon in Chrome toolbar",
        app="chrome.exe",
        window_title_hint="Chrome",
    )


class TestElementRef(unittest.TestCase):
    def test_requires_a11y_or_visual(self):
        with self.assertRaises(ValidationError):
            ElementRef(semantic=_semantic())

    def test_with_a11y_only(self):
        ref = ElementRef(
            a11y=A11yRef(name="Extensions", control_type="Button"),
            semantic=_semantic(),
        )
        self.assertIsNotNone(ref.a11y)
        self.assertIsNone(ref.visual)

    def test_with_visual_only(self):
        ref = ElementRef(
            visual=VisualRef(crop_path="fake.png"),
            semantic=_semantic(),
        )
        self.assertIsNotNone(ref.visual)
        self.assertIsNone(ref.a11y)

    def test_with_both(self):
        ref = ElementRef(
            a11y=A11yRef(automation_id="ExtBtn"),
            visual=VisualRef(rel_bbox=(0.1, 0.1, 0.2, 0.2)),
            semantic=_semantic(),
        )
        self.assertTrue(ref.a11y and ref.visual)

    def test_semantic_required(self):
        with self.assertRaises(ValidationError):
            ElementRef(a11y=A11yRef(name="x"))  # type: ignore[call-arg]


if __name__ == "__main__":
    unittest.main()
