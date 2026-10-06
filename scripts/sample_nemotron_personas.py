#!/usr/bin/env python3
"""Sample Nemotron-Personas-Korea rows and save compact personas as JSONL.

Example:
  python scripts/sample_nemotron_personas.py --seed 42 --n 10 --output data/personas/sample.jsonl
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from upstage_api_sim.personas.sampling import sample_personas, write_persona_sample  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--buffer-size", type=int, default=10_000)
    parser.add_argument("--revision", default=os.environ.get("UPKINSEY_PERSONA_REVISION") or None,
                        help="Requested dataset reference (a name such as main is mutable)")
    parser.add_argument("--output", type=Path, default=Path("data/personas/sample.jsonl"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    records = sample_personas(sample_size=args.n, seed=args.seed, revision=args.revision, buffer_size=args.buffer_size)
    manifest_path = write_persona_sample(args.output, records, sample_size=args.n, seed=args.seed,
                                         revision=args.revision, buffer_size=args.buffer_size)

    print(f"wrote {args.output}")
    print(f"wrote {manifest_path}")


if __name__ == "__main__":
    main()
