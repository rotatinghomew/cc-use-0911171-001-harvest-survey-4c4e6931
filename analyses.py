"""分析编号与不可变分析包。

采购员日后用同一个分析编号查看，必须能重现当时依据：分析包固化 known_at 时刻
有效的名册/观察/订单快照、方法版本和全部参数；补录与名册调整只产生新编号的新
判断，不会悄悄改写已发出的分析。分析编号是对快照、参数和方法版本的内容哈希，
无随机数、无墙钟时间，因此同依据同参数必然同编号，新旧判断可比较。
"""

import json
from pathlib import Path

from estimate import ESTIMATOR_VERSION, effective_snapshot, yield_outlook
from records import fingerprint, load, save
from risk import evaluate_risk

ANALYSIS_ID_PREFIX = "ANA"


def build_analysis(data, due_on, known_at, include_proposed=True,
                   stress_factors=(0.9, 0.8)):
    """依据当前 data 构建一次完整分析（不修改输入）。"""
    outlook = yield_outlook(data, due_on, known_at, stress_factors)
    risk = evaluate_risk(data, due_on, known_at, outlook, include_proposed)
    snapshot = effective_snapshot(data, known_at)
    analysis_id = _identify(snapshot, due_on, known_at, include_proposed,
                            tuple(outlook["parameters"]["stress_factors"]))
    return {
        "analysis_id": analysis_id,
        "created_basis": {
            "estimator_version": ESTIMATOR_VERSION,
            "due_on": due_on,
            "known_at": known_at,
            "include_proposed": include_proposed,
            "stress_factors": list(outlook["parameters"]["stress_factors"]),
            "basis_fingerprints": outlook["basis_fingerprints"]},
        "snapshot": snapshot,
        "outlook": outlook,
        "risk": risk}


def _identify(snapshot, due_on, known_at, include_proposed, stress_factors):
    material = {
        "estimator_version": ESTIMATOR_VERSION,
        "due_on": due_on,
        "known_at": known_at,
        "include_proposed": include_proposed,
        "stress_factors": list(stress_factors),
        "plots": sorted(snapshot["plots"], key=lambda row: row["plot_id"]),
        "visits": sorted(snapshot["visits"], key=lambda row: row["visit_id"]),
        "orders": sorted(snapshot["orders"], key=lambda row: row["order_id"])}
    return f"{ANALYSIS_ID_PREFIX}-{fingerprint(material)[:16]}"


def issue_analysis(store_dir, analysis):
    """把分析包幂等写入仓库目录，返回分析编号；已存在则不覆盖。"""
    store = Path(store_dir)
    target = store / f"{analysis['analysis_id']}.json"
    if target.exists():
        existing = load(target)
        if existing["analysis_id"] != analysis["analysis_id"]:
            raise ValueError("分析编号与文件内容不一致")
        return analysis["analysis_id"]
    save(target, analysis)
    return analysis["analysis_id"]


def read_analysis(store_dir, analysis_id):
    return load(Path(store_dir) / f"{analysis_id}.json")


def list_analyses(store_dir):
    store = Path(store_dir)
    if not store.exists():
        return []
    return sorted(path.stem for path in store.glob(f"{ANALYSIS_ID_PREFIX}-*.json"))


def replay(analysis):
    """只用分析包内固化的快照重算，验证能否重现当时判断。"""
    basis = analysis["created_basis"]
    rebuilt = build_analysis(
        analysis["snapshot"], basis["due_on"], basis["known_at"],
        basis["include_proposed"], tuple(basis["stress_factors"]))
    if rebuilt["analysis_id"] != analysis["analysis_id"]:
        raise ValueError("重放编号不一致，分析包依据可能被改动")
    same_outlook = json.dumps(rebuilt["outlook"], sort_keys=True,
                              ensure_ascii=False, allow_nan=False)
    stored_outlook = json.dumps(analysis["outlook"], sort_keys=True,
                                ensure_ascii=False, allow_nan=False)
    same_risk = json.dumps(rebuilt["risk"], sort_keys=True,
                           ensure_ascii=False, allow_nan=False)
    stored_risk = json.dumps(analysis["risk"], sort_keys=True,
                             ensure_ascii=False, allow_nan=False)
    return {"analysis_id": analysis["analysis_id"],
            "reproduced": same_outlook == stored_outlook and same_risk == stored_risk}
