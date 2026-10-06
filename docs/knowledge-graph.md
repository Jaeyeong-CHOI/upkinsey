# Upkinsey Knowledge Graph

## Decision

Upkinsey does not need Zep as a required dependency for the first graph layer.
The implementation uses a local SQLite projection of saved simulation runs.
JSON run files remain the source of truth; a projection failure must not discard
a successful simulation. The graph requires no extra Python package or service.

Default layout inside the configured data directory:

- `data/graph/upkinsey.sqlite3`
- module: `src/upstage_api_sim/graph_store.py`
- authenticated debug endpoint: `GET /api/graph/summary` (also supports `HEAD`)

This keeps self-hosting simple and avoids introducing a managed memory service
before the product needs temporal agent memory.

## What Gets Stored

Each saved run is projected into:

- entities: `run`, `product`, `persona`, `driver`, `objection`, `segment`, `decision`
- edges: `evaluates`, `tested_with`, `reacted_to`, `has_driver`, `has_objection`,
  `has_aggregate_objection`, `has_segment`, `has_decision`
- observations: per-run evidence attached to each edge

Edges are aggregate relationships. Observations retain a run ID and evidence
payload. Their counts are **not** unique-person counts, independent empirical
observations, or measured market demand. No graph-backed retrieval is added to
model prompts.

Schema version 1 and the existing deterministic entity/edge/observation ID
algorithms are retained. A newer or unrecognized schema is rejected rather than
overwritten. The summary endpoint reports `available: false` and a stable error
code for an unreadable or incompatible graph instead of leaking file paths or
preventing the application health response.

## Backfill

Existing saved runs can be projected into the graph with:

```bash
python scripts/backfill_graph_store.py
```

The script is run from a source checkout. It reads `data/simulation_runs/*.json`
and writes the local SQLite graph store. When `UPKINSEY_DATA_DIR` is set, both
defaults are relative to that directory. For a server started with a custom
`--data-dir`, supply matching locations explicitly if the environment variable
is not set:

```bash
python scripts/backfill_graph_store.py \
  --run-store /path/to/upkinsey-data/simulation_runs \
  --graph-store /path/to/upkinsey-data/graph/upkinsey.sqlite3
```

Back up the SQLite file before any bulk operation. Backfill does not call a model
or download a dataset; it modifies the graph only. It reports processed/skipped
files and is idempotent for unchanged run evidence. Reusing a run ID with changed
evidence can add observations, so retain the immutable JSON versions. Non-finite
JSON numbers and unsupported graph schemas are rejected; a bad record is skipped
without aborting the rest of the backfill.

## Why Not Zep Yet

Zep is useful when the product needs hosted long-term agent memory, temporal
fact updates, and a ready-made retrieval API. Upkinsey's immediate need is
lighter:

- accumulate product/persona/objection relationships across runs
- compare repeated simulations
- keep public/self-hosted deployment easy
- avoid vendor lock-in while the schema is still changing

## Migration Path

The schema is intentionally portable:

1. SQLite now for local/self-hosted installs.
2. Postgres later if saved runs and graph queries need multi-user durability.
3. Postgres + pgvector if semantic retrieval over entity evidence becomes core.
4. Graphiti/Zep only if temporal agent memory becomes a product feature.
5. Neo4j only if deep graph traversal becomes central to the UI or API.

## Current Limits

- No semantic embeddings yet.
- No graph-backed retrieval in prompts yet.
- Graph deletion is not coupled to run deletion; deleted runs are moved to trash,
  while graph observations remain as historical evidence.
- Entity identity is deterministic but conservative, so two products with the
  same name are treated as the same product until we add workspace/user scoping.
  Missing persona IDs and changed metadata can also merge or split identities.
- Aggregate entity/edge properties describe the last projection write, not a
  temporal query of every run; use observation evidence and the JSON source for
  historical interpretation. Backfilling older runs can change those properties.
- The legacy observation column named `confidence` stores the generated adoption
  rating for `reacted_to` edges. It is not calibrated confidence or probability;
  the name is retained for database compatibility.
- SQLite remains a single-instance local projection, not a multi-tenant graph
  service. Keep it outside the static web root and include it in backup/retention
  planning. Deleting a run does not erase its graph evidence.
