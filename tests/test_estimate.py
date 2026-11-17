import copy
import json
import tempfile
import unittest
from pathlib import Path

from analyses import build_analysis, issue_analysis, list_analyses, read_analysis, replay
from app import estimate as run_estimate
from estimate import (GAP_NO_DOMAIN_BASIS, LATE_FINISH,
                      yield_outlook)
from records import load, quantity
from risk import evaluate_risk, order_combinations


def plot(plot_id, group, area, probability=0.5, band="river",
         valid_from="2026-09-01"):
    return {"plot_id": plot_id, "group": group, "area_mu": area,
            "valid_from": valid_from, "valid_to": None,
            "recorded_at": "2026-09-01",
            "selection_probability": probability, "soil_band": band}


def visit(visit_id, plot_id, observed_on, area, kg, complete=False,
          finish="2026-11-19", recorded_at=None):
    return {"visit_id": visit_id, "plot_id": plot_id,
            "observed_on": observed_on, "recorded_at": recorded_at or observed_on,
            "harvested_area_mu": area, "harvested_kg": kg,
            "complete": complete, "expected_finish": finish}


def order(order_id, due_on, kg, status, group=None):
    return {"order_id": order_id, "due_on": due_on, "group": group,
            "kg": kg, "status": status}


def synthetic_data():
    return {
        "plots": [
            plot("E1", "east", 4, probability=1.0),
            plot("E2", "east", 4, probability=0.25),
            plot("E3", "east", 4, probability=0.25, band="hill"),
            plot("W1", "west", 4, probability=0.5),
            plot("W2", "west", 4, probability=0.5, band="hill"),
        ],
        "visits": [
            visit("V1", "E1", "2026-11-10", 2, 1000, finish="2026-11-19"),
            visit("V2", "E2", "2026-11-10", 2, 600, finish="2026-11-25"),
            visit("V3", "W1", "2026-11-10", 2, 800, finish="2026-11-19"),
        ],
        "orders": [
            order("C1", "2026-11-20", 1000, "confirmed"),
            order("C2", "2026-11-20", 3000, "proposed", group="east"),
        ],
    }


class EstimateTests(unittest.TestCase):
    def setUp(self):
        self.data = synthetic_data()
        self.known = "2026-11-17"
        self.due = "2026-11-20"

    def test_repeated_visits_count_as_one_sample_unit(self):
        data = self.data
        data["visits"].append(visit("V1b", "E1", "2026-11-11", 2, 1010,
                                    finish="2026-11-19"))
        outlook = yield_outlook(data, self.due, self.known)
        self.assertEqual(outlook["coverage"]["visit_records_used"], 4)
        self.assertEqual(outlook["coverage"]["sample_plots"], 3)
        basis = next(row for row in outlook["rate_basis"]
                     if row["group"] == "east" and row["soil_band"] == "river")
        self.assertEqual(basis["sample_plots"], 2)
        self.assertEqual(basis["plot_ids"], ["E1", "E2"])

    def test_weighted_rate_corrects_unequal_probability(self):
        outlook = yield_outlook(self.data, self.due, self.known)
        basis = next(row for row in outlook["rate_basis"]
                     if row["group"] == "east" and row["soil_band"] == "river")
        # 简单平均 (500+300)/2 = 400；HT 加权 (1000/1 + 600/0.25)/(2/1 + 2/0.25)
        self.assertAlmostEqual(basis["simple_mean_rate_kg_per_mu"], 400.0)
        self.assertAlmostEqual(basis["weighted_mean_rate_kg_per_mu"],
                               (1000 + 2400) / (2 + 8))
        self.assertNotAlmostEqual(basis["simple_mean_rate_kg_per_mu"],
                                  basis["weighted_mean_rate_kg_per_mu"])

    def test_registered_and_pending_are_kept_separate(self):
        outlook = yield_outlook(self.data, self.due, self.known)
        east = outlook["groups"]["east"]
        self.assertEqual(east["registered_kg"], 1600.0)
        # E1 待收 2 亩用自己实测率 500；E2 排期晚于供货日，进 late 不进 pending
        self.assertEqual(east["pending"]["area_mu"], 2.0)
        self.assertEqual(east["pending"]["plot_ids"], ["E1"])
        self.assertAlmostEqual(east["pending"]["point_kg"], 1000.0)
        self.assertEqual(east["late_mu"], 2.0)
        self.assertEqual(east["late"][0]["plot_id"], "E2")
        self.assertEqual(east["late"][0]["reason"], LATE_FINISH)

    def test_unobserved_plot_is_gap_not_fake_safety(self):
        outlook = yield_outlook(self.data, self.due, self.known)
        east = outlook["groups"]["east"]
        gap = next(row for row in east["gap"] if row["plot_id"] == "E3")
        # east×hill 地力带没有任何实测亩产率，因此是无依据缺口
        self.assertEqual(gap["reason"], GAP_NO_DOMAIN_BASIS)
        self.assertEqual(gap["area_mu"], 4.0)
        pending = east["pending"]
        expected = {"lower_bound": east["registered_kg"] + pending["low_kg"],
                    "central": east["registered_kg"] + pending["point_kg"],
                    "upper_bound": east["registered_kg"] + pending["high_kg"]}
        # 任何情景的 east 可供量都只含已收与待收，缺口面积从不计入
        for label, scenario in outlook["scenarios"].items():
            if label in expected:
                self.assertAlmostEqual(scenario["groups"]["east"], expected[label])
            else:
                factor = scenario["factor"]
                self.assertAlmostEqual(
                    scenario["groups"]["east"],
                    east["registered_kg"] + pending["point_kg"] * factor)
        self.assertAlmostEqual(east["available"]["point_kg"],
                               east["registered_kg"] + pending["point_kg"])

    def test_domain_without_any_rate_is_unobserved_domain_gap(self):
        # west 的 hill 地力带只有 W2 且从未观察：无亩产率依据
        outlook = yield_outlook(self.data, self.due, self.known)
        west = outlook["groups"]["west"]
        gap = next(row for row in west["gap"] if row["plot_id"] == "W2")
        self.assertEqual(gap["reason"], GAP_NO_DOMAIN_BASIS)
        self.assertIsNone(gap["indicative_kg"])
        self.assertIsNone(gap["domain_rate_basis"])

    def test_gap_kept_explicit_in_overall_and_nothing_hidden_as_zero(self):
        outlook = yield_outlook(self.data, self.due, self.known)
        self.assertEqual(outlook["overall"]["gap_mu"], 8.0)
        self.assertEqual({row["plot_id"] for row in outlook["overall"]["gap"]},
                         {"E3", "W2"})
        self.assertEqual(outlook["overall"]["late_mu"], 2.0)

    def test_common_shock_moves_all_groups_together(self):
        outlook = yield_outlook(self.data, self.due, self.known,
                                stress_factors=(0.8,))
        central = outlook["scenarios"]["central"]["groups"]
        stress = outlook["scenarios"]["stress_80"]["groups"]
        for group, point in central.items():
            registered = outlook["groups"][group]["registered_kg"]
            pending = point - registered
            self.assertAlmostEqual(stress[group], registered + pending * 0.8)
        # 下界同时作用于全部待收，不允许各村组独立互相抵消
        lower = outlook["scenarios"]["lower_bound"]["overall_kg"]
        summed = sum(group["registered_kg"] + group["pending"]["low_kg"]
                     for group in outlook["groups"].values())
        self.assertAlmostEqual(lower, summed)

    def test_future_records_do_not_change_judgement(self):
        before = yield_outlook(self.data, self.due, self.known)
        later = copy.deepcopy(self.data)
        later["visits"].append(visit("V9", "E3", "2026-11-16", 2, 9999,
                                     recorded_at="2026-12-01"))
        after = yield_outlook(later, self.due, self.known)
        self.assertEqual(before, after)

    def test_roster_adjustment_after_known_at_is_invisible(self):
        later = copy.deepcopy(self.data)
        later["plots"].append({**plot("E9", "east", 10),
                               "recorded_at": "2026-12-01"})
        before = yield_outlook(self.data, self.due, self.known)
        after = yield_outlook(later, self.due, self.known)
        self.assertEqual(before, after)

    def test_input_is_not_mutated(self):
        snapshot = copy.deepcopy(self.data)
        yield_outlook(self.data, self.due, self.known)
        evaluate_risk(self.data, self.due, self.known,
                      yield_outlook(self.data, self.due, self.known))
        self.assertEqual(snapshot, self.data)


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.data = synthetic_data()
        self.known = "2026-11-17"
        self.due = "2026-11-20"
        self.outlook = yield_outlook(self.data, self.due, self.known)

    def test_optional_orders_enumerated_with_confirmed_fixed(self):
        from commitments import obligations
        orders = obligations(self.data, self.due, True)
        combos = order_combinations(orders)
        ids = sorted(tuple(c["order_ids"]) for c in combos)
        self.assertEqual(ids, [("C1",), ("C1", "C2")])
        self.assertTrue(all("C1" in c["order_ids"] for c in combos))

    def test_orders_compete_for_same_supply(self):
        # C1 提到 3000、C2 跨村组且也为 3000，总需求 6000 超过中心可供量 4200
        self.data["orders"][0]["kg"] = 3000
        self.data["orders"][1]["group"] = None
        outlook = yield_outlook(self.data, self.due, self.known)
        risk = evaluate_risk(self.data, self.due, self.known, outlook)
        combo = next(c for c in risk["combinations"]
                     if c["order_ids"] == ["C1", "C2"])
        central = combo["scenarios"]["central"]
        total_required = sum(a["required_kg"] for a in central["allocations"])
        total_allocated = sum(sum(a["allocated"].values())
                              for a in central["allocations"])
        # 共享库存：全部分配被中心情景总可供量封顶，同一份货不会重复计算
        supply_central = outlook["scenarios"]["central"]["overall_kg"]
        self.assertLess(supply_central, total_required)
        self.assertAlmostEqual(total_allocated, supply_central)
        # 再造一个总需求超过可供量的组合，验证封顶确实来自共享库存
        self.data["orders"][1]["kg"] = 99999
        outlook2 = yield_outlook(self.data, self.due, self.known)
        risk2 = evaluate_risk(self.data, self.due, self.known, outlook2)
        combo2 = next(c for c in risk2["combinations"]
                      if c["order_ids"] == ["C1", "C2"])
        allocated2 = sum(sum(a["allocated"].values())
                         for a in combo2["scenarios"]["central"]["allocations"])
        self.assertAlmostEqual(allocated2,
                               outlook2["scenarios"]["central"]["overall_kg"])

    def test_shortfall_explained_under_explicit_scenario(self):
        self.data["orders"][0]["kg"] = 99999
        outlook = yield_outlook(self.data, self.due, self.known)
        risk = evaluate_risk(self.data, self.due, self.known, outlook)
        combo = risk["combinations"][0]
        lower = combo["scenarios"]["lower_bound"]
        self.assertFalse(lower["all_orders_covered"])
        bad = next(a for a in lower["allocations"] if a["shortfall_kg"] > 0)
        self.assertTrue(bad["shortfall_explanation"]["reasons"])
        # 上界情景应比下界情景更可能覆盖
        self.assertGreaterEqual(
            combo["scenarios"]["upper_bound"]["total_shortfall_kg"] * -1,
            lower["total_shortfall_kg"] * -1 - 1e-9)

    def test_gap_area_never_backstops_shortfall(self):
        risk = evaluate_risk(self.data, self.due, self.known, self.outlook)
        for combo in risk["combinations"]:
            for scenario in combo["scenarios"].values():
                for allocation in scenario["allocations"]:
                    for group_kg in allocation["allocated"].values():
                        self.assertGreaterEqual(quantity(group_kg), 0)
        # E3/W2 的缺口面积在解释中可见，但没有任何情景把它们算作可供
        explanation_groups = set()
        for combo in risk["combinations"]:
            for scenario in combo["scenarios"].values():
                for allocation in scenario["allocations"]:
                    note = allocation.get("shortfall_explanation")
                    if note:
                        explanation_groups.update(note["excluded_supply"])
        self.assertTrue(explanation_groups)


class AnalysisReproducibilityTests(unittest.TestCase):
    def setUp(self):
        self.data = synthetic_data()
        self.known = "2026-11-17"
        self.due = "2026-11-20"

    def test_same_basis_same_id_and_deterministic_output(self):
        first = build_analysis(self.data, self.due, self.known)
        second = build_analysis(copy.deepcopy(self.data), self.due, self.known)
        self.assertEqual(first["analysis_id"], second["analysis_id"])
        self.assertEqual(json.dumps(first, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False),
                         json.dumps(second, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False))

    def test_replay_reproduces_then_new_data_makes_new_comparable_id(self):
        with tempfile.TemporaryDirectory() as directory:
            analysis = run_estimate(self.data, self.due, self.known, directory)
            old_id = analysis["analysis_id"]
            self.assertEqual(list_analyses(directory), [old_id])
            stored = read_analysis(directory, old_id)
            self.assertTrue(replay(stored)["reproduced"])

            # 补录一块地的观察（新有效数据）：旧分析包不变，产生新编号
            updated = copy.deepcopy(self.data)
            updated["visits"].append(visit("V4", "E3", "2026-11-18", 2, 700,
                                           recorded_at="2026-11-18"))
            new_analysis = run_estimate(updated, self.due, "2026-11-18", directory)
            self.assertNotEqual(new_analysis["analysis_id"], old_id)
            self.assertEqual(sorted(list_analyses(directory)),
                             sorted([old_id, new_analysis["analysis_id"]]))
            # 旧编号仍能重现当时判断
            self.assertTrue(replay(read_analysis(directory, old_id))["reproduced"])
            # 新判断里 E3 已不再是缺口
            self.assertFalse(any(row["plot_id"] == "E3"
                                 for row in new_analysis["outlook"]["overall"]["gap"]))

    def test_backfill_after_known_at_cannot_silently_change_issued_analysis(self):
        analysis = build_analysis(self.data, self.due, self.known)
        tampered = copy.deepcopy(analysis)
        tampered["snapshot"]["visits"].append(
            visit("VX", "E3", "2026-11-16", 2, 500, recorded_at="2026-11-16"))
        with self.assertRaises(ValueError):
            replay(tampered)

    def test_issue_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            analysis = build_analysis(self.data, self.due, self.known)
            first = issue_analysis(directory, analysis)
            second = issue_analysis(directory, analysis)
            self.assertEqual(first, second)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_invalid_stress_factor_rejected(self):
        with self.assertRaises(ValueError):
            yield_outlook(self.data, self.due, self.known, stress_factors=(0,))
        with self.assertRaises(ValueError):
            yield_outlook(self.data, self.due, self.known, stress_factors=(1.2,))

    def test_late_finish_area_excluded_but_visible_with_indicator(self):
        outlook = yield_outlook(self.data, self.due, self.known)
        late = outlook["groups"]["east"]["late"][0]
        self.assertEqual(late["plot_id"], "E2")
        self.assertEqual(late["reason"], LATE_FINISH)
        self.assertAlmostEqual(late["indicative_kg"]["point_kg"], 2 * 300)
        # 晚收面积不进入可供量，也不进入待收区间
        east = outlook["groups"]["east"]
        self.assertNotIn("E2", east["pending"]["plot_ids"])
        for scenario in outlook["scenarios"].values():
            self.assertLessEqual(scenario["groups"]["east"],
                                 east["available"]["high_kg"])

    def test_group_restricted_order_cannot_touch_other_group_supply(self):
        # C2 限定 east，而 east 中心可供量不足；west 有余量也无法调用
        self.data["orders"][1]["kg"] = 99999
        outlook = yield_outlook(self.data, self.due, self.known)
        risk = evaluate_risk(self.data, self.due, self.known, outlook)
        combo = next(c for c in risk["combinations"]
                     if c["order_ids"] == ["C1", "C2"])
        c2 = next(a for a in combo["scenarios"]["central"]["allocations"]
                  if a["order_id"] == "C2")
        self.assertGreater(c2["shortfall_kg"], 0)
        reasons = c2["shortfall_explanation"]["reasons"]
        self.assertIn("其他村组仍有余量，但本订单限定来源村组，无法调用", reasons)


class FixtureIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.data = load(Path(__file__).parents[1] / "fixtures/data.json")
        self.known = "2026-11-17"

    def test_fixture_visits_not_double_counted(self):
        outlook = yield_outlook(self.data, "2026-11-20", self.known)
        self.assertEqual(outlook["coverage"]["visit_records_used"], 21)
        self.assertEqual(outlook["coverage"]["sample_plots"], 18)
        self.assertEqual(outlook["overall"]["gap_mu"], 26.0)
        for group in outlook["groups"].values():
            self.assertEqual(len(group["observed_plot_ids"]),
                             len(set(group["observed_plot_ids"])))

    def test_fixture_end_to_end_is_json_ready_and_replayable(self):
        with tempfile.TemporaryDirectory() as directory:
            analysis = run_estimate(copy.deepcopy(self.data), "2026-11-20",
                                    self.known, directory)
            encoded = json.dumps(analysis, ensure_ascii=False, allow_nan=False)
            self.assertTrue(encoded)
            self.assertTrue(replay(read_analysis(directory,
                                                 analysis["analysis_id"]))["reproduced"])

    def test_naive_reference_is_shown_as_reference_only(self):
        outlook = yield_outlook(self.data, "2026-11-20", self.known)
        ref = outlook["method"]["reference_calculation"]
        self.assertGreater(ref["naive_pooled_rate_times_total_area_kg"],
                           outlook["scenarios"]["upper_bound"]["overall_kg"])


if __name__ == "__main__":
    unittest.main()
