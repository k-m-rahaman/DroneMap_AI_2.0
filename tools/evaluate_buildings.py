"""Usage: python tools/evaluate_buildings.py predictions.geojson references.geojson --output metrics.json"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation import evaluate_buildings

MAX_INPUT_BYTES = 64 * 1024 * 1024


def _read(path):
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("Input exceeds 64 MiB; split the evaluation into smaller geographic areas.")
    with path.open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate WGS84 building GeoJSON against independently labeled reference polygons.")
    parser.add_argument("predictions", type=Path)
    parser.add_argument("references", type=Path)
    parser.add_argument("--iou-threshold", type=float, default=0.5)
    parser.add_argument("--output", type=Path, help="Write JSON here; otherwise print JSON to stdout.")
    args = parser.parse_args(argv)
    try:
        if args.output and args.output.resolve() in {args.predictions.resolve(), args.references.resolve()}:
            raise ValueError("Output must not overwrite either input file.")
        result = evaluate_buildings(_read(args.predictions), _read(args.references), iou_threshold=args.iou_threshold)
        encoded = json.dumps(result, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(encoded, encoding="utf-8")
        else:
            print(encoded, end="")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Evaluation failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
