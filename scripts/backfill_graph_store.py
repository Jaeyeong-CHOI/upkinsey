#!/usr/bin/env python3
"""Backfill the local graph store from saved simulation run JSON files."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from upstage_api_sim.graph_store import graph_summary, save_simulation_graph  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    data_dir = Path(os.environ.get("UPKINSEY_DATA_DIR") or ROOT / "data")
    parser.add_argument("--run-store", default=str(data_dir / "simulation_runs"))
    parser.add_argument("--graph-store", default=str(data_dir / "graph" / "upkinsey.sqlite3"))
    args = parser.parse_args()

    run_store = Path(args.run_store)
    if not run_store.is_dir():
        parser.error(f"run store directory does not exist: {run_store}")
    processed = 0
    skipped: list[str] = []
    for path in sorted(run_store.glob("*.json")):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(run, dict):
                raise ValueError("run file must contain a JSON object")
            brief = run.get("brief") if isinstance(run.get("brief"), dict) else {}
            result = run.get("result") if isinstance(run.get("result"), dict) else {}
            version_id = str(run.get("version_id") or path.stem)
            if not result:
                raise ValueError("missing result object")
            # Match the live projection's version metadata. Canonical JSON stores
            # these fields outside result; omitting them would erase created_at
            # on an otherwise unchanged backfill of an already projected run.
            version = result.get("version") if isinstance(result.get("version"), dict) else {}
            projection = {**result, "version": {**version, "version_id": version_id, "created_at": run.get("created_at")}}
            save_simulation_graph(args.graph_store, brief, projection, version_id=version_id, observed_at=run.get("created_at"))
            processed += 1
        except Exception as exc:
            skipped.append(f"{path.name}: {exc}")

    print(
        json.dumps(
            {
                "processed": processed,
                "skipped": skipped,
                "graph": graph_summary(args.graph_store),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
