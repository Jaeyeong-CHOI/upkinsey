"""SQLite-backed knowledge graph projection for saved Upkinsey runs.

This module keeps the first graph layer deliberately small: it projects each
simulation result into typed entities, aggregate edges, and per-run
observations. SQLite is enough for local/self-hosted installs, while the schema
maps cleanly to Postgres or Neo4j if graph queries become product-critical.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: str, *, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _safe_text(value: Any, *, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _stable_key(*parts: Any) -> str:
    text = "|".join(_safe_text(part, limit=400).lower() for part in parts if _safe_text(part, limit=400))
    return text or "unknown"


def _listify(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [value]


def _as_number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _report(result: dict[str, Any]) -> dict[str, Any]:
    report = result.get("report")
    return report if isinstance(report, dict) else {}


def _result_section(result: dict[str, Any], key: str) -> Any:
    section = result.get(key)
    if section not in (None, [], {}):
        return section
    return _report(result).get(key)


def _persona_context_id(reaction: dict[str, Any]) -> str:
    context = reaction.get("persona_context") if isinstance(reaction.get("persona_context"), dict) else {}
    source = context.get("source") if isinstance(context.get("source"), dict) else {}
    return _safe_text(source.get("uuid") or source.get("dataset_id") or reaction.get("uuid") or reaction.get("id"), limit=120)


def _entity_id(entity_type: str, entity_key: str) -> str:
    return f"{entity_type}:{_hash(entity_key)}"


def _edge_id(source_id: str, relation: str, target_id: str) -> str:
    return f"edge:{_hash(f'{source_id}|{relation}|{target_id}', length=24)}"


def init_graph_store(db_path: str | Path) -> None:
    """Create the graph schema if needed."""

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS graph_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        existing_version = conn.execute("SELECT value FROM graph_meta WHERE key = 'schema_version'").fetchone()
        if existing_version is not None and existing_version[0] != str(SCHEMA_VERSION):
            raise ValueError("unsupported_graph_schema_version")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS graph_entities (
                entity_id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_key TEXT NOT NULL,
                label TEXT NOT NULL,
                properties_json TEXT NOT NULL DEFAULT '{}',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                UNIQUE(entity_type, entity_key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS graph_edges (
                edge_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                target_id TEXT NOT NULL,
                properties_json TEXT NOT NULL DEFAULT '{}',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                observation_count INTEGER NOT NULL DEFAULT 0,
                UNIQUE(source_id, relation, target_id),
                FOREIGN KEY(source_id) REFERENCES graph_entities(entity_id) ON DELETE CASCADE,
                FOREIGN KEY(target_id) REFERENCES graph_entities(entity_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS graph_observations (
                observation_id TEXT PRIMARY KEY,
                edge_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL,
                FOREIGN KEY(edge_id) REFERENCES graph_edges(edge_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_graph_entities_type ON graph_entities(entity_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_graph_edges_relation ON graph_edges(relation)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_graph_observations_run ON graph_observations(run_id)")
        conn.execute(
            "INSERT OR IGNORE INTO graph_meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()


def _upsert_entity(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_key: str,
    label: str,
    properties: dict[str, Any] | None,
    *,
    observed_at: str,
) -> str:
    entity_key = entity_key or "unknown"
    entity_id = _entity_id(entity_type, entity_key)
    conn.execute(
        """
        INSERT INTO graph_entities(entity_id, entity_type, entity_key, label, properties_json, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(entity_type, entity_key) DO UPDATE SET
            label = excluded.label,
            properties_json = excluded.properties_json,
            last_seen_at = excluded.last_seen_at
        """,
        (entity_id, entity_type, entity_key, label or entity_key, _json_dumps(properties or {}), observed_at, observed_at),
    )
    return entity_id


def _upsert_edge(
    conn: sqlite3.Connection,
    source_id: str,
    relation: str,
    target_id: str,
    properties: dict[str, Any] | None,
    *,
    run_id: str,
    observed_at: str,
    evidence: dict[str, Any] | None = None,
    confidence: float | None = None,
) -> str:
    edge_id = _edge_id(source_id, relation, target_id)
    conn.execute(
        """
        INSERT INTO graph_edges(edge_id, source_id, relation, target_id, properties_json, first_seen_at, last_seen_at, observation_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        ON CONFLICT(source_id, relation, target_id) DO UPDATE SET
            properties_json = excluded.properties_json,
            last_seen_at = excluded.last_seen_at
        """,
        (edge_id, source_id, relation, target_id, _json_dumps(properties or {}), observed_at, observed_at),
    )

    evidence_json = _json_dumps(evidence or {})
    observation_id = f"obs:{_hash(f'{edge_id}|{run_id}|{evidence_json}', length=24)}"
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO graph_observations(observation_id, edge_id, run_id, observed_at, evidence_json, confidence)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (observation_id, edge_id, run_id, observed_at, evidence_json, confidence),
    )
    if conn.total_changes > before:
        conn.execute(
            "UPDATE graph_edges SET observation_count = observation_count + 1, last_seen_at = ? WHERE edge_id = ?",
            (observed_at, edge_id),
        )
    return edge_id


def save_simulation_graph(
    db_path: str | Path,
    brief: dict[str, Any],
    result: dict[str, Any],
    *,
    version_id: str | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Project a saved simulation result into the local graph store."""

    init_graph_store(db_path)
    timestamp = observed_at or _now_iso()
    version = result.get("version") if isinstance(result.get("version"), dict) else {}
    run_id = _safe_text(version_id or version.get("version_id") or f"adhoc-{_hash(_json_dumps({'brief': brief, 'result': result}), length=12)}")
    product_name = _safe_text(brief.get("product_name"), limit=100) or "제품"
    research_type = _safe_text(brief.get("research_type"), limit=80) or "Concept test"
    report = _report(result)
    reactions = _listify(result.get("persona_reactions") or result.get("personas"))
    objections = _listify(_result_section(result, "objections"))
    segments = _listify(_result_section(result, "segment_recommendations"))
    decision_board = _result_section(result, "decision_board") or report.get("decision_board") or {}

    counts = {"entities": 0, "edges": 0, "observations": 0}
    with closing(sqlite3.connect(Path(db_path))) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        before_entities = conn.execute("SELECT COUNT(*) FROM graph_entities").fetchone()[0]
        before_edges = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        before_observations = conn.execute("SELECT COUNT(*) FROM graph_observations").fetchone()[0]

        run_entity = _upsert_entity(
            conn,
            "run",
            run_id,
            run_id,
            {
                "research_type": research_type,
                "created_at": version.get("created_at"),
                "adoption_score": result.get("adoption_score"),
                "need_fit_score": result.get("need_fit_score"),
                "price_risk": result.get("price_risk"),
            },
            observed_at=timestamp,
        )
        product_entity = _upsert_entity(
            conn,
            "product",
            _stable_key(product_name),
            product_name,
            {
                "research_type": research_type,
                "target_market": _safe_text(brief.get("target_market"), limit=400),
                "description": _safe_text(brief.get("description"), limit=600),
                "pricing": _listify(brief.get("pricing"))[:6],
            },
            observed_at=timestamp,
        )
        _upsert_edge(
            conn,
            run_entity,
            "evaluates",
            product_entity,
            {"research_type": research_type},
            run_id=run_id,
            observed_at=timestamp,
            evidence={"brief": {"product_name": product_name, "research_type": research_type}},
        )

        for reaction in reactions:
            if not isinstance(reaction, dict):
                continue
            persona_label = _safe_text(reaction.get("name"), limit=100) or "Persona"
            persona_key = _stable_key(_persona_context_id(reaction) or persona_label, reaction.get("meta"))
            persona_entity = _upsert_entity(
                conn,
                "persona",
                persona_key,
                persona_label,
                {
                    "meta": _safe_text(reaction.get("meta"), limit=180),
                    "stance": _safe_text(reaction.get("stance"), limit=80),
                    "used_persona_fields": _listify(reaction.get("used_persona_fields"))[:10],
                },
                observed_at=timestamp,
            )
            _upsert_edge(
                conn,
                product_entity,
                "tested_with",
                persona_entity,
                {},
                run_id=run_id,
                observed_at=timestamp,
                evidence={"persona": persona_label},
            )
            _upsert_edge(
                conn,
                persona_entity,
                "reacted_to",
                product_entity,
                {
                    "adoption_likelihood": reaction.get("adoption_likelihood"),
                    "need_fit_score": reaction.get("need_fit_score"),
                    "understanding_score": reaction.get("understanding_score"),
                    "price_resistance": reaction.get("price_resistance"),
                    "stance": reaction.get("stance"),
                },
                run_id=run_id,
                observed_at=timestamp,
                evidence={
                    "concern": _safe_text(reaction.get("concern"), limit=400),
                    "next_validation_question": _safe_text(reaction.get("next_validation_question"), limit=400),
                },
                confidence=_as_number(reaction.get("adoption_likelihood")),
            )

            for driver in _listify(reaction.get("positive_drivers"))[:8]:
                driver_text = _safe_text(driver, limit=180)
                if not driver_text:
                    continue
                driver_entity = _upsert_entity(conn, "driver", _stable_key(driver_text), driver_text, {}, observed_at=timestamp)
                _upsert_edge(
                    conn,
                    persona_entity,
                    "has_driver",
                    driver_entity,
                    {},
                    run_id=run_id,
                    observed_at=timestamp,
                    evidence={"driver": driver_text, "persona": persona_label},
                )
                _upsert_edge(
                    conn,
                    product_entity,
                    "has_driver",
                    driver_entity,
                    {},
                    run_id=run_id,
                    observed_at=timestamp,
                    evidence={"driver": driver_text, "persona": persona_label},
                )

            for risk in _listify(reaction.get("top_risks"))[:8]:
                risk_text = _safe_text(risk, limit=180)
                if not risk_text:
                    continue
                objection_entity = _upsert_entity(
                    conn,
                    "objection",
                    _stable_key(risk_text),
                    risk_text,
                    {"source": "persona_top_risk"},
                    observed_at=timestamp,
                )
                _upsert_edge(
                    conn,
                    persona_entity,
                    "has_objection",
                    objection_entity,
                    {},
                    run_id=run_id,
                    observed_at=timestamp,
                    evidence={"risk": risk_text, "persona": persona_label},
                )
                _upsert_edge(
                    conn,
                    product_entity,
                    "has_objection",
                    objection_entity,
                    {},
                    run_id=run_id,
                    observed_at=timestamp,
                    evidence={"risk": risk_text, "persona": persona_label},
                )

        for objection in objections:
            if not isinstance(objection, dict):
                continue
            label = _safe_text(objection.get("category") or objection.get("objection"), limit=180)
            if not label:
                continue
            objection_entity = _upsert_entity(
                conn,
                "objection",
                _stable_key(label),
                label,
                {
                    "example": _safe_text(objection.get("objection"), limit=240),
                    "count": objection.get("count"),
                    "suggested_fix": _safe_text(objection.get("suggested_fix"), limit=300),
                },
                observed_at=timestamp,
            )
            _upsert_edge(
                conn,
                product_entity,
                "has_aggregate_objection",
                objection_entity,
                {"count": objection.get("count")},
                run_id=run_id,
                observed_at=timestamp,
                evidence=objection,
            )

        for segment in segments:
            if not isinstance(segment, dict):
                continue
            label = _safe_text(segment.get("segment") or segment.get("role"), limit=140)
            if not label:
                continue
            segment_entity = _upsert_entity(
                conn,
                "segment",
                _stable_key(segment.get("role"), label),
                label,
                {
                    "role": _safe_text(segment.get("role"), limit=80),
                    "persona_count": segment.get("persona_count"),
                    "avg_adoption": segment.get("avg_adoption"),
                    "avg_need_fit": segment.get("avg_need_fit"),
                    "primary_driver": _safe_text(segment.get("primary_driver"), limit=180),
                    "primary_objection": _safe_text(segment.get("primary_objection"), limit=180),
                },
                observed_at=timestamp,
            )
            _upsert_edge(
                conn,
                product_entity,
                "has_segment",
                segment_entity,
                {"role": segment.get("role")},
                run_id=run_id,
                observed_at=timestamp,
                evidence=segment,
            )

        if isinstance(decision_board, dict) and decision_board.get("decision"):
            decision = _safe_text(decision_board.get("decision"), limit=80)
            decision_entity = _upsert_entity(conn, "decision", _stable_key(decision), decision, {}, observed_at=timestamp)
            _upsert_edge(
                conn,
                product_entity,
                "has_decision",
                decision_entity,
                {
                    "confidence": decision_board.get("confidence"),
                    "next_step": _safe_text(decision_board.get("next_step"), limit=300),
                },
                run_id=run_id,
                observed_at=timestamp,
                evidence=decision_board,
            )

        after_entities = conn.execute("SELECT COUNT(*) FROM graph_entities").fetchone()[0]
        after_edges = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        after_observations = conn.execute("SELECT COUNT(*) FROM graph_observations").fetchone()[0]
        counts["entities"] = after_entities
        counts["edges"] = after_edges
        counts["observations"] = after_observations
        counts["new_entities"] = after_entities - before_entities
        counts["new_edges"] = after_edges - before_edges
        counts["new_observations"] = after_observations - before_observations
        conn.commit()

    return {"run_id": run_id, "store": "local_sqlite", **counts}


def graph_summary(db_path: str | Path) -> dict[str, Any]:
    """Return compact stats without letting a broken projection break health.

    Summary reads never create or initialize a database. JSON runs remain the
    source of truth when an optional graph projection is unavailable.
    """

    path = Path(db_path)
    if not path.exists():
        return {"exists": False, "entities": 0, "edges": 0, "observations": 0, "entity_types": {}, "relations": {}}
    unavailable = {"exists": True, "available": False, "store": "local_sqlite", "entities": 0, "edges": 0, "observations": 0, "entity_types": {}, "relations": {}}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            version = conn.execute("SELECT value FROM graph_meta WHERE key = 'schema_version'").fetchone()
            if version is None or version[0] != str(SCHEMA_VERSION):
                return {**unavailable, "error": "unsupported_graph_schema_version"}
            entity_rows = conn.execute(
                "SELECT entity_type, COUNT(*) FROM graph_entities GROUP BY entity_type ORDER BY entity_type"
            ).fetchall()
            relation_rows = conn.execute(
                "SELECT relation, COUNT(*) FROM graph_edges GROUP BY relation ORDER BY relation"
            ).fetchall()
            entities = conn.execute("SELECT COUNT(*) FROM graph_entities").fetchone()[0]
            edges = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
            observations = conn.execute("SELECT COUNT(*) FROM graph_observations").fetchone()[0]
    except (sqlite3.Error, OSError):
        return {**unavailable, "error": "graph_store_unavailable"}
    return {
        "exists": True,
        "available": True,
        "schema_version": SCHEMA_VERSION,
        "store": "local_sqlite",
        "entities": entities,
        "edges": edges,
        "observations": observations,
        "entity_types": {key: count for key, count in entity_rows},
        "relations": {key: count for key, count in relation_rows},
    }
