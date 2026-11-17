from records import latest, quantity


def roster(data, on_date, known_at):
    rows = latest(data["plots"], "plot_id", known_at)
    active = {}
    for row in rows:
        if row["valid_from"] > on_date or row.get("valid_to") and row["valid_to"] <= on_date:
            continue
        row["area_mu"] = quantity(row["area_mu"])
        probability = float(row["selection_probability"])
        if not 0 < probability <= 1:
            raise ValueError("抽样概率不在有效范围")
        active[row["plot_id"]] = row
    return active


def coverage(data, on_date, known_at):
    plots = roster(data, on_date, known_at)
    observed = {row["plot_id"] for row in latest(data["visits"], "visit_id", known_at) if row["observed_on"] <= on_date}
    groups = {}
    for identity, plot in plots.items():
        group = groups.setdefault(plot["group"], {"plots": 0, "area_mu": 0, "observed_plots": 0})
        group["plots"] += 1
        group["area_mu"] += plot["area_mu"]
        group["observed_plots"] += identity in observed
    return groups
