import copy
import json
import unittest
from pathlib import Path
from app import inspect
from records import load


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.data = copy.deepcopy(load(Path(__file__).parents[1] / "fixtures/data.json"))
        self.day = "2026-11-17"

    def test_end_to_end_inspection_is_json_and_repeatable(self):
        before = copy.deepcopy(self.data)
        first = inspect(self.data, self.day)
        self.assertEqual(first, inspect(self.data, self.day))
        self.assertEqual(before, self.data)
        self.assertTrue(json.dumps(first, ensure_ascii=False, allow_nan=False))

    def test_roster_coverage(self):
        from frame import roster, coverage
        self.assertEqual(len(roster(self.data, self.day, self.day)), 24)
        groups = coverage(self.data, self.day, self.day)
        self.assertLess(groups["east"]["observed_plots"], groups["east"]["plots"])

    def test_repeated_progress_is_not_summed(self):
        from harvest import progress, registered
        plots = progress(self.data, self.day, self.day)["plots"]
        self.assertEqual(len({row["plot_id"] for row in plots}), len(plots))
        self.assertAlmostEqual(sum(registered(self.data, self.day, self.day).values()), sum(row["registered_kg"] for row in plots))

    def test_future_visit_is_not_visible(self):
        from harvest import progress
        before = progress(self.data, self.day, self.day)
        self.data["visits"].append({**self.data["visits"][0], "harvested_kg": 99999, "recorded_at": "2026-12-01"})
        self.assertEqual(before, progress(self.data, self.day, self.day))

    def test_supply_is_shared_between_orders(self):
        from commitments import check_registered
        result = check_registered(self.data, "2026-11-20", self.day, True)
        total = sum(sum(row["allocated"].values()) for row in result["allocations"])
        from harvest import registered
        self.assertLessEqual(total, sum(registered(self.data, self.day, self.day).values()))

    def test_bad_probability_rejected(self):
        from frame import roster
        self.data["plots"][0]["selection_probability"] = 0
        with self.assertRaises(ValueError):
            roster(self.data, self.day, self.day)

    def test_area_cannot_exceed_plot(self):
        from harvest import progress
        self.data["visits"][0]["harvested_area_mu"] = 1000
        self.data["visits"][0]["observed_on"] = self.day
        with self.assertRaises(ValueError):
            progress(self.data, self.day, self.day)
