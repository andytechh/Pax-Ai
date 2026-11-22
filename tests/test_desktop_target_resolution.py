"""Regression tests for conservative Windows UIA target resolution."""
import unittest

from core.desktop.controller import DesktopController


class DesktopTargetScoringTests(unittest.TestCase):
    def item(self, name, role, automation_id=""):
        return {"name": name, "control_type": role, "automation_id": automation_id}

    def test_exact_button_label_wins(self):
        score = DesktopController._score_control(
            "Start recording", self.item("Start recording", "Button"), "Button"
        )
        self.assertGreaterEqual(score, 1.1)

    def test_search_field_is_not_a_button_substitute(self):
        score = DesktopController._score_control(
            "Start", self.item("Search", "Edit"), "Button"
        )
        self.assertEqual(score, 0.0)

    def test_weak_fuzzy_match_is_rejected(self):
        score = DesktopController._score_control(
            "Stop recording", self.item("Search", "Edit")
        )
        self.assertEqual(score, 0.0)

    def test_wrong_role_is_rejected(self):
        score = DesktopController._score_control(
            "Start", self.item("Start", "Text"), "Button"
        )
        self.assertEqual(score, 0.0)

    def test_automation_id_can_match_exactly(self):
        score = DesktopController._score_control(
            "record_toggle", self.item("", "Button", "record_toggle"), "Button"
        )
        self.assertGreaterEqual(score, 1.1)


if __name__ == "__main__":
    unittest.main()
