"""面向指定供货日的产量估计。

口径要点：
- 只使用 ``known_at`` 时已知的有效名册、抽样安排与采收观察；同一地块多次观察
  只代表一个抽样单元，后一次有效观察替代前一次，绝不重复计为独立样本。
- 已收重量是登记实产（确定值）；待收面积只能给区间，不与已收混算。
- 抽样概率不等时，用 1/π 加权的比率估计修正样本偏差，而不是直接拿记录的简单
  平均亩产乘全村面积。
- 没有代表性依据或没有采收排期依据的面积进入缺口（gap），不编造看似精确的
  安全量；预计在供货日之后才能收完的面积进入 late，同样不计入可供量。
"""

from frame import roster
from harvest import progress
from records import fingerprint, quantity

ESTIMATOR_VERSION = "estimate-1"

DEFAULT_STRESS_FACTORS = (0.9, 0.8)

# 缺口原因
GAP_UNSCHEDULED = "unscheduled"          # 无任何观察，没有 expected_finish 排期
GAP_NO_DOMAIN_BASIS = "unobserved_domain"  # 村组×地力带内没有任何实测亩产率
LATE_FINISH = "expected_finish_after_due_on"  # 排期晚于供货日


def _known_rows(rows, key, known_at):
    """与 records.latest 同口径，但允许订单等记录缺省 recorded_at（视为始终已知）。"""
    selected = {}
    for row in rows:
        if row.get("recorded_at", "") > known_at:
            continue
        identity = row[key]
        current = selected.get(identity)
        stamp = (row.get("recorded_at", ""), row.get("revision", 0))
        if current is None or stamp > (current.get("recorded_at", ""),
                                        current.get("revision", 0)):
            selected[identity] = dict(row)
    return [selected[identity] for identity in sorted(selected)
            if not selected[identity].get("withdrawn", False)]


def effective_snapshot(data, known_at):
    """截取 known_at 时有效的三类业务记录，供固化分析依据与离线重放。"""
    return {
        "plots": _known_rows(data["plots"], "plot_id", known_at),
        "visits": _known_rows(data["visits"], "visit_id", known_at),
        "orders": _known_rows(data["orders"], "order_id", known_at),
    }


def _normalize_factors(stress_factors):
    factors = sorted({float(factor) for factor in stress_factors}, reverse=True)
    for factor in factors:
        if not 0 < factor <= 1:
            raise ValueError("共同减产情景系数应在 (0, 1] 区间")
    return tuple(factors)


def _rate_table(plots, observed_rows):
    """按 (村组, 地力带) 汇总实测亩产率。

    每个地块至多贡献一个亩产率（来自其最新有效观察的已收部分），因此重复走访
    不会扩大样本量。加权平均采用 HT 比率形式：
    Σ(kg_i/π_i) / Σ(面积_i/π_i)，修正不等概率抽样造成的样本偏差。
    """
    table = {}
    for row in observed_rows:
        harvested = quantity(row["harvested_area_mu"])
        if harvested <= 0:
            continue
        plot = plots[row["plot_id"]]
        key = (plot["group"], plot["soil_band"])
        probability = float(plot["selection_probability"])
        entry = table.get(key)
        if entry is None:
            entry = {"group": plot["group"], "soil_band": plot["soil_band"],
                     "rates": [], "plot_ids": [], "weighted_kg": 0.0,
                     "weighted_area": 0.0}
            table[key] = entry
        entry["rates"].append(row["registered_kg"] / harvested)
        entry["plot_ids"].append(row["plot_id"])
        entry["weighted_kg"] += row["registered_kg"] / probability
        entry["weighted_area"] += harvested / probability
    stats = {}
    for key in sorted(table):
        entry = table[key]
        rates = entry["rates"]
        stats[key] = {
            "group": entry["group"],
            "soil_band": entry["soil_band"],
            "sample_plots": len(rates),
            "plot_ids": sorted(entry["plot_ids"]),
            "simple_mean_rate_kg_per_mu": sum(rates) / len(rates),
            "weighted_mean_rate_kg_per_mu": entry["weighted_kg"] / entry["weighted_area"],
            "min_rate_kg_per_mu": min(rates),
            "max_rate_kg_per_mu": max(rates),
        }
    return stats


def _blank_group():
    return {"plots": 0, "area_mu": 0.0, "harvested_area_mu": 0.0,
            "observed_plot_ids": [], "unobserved_plot_ids": [],
            "probability_bands": {},
            "registered_kg": 0.0,
            "pending": {"area_mu": 0.0, "low_kg": 0.0, "point_kg": 0.0,
                        "high_kg": 0.0, "plot_ids": []},
            "gap_mu": 0.0, "gap": [],
            "late_mu": 0.0, "late": []}


def _indicative(area_mu, domain):
    if domain is None:
        return None
    return {"low_kg": area_mu * domain["min_rate_kg_per_mu"],
            "point_kg": area_mu * domain["weighted_mean_rate_kg_per_mu"],
            "high_kg": area_mu * domain["max_rate_kg_per_mu"]}


def yield_outlook(data, due_on, known_at, stress_factors=DEFAULT_STRESS_FACTORS):
    """生成供货日 due_on、依据 known_at 的产量区间与覆盖说明。"""
    factors = _normalize_factors(stress_factors)
    plots = roster(data, due_on, known_at)
    progress_rows = progress(data, due_on, known_at)["plots"]
    observed = {row["plot_id"]: row for row in progress_rows}
    rates = _rate_table(plots, progress_rows)

    effective_visits = [row for row in _known_rows(data["visits"], "visit_id", known_at)
                        if row["observed_on"] <= due_on and row["plot_id"] in plots]

    groups = {}
    for plot_id in sorted(plots):
        plot = plots[plot_id]
        group = groups.setdefault(plot["group"], _blank_group())
        group["plots"] += 1
        group["area_mu"] += plot["area_mu"]
        band = group["probability_bands"].setdefault(
            float(plot["selection_probability"]),
            {"probability": float(plot["selection_probability"]),
             "plots": 0, "observed_plots": 0})
        band["plots"] += 1

        row = observed.get(plot_id)
        domain = rates.get((plot["group"], plot["soil_band"]))
        if row is None:
            # 整块地从未观察：没有自己的亩产率，也没有采收排期。
            group["unobserved_plot_ids"].append(plot_id)
            reason = GAP_UNSCHEDULED if domain else GAP_NO_DOMAIN_BASIS
            group["gap_mu"] += plot["area_mu"]
            group["gap"].append({
                "plot_id": plot_id, "area_mu": plot["area_mu"],
                "soil_band": plot["soil_band"], "reason": reason,
                "domain_rate_basis": None if domain is None else {
                    "min_rate_kg_per_mu": domain["min_rate_kg_per_mu"],
                    "weighted_mean_rate_kg_per_mu": domain["weighted_mean_rate_kg_per_mu"],
                    "max_rate_kg_per_mu": domain["max_rate_kg_per_mu"]},
                "indicative_kg": _indicative(plot["area_mu"], domain)})
            continue

        band["observed_plots"] += 1
        group["observed_plot_ids"].append(plot_id)
        harvested = quantity(row["harvested_area_mu"])
        group["harvested_area_mu"] += harvested
        group["registered_kg"] += row["registered_kg"]

        remaining = row["remaining_mu"]
        if remaining <= 0:
            continue
        own_rate = (row["registered_kg"] / harvested) if harvested > 0 else None
        segment = {"plot_id": plot_id, "area_mu": remaining,
                   "soil_band": plot["soil_band"]}
        if row["expected_finish"] > due_on:
            segment["reason"] = LATE_FINISH
            segment["expected_finish"] = row["expected_finish"]
            rate = own_rate if own_rate is not None else (
                domain["weighted_mean_rate_kg_per_mu"] if domain else None)
            segment["indicative_kg"] = None if rate is None else {
                "point_kg": remaining * rate}
            group["late_mu"] += remaining
            group["late"].append(segment)
            continue
        if domain is None:
            segment["reason"] = GAP_NO_DOMAIN_BASIS
            segment["domain_rate_basis"] = None
            segment["indicative_kg"] = None
            group["gap_mu"] += remaining
            group["gap"].append(segment)
            continue

        point_rate = own_rate if own_rate is not None else domain["weighted_mean_rate_kg_per_mu"]
        group["pending"]["area_mu"] += remaining
        group["pending"]["plot_ids"].append(plot_id)
        group["pending"]["low_kg"] += remaining * domain["min_rate_kg_per_mu"]
        group["pending"]["point_kg"] += remaining * point_rate
        group["pending"]["high_kg"] += remaining * domain["max_rate_kg_per_mu"]

    group_out = {}
    for name in sorted(groups):
        group = groups[name]
        group["observed_plot_ids"].sort()
        group["unobserved_plot_ids"].sort()
        group["probability_bands"] = [group["probability_bands"][key]
                                      for key in sorted(group["probability_bands"])]
        group["pending"]["plot_ids"].sort()
        group["available"] = {
            "low_kg": group["registered_kg"] + group["pending"]["low_kg"],
            "point_kg": group["registered_kg"] + group["pending"]["point_kg"],
            "high_kg": group["registered_kg"] + group["pending"]["high_kg"]}
        group_out[name] = group

    overall = _overall_totals(group_out)
    scenarios = _scenarios(group_out, factors)

    pooled_rates = [rate["simple_mean_rate_kg_per_mu"]
                    for rate in rates.values()]
    naive_reference = None
    if pooled_rates:
        naive_reference = {
            "naive_pooled_rate_times_total_area_kg":
                sum(pooled_rates) / len(pooled_rates) * overall["area_mu"],
            "说明": "简单平均亩产乘全村面积，未做抽样加权、未区分已收待收、未扣除缺口与晚于供货日面积，仅作对照，不是承诺量"}

    return {
        "estimator_version": ESTIMATOR_VERSION,
        "parameters": {"due_on": due_on, "known_at": known_at,
                       "stress_factors": list(factors)},
        "coverage": {
            "visit_records_used": len(effective_visits),
            "sample_plots": len(observed),
            "说明": "visit_records_used 是有效走访条数；同一地块连续观察只计一个 sample_plots 抽样单元",
            "groups": {name: {key: value for key, value in group.items()
                              if key in {"plots", "area_mu", "harvested_area_mu",
                                         "observed_plot_ids", "unobserved_plot_ids",
                                         "probability_bands"}}
                       for name, group in group_out.items()}},
        "rate_basis": [rates[key] for key in sorted(rates)],
        "groups": group_out,
        "overall": overall,
        "scenarios": scenarios,
        "method": {
            "common_shock": "low/high 与压力情景把同一减产因素同时施加到所有村组和地块，区间是同步边界，不是各村组独立波动后相加",
            "pending_bounds": "待收下界/上界取该地力带内实测亩产率的最小/最大值，中心点：已观察地块用本地块已收部分实测率，未观察地块不进入可供量",
            "gap_policy": "无亩产率依据或无供货日前采收排期的面积保留为缺口，只给可追溯的指示性区间或 null，不计入任何安全可供量",
            "reference_calculation": naive_reference},
        "basis_fingerprints": _basis_fingerprints(data, known_at)}


def _overall_totals(group_out):
    overall = _blank_group()
    del overall["probability_bands"]
    overall["observed_plot_ids"] = []
    overall["unobserved_plot_ids"] = []
    for name, group in group_out.items():
        overall["plots"] += group["plots"]
        overall["area_mu"] += group["area_mu"]
        overall["harvested_area_mu"] += group["harvested_area_mu"]
        overall["registered_kg"] += group["registered_kg"]
        overall["gap_mu"] += group["gap_mu"]
        overall["late_mu"] += group["late_mu"]
        overall["gap"].extend(group["gap"])
        overall["late"].extend(group["late"])
        for key in ("area_mu", "low_kg", "point_kg", "high_kg"):
            overall["pending"][key] += group["pending"][key]
        overall["pending"]["plot_ids"].extend(group["pending"]["plot_ids"])
        overall["observed_plot_ids"].extend(group["observed_plot_ids"])
        overall["unobserved_plot_ids"].extend(group["unobserved_plot_ids"])
    overall["observed_plot_ids"].sort()
    overall["unobserved_plot_ids"].sort()
    overall["pending"]["plot_ids"].sort()
    overall["gap"].sort(key=lambda row: (row["plot_id"], row["reason"]))
    overall["late"].sort(key=lambda row: row["plot_id"])
    overall["available"] = {
        "low_kg": overall["registered_kg"] + overall["pending"]["low_kg"],
        "point_kg": overall["registered_kg"] + overall["pending"]["point_kg"],
        "high_kg": overall["registered_kg"] + overall["pending"]["high_kg"]}
    return overall


def _scenarios(group_out, factors):
    scenarios = {}
    for bound, key in (("low_kg", "lower_bound"),
                       ("point_kg", "central"),
                       ("high_kg", "upper_bound")):
        per_group = {name: group["registered_kg"] + group["pending"][bound]
                     for name, group in group_out.items()}
        scenarios[key] = {"groups": per_group,
                          "overall_kg": sum(per_group.values())}
    for factor in factors:
        per_group = {name: group["registered_kg"]
                     + group["pending"]["point_kg"] * factor
                     for name, group in group_out.items()}
        label = f"stress_{round(factor * 100)}"
        scenarios[label] = {"groups": per_group,
                            "overall_kg": sum(per_group.values()),
                            "factor": factor}
    return scenarios


def _basis_fingerprints(data, known_at):
    snapshot = effective_snapshot(data, known_at)
    key_of = {"plots": "plot_id", "visits": "visit_id", "orders": "order_id"}
    return {name: fingerprint(sorted(rows, key=lambda row: row[key_of[name]]))
            for name, rows in snapshot.items()}
