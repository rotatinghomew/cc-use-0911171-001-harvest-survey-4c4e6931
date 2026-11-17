import argparse
import json
from pathlib import Path
from records import load, save


def inspect(data, as_of):
    from frame import coverage
    from harvest import progress
    from commitments import check_registered
    return {"coverage": coverage(data, as_of, as_of), "progress": progress(data, as_of, as_of),
            "confirmed": check_registered(data, "2026-11-20", as_of),
            "with_proposed": check_registered(data, "2026-11-20", as_of, True)}


def estimate(data, due_on, known_at, store_dir=None, include_proposed=True,
             stress_factors=(0.9, 0.8)):
    from analyses import build_analysis, issue_analysis
    analysis = build_analysis(data, due_on, known_at, include_proposed, stress_factors)
    if store_dir is not None:
        issue_analysis(store_dir, analysis)
    return analysis


def replay(store_dir, analysis_id):
    from analyses import read_analysis
    from analyses import replay as run_replay
    return run_replay(read_analysis(store_dir, analysis_id))


def main():
    parser = argparse.ArgumentParser(description="合作社地块名册、采收观察及供货承诺核对。")
    parser.add_argument("action", choices=["demo", "inspect", "estimate", "replay"])
    parser.add_argument("--data", type=Path, default=Path(__file__).parent / "fixtures/data.json")
    parser.add_argument("--as-of", default="2026-11-17")
    parser.add_argument("--due-on", default="2026-11-20")
    parser.add_argument("--known-at", default=None)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--store", type=Path, default=Path(__file__).parent / "out" / "analyses")
    parser.add_argument("--analysis-id")
    parser.add_argument("--confirmed-only", action="store_true")
    parser.add_argument("--stress", type=float, nargs="*", default=[0.9, 0.8])
    parser.add_argument("--no-store", action="store_true")
    args = parser.parse_args()

    if args.action in ("demo", "inspect"):
        result = inspect(load(args.data), args.as_of)
    elif args.action == "estimate":
        known_at = args.known_at or args.as_of
        store = None if args.no_store else args.store
        result = estimate(load(args.data), args.due_on, known_at, store,
                          include_proposed=not args.confirmed_only,
                          stress_factors=tuple(args.stress))
    else:
        if not args.analysis_id:
            parser.error("replay 需要 --analysis-id")
        result = replay(args.store, args.analysis_id)

    if args.output:
        save(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
