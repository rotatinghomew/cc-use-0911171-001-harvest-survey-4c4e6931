from frame import roster
from records import latest, quantity


def progress(data, on_date, known_at):
    plots = roster(data, on_date, known_at)
    effective = {}
    rejected = []
    for row in latest(data["visits"], "visit_id", known_at):
        if row["observed_on"] > on_date:
            continue
        identity = row["plot_id"]
        if identity not in plots:
            rejected.append(row["visit_id"])
            continue
        if identity not in effective or row["observed_on"] > effective[identity]["observed_on"]:
            effective[identity] = row
    results = []
    for identity, row in effective.items():
        area = quantity(row["harvested_area_mu"])
        mass = quantity(row["harvested_kg"])
        if area > plots[identity]["area_mu"] or area == 0 and mass:
            raise ValueError("采收面积与地块不一致")
        results.append({**row, "group": plots[identity]["group"], "area_mu": plots[identity]["area_mu"],
                        "registered_kg": mass, "remaining_mu": max(0, plots[identity]["area_mu"] - area)})
    return {"plots": sorted(results, key=lambda row: row["plot_id"]), "unmatched_visits": rejected}


def registered(data, on_date, known_at):
    totals = {}
    for row in progress(data, on_date, known_at)["plots"]:
        totals[row["group"]] = totals.get(row["group"], 0) + row["registered_kg"]
    return totals
