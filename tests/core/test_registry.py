"""Unit tests for the action registry (stdlib unittest)."""

import unittest

from mimicagent.core.actions import REGISTRY, get_action


REQUIRED = {
    "focus_app",
    "navigate",
    "click",
    "type_text",
    "read_text",
    "wait_for",
    "extract",
    "copy",
    "paste",
    "scroll_to",
    "llm_generate",
    "human_approve",
}


class TestRegistry(unittest.TestCase):
    def test_contains_required_actions(self):
        self.assertTrue(REQUIRED.issubset(set(REGISTRY.keys())))

    def test_get_action_lookup(self):
        spec = get_action("focus_app")
        self.assertEqual(spec.name, "focus_app")
        self.assertFalse(spec.is_irreversible)

    def test_get_action_unknown_raises(self):
        with self.assertRaises(KeyError) as cm:
            get_action("not_a_real_action")
        self.assertIn("Unknown action", str(cm.exception))
        self.assertIn("focus_app", str(cm.exception))

    def test_irreversible_flags(self):
        self.assertTrue(get_action("type_text").is_irreversible)
        self.assertTrue(get_action("human_approve").is_irreversible)
        self.assertFalse(get_action("navigate").is_irreversible)
        self.assertFalse(get_action("click").is_irreversible)
        self.assertFalse(get_action("read_text").is_irreversible)


if __name__ == "__main__":
    unittest.main()
