from harvest import registered
from records import quantity


def obligations(data, due_on, include_proposed=False):
    accepted = {"confirmed", "proposed"} if include_proposed else {"confirmed"}
    rows = []
    identities = set()
    for row in data["orders"]:
        if row["order_id"] in identities:
            raise ValueError("订单编号重复")
        identities.add(row["order_id"])
        if row["due_on"] <= due_on and row["status"] in accepted:
            rows.append({**row, "kg": quantity(row["kg"])})
    return sorted(rows, key=lambda row: (row["due_on"], row["order_id"]))


def check_registered(data, due_on, known_at, include_proposed=False):
    supply = registered(data, due_on, known_at)
    allocations = []
    for order in obligations(data, due_on, include_proposed):
        remaining = order["kg"]
        groups = [order["group"]] if order["group"] else sorted(supply)
        used = {}
        for group in groups:
            taken = min(supply.get(group, 0), remaining)
            supply[group] = supply.get(group, 0) - taken
            remaining -= taken
            used[group] = taken
        allocations.append({"order_id": order["order_id"], "allocated": used, "shortfall_kg": remaining})
    return {"basis": "已登记采收，不含预测", "allocations": allocations, "remaining": supply}
