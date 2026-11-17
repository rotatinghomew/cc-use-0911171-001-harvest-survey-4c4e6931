"""供货日的履约风险分析。

与 commitments.check_registered 的区别：
- 可供量不只是登记实产，还包括 estimate.yield_outlook 给出的待收区间与缺口；
- 同一份货被多份订单争用时按实际分配顺序模拟，共享库存逐单扣减，而不是把各
  村组的保守下限各算各的；
- 共同减产作为相关冲击同时作用于所有地块，用情景（下界/中心/上界/压力系数）
  比较不同接单组合，解释哪些订单在哪些假设下可能供不上；
- 没有代表性依据的缺口不计入可供量，短供时明确区分"无依据缺口"与"晚于供货日"，
  不伪造精确安全量。
"""

from commitments import obligations
from estimate import GAP_NO_DOMAIN_BASIS, GAP_UNSCHEDULED, LATE_FINISH
from records import quantity

_SCENARIO_ORDER = ("lower_bound", "central", "upper_bound")


def _eligible_orders(data, due_on, include_proposed):
    return obligations(data, due_on, include_proposed)


def order_combinations(orders):
    """枚举可选（proposed）订单的所有接单组合；confirmed 订单始终包含。"""
    fixed = [row for row in orders if row["status"] == "confirmed"]
    optional = [row for row in orders if row["status"] != "confirmed"]
    combos = []
    for mask in range(1 << len(optional)):
        chosen = [optional[i] for i in range(len(optional)) if mask & (1 << i)]
        selected = fixed + chosen
        combos.append({
            "optional_order_ids": sorted(row["order_id"] for row in chosen),
            "order_ids": sorted(row["order_id"] for row in selected),
            "orders": sorted(selected, key=lambda row: (row["due_on"], row["order_id"]))})
    return combos


def _scenario_supply(outlook, label):
    scenario = outlook["scenarios"][label]
    groups = {name: amount for name, amount in scenario["groups"].items()}
    return groups


def _exposure(outlook):
    """每个村组内不计入可供量的缺口与晚收面积指示量。"""
    exposure = {}
    for name, group in outlook["groups"].items():
        gap_unscheduled = gap_no_basis = late = 0.0
        for row in group["gap"]:
            indicative = row.get("indicative_kg") or {}
            point = indicative.get("point_kg", 0.0) or 0.0
            if row["reason"] == GAP_NO_DOMAIN_BASIS:
                gap_no_basis += point
            else:
                gap_unscheduled += point
        for row in group["late"]:
            late += (row.get("indicative_kg") or {}).get("point_kg", 0.0) or 0.0
        exposure[name] = {"gap_unscheduled_kg": gap_unscheduled,
                          "gap_unobserved_domain_kg": gap_no_basis,
                          "late_finish_kg": late,
                          "gap_mu": group["gap_mu"], "late_mu": group["late_mu"]}
    return exposure


def _allocate(orders, supply, exposure):
    """模拟订单逐单争用共享可供量，返回每单分配与未满足量及其来源说明。"""
    remaining = dict(supply)
    allocations = []
    for order in orders:
        need = quantity(order["kg"])
        groups = [order["group"]] if order["group"] else sorted(remaining)
        used = {}
        for group in groups:
            if need <= 0:
                break
            taken = min(remaining.get(group, 0.0), need)
            if taken > 0:
                used[group] = taken
                remaining[group] -= taken
                need -= taken
        unmet = need
        detail = {"order_id": order["order_id"], "due_on": order["due_on"],
                  "group": order["group"], "required_kg": quantity(order["kg"]),
                  "allocated": used, "shortfall_kg": unmet}
        if unmet > 0:
            detail["shortfall_explanation"] = _explain(
                unmet, order["group"], groups, remaining, exposure)
        allocations.append(detail)
    return allocations


def _explain(unmet, order_group, eligible_groups, remaining, exposure):
    note = {"remaining_after_earlier_orders_kg":
            {group: remaining.get(group, 0.0) for group in eligible_groups}}
    gaps = {group: exposure.get(group, {}) for group in eligible_groups}
    note["excluded_supply"] = gaps
    reasons = []
    if order_group and any(remaining.get(group, 0.0) > 0
                           for group in remaining if group not in eligible_groups):
        reasons.append("其他村组仍有余量，但本订单限定来源村组，无法调用")
    if any(cell.get("gap_mu", 0) > 0 for cell in gaps.values()):
        reasons.append("缺口面积没有代表性亩产率或采收排期，不能计入安全可供量")
    if any(cell.get("late_mu", 0) > 0 for cell in gaps.values()):
        reasons.append("部分待收面积预计晚于供货日完成采收")
    reasons.append("同一份可供量已被前面的订单逐单扣减，订单之间存在争用")
    note["reasons"] = reasons
    note["unmet_kg"] = unmet
    return note


def evaluate_risk(data, due_on, known_at, outlook, include_proposed=True):
    orders = _eligible_orders(data, due_on, include_proposed)
    exposure = _exposure(outlook)
    stress_labels = sorted(
        (key for key in outlook["scenarios"] if key.startswith("stress_")),
        key=lambda label: outlook["scenarios"][label]["factor"], reverse=True)
    labels = list(_SCENARIO_ORDER) + stress_labels
    results = []
    for combo in order_combinations(orders):
        per_scenario = {}
        for label in labels:
            supply = _scenario_supply(outlook, label)
            per_scenario[label] = {
                "allocations": _allocate(combo["orders"], supply, exposure),
                "total_shortfall_kg": 0.0,
                "all_orders_covered": True}
            total = sum(row["shortfall_kg"]
                        for row in per_scenario[label]["allocations"])
            per_scenario[label]["total_shortfall_kg"] = total
            per_scenario[label]["all_orders_covered"] = total <= 1e-9
        feasible = {label: cell["all_orders_covered"]
                    for label, cell in per_scenario.items()}
        results.append({
            "optional_order_ids": combo["optional_order_ids"],
            "order_ids": combo["order_ids"],
            "scenarios": per_scenario,
            "feasible_by_scenario": feasible,
            "feasible_under_lower_bound": feasible["lower_bound"],
            "feasible_under_central": feasible["central"]})
    results.sort(key=lambda item: (-len(item["order_ids"]), item["order_ids"]))
    return {
        "due_on": due_on,
        "known_at": known_at,
        "scenario_meaning": {
            "lower_bound": "所有待收地块同时按各自地力带实测最低亩产率采收（共同减产的悲观边界）",
            "central": "已观察地块按本地块实测率、其余待收按概率加权中心率，且共同因素维持中性",
            "upper_bound": "所有待收地块同时按各自地力带实测最高亩产率采收（乐观边界）",
            "stress_*": "在中心判断上对全部待收面积同时施加同一减产系数，模拟相关冲击"},
        "gap_reasons": {GAP_UNSCHEDULED: "无观察、无采收排期",
                        GAP_NO_DOMAIN_BASIS: "村组×地力带内无实测亩产率依据",
                        LATE_FINISH: "预计晚于供货日采收完成"},
        "combinations": results}
