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



def main():
    parser = argparse.ArgumentParser(description="合作社地块名册、采收观察及供货承诺核对。")
    parser.add_argument("action", choices=["demo", "inspect"])
    parser.add_argument("--data", type=Path, default=Path(__file__).parent / "fixtures/data.json")
    parser.add_argument("--as-of", default="2026-11-17")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = inspect(load(args.data), args.as_of)
    if args.output:
        save(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
