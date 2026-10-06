"""Persistent simulation run history for the Upkinsey prototype."""

from __future__ import annotations

import json
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-f0-9]{8}$")


def _default_trash_dir(store_dir: str | Path) -> Path:
    root = Path(store_dir)
    return root / ".trash"


def _trash_path(path: Path, trash_dir: str | Path | None) -> Path:
    trash_root = Path(trash_dir) if trash_dir is not None else _default_trash_dir(path.parent)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest_dir = trash_root / stamp
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    if dest.exists():
        dest = dest_dir / f"{path.stem}-{uuid.uuid4().hex[:6]}{path.suffix}"
    return dest


def _safe_text(value: Any, *, limit: int = 160) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _decision_from_result(result: dict[str, Any]) -> str:
    report = result.get("report") if isinstance(result.get("report"), dict) else {}
    board = report.get("decision_board") if isinstance(report.get("decision_board"), dict) else {}
    return _safe_text(board.get("decision") or board.get("recommendation") or "")


def _result_section(result: dict[str, Any], key: str) -> dict[str, Any]:
    """Return a report artifact from either the top-level result or nested report.

    Older saved runs may only have these artifacts under ``result.report`` while
    newer runs also expose them at top level. Version history should summarize
    both shapes so previously saved simulations remain comparable.
    """

    section = result.get(key)
    if isinstance(section, dict):
        return section
    report = result.get("report") if isinstance(result.get("report"), dict) else {}
    section = report.get(key)
    return section if isinstance(section, dict) else {}


def _as_number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _delta_direction(delta: float | None) -> str:
    if delta is None or abs(delta) < 0.5:
        return "flat"
    return "up" if delta > 0 else "down"


def _load_run_file(path: Path) -> dict[str, Any] | None:
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return run if isinstance(run, dict) else None


def _list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def summarize_run(run: dict[str, Any]) -> dict[str, Any]:
    """Return the compact metadata shown in the prototype version history."""

    brief = run.get("brief") if isinstance(run.get("brief"), dict) else {}
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    evidence_quality = _result_section(result, "evidence_quality")
    request_budget = _result_section(result, "request_budget")
    panel_profile = _result_section(result, "panel_profile")
    version_id = _safe_text(run.get("version_id"))
    return {
        "version_id": version_id,
        "created_at": _safe_text(run.get("created_at")),
        "product_name": _safe_text(brief.get("product_name"), limit=80) or "제품",
        "research_type": _safe_text(brief.get("research_type"), limit=80) or "Concept test",
        "sample_size": request_budget.get("requested_sample_size") or brief.get("sample_size"),
        "persona_count": _list_count(result.get("persona_reactions") or result.get("personas") or []),
        "adoption_score": result.get("adoption_score"),
        "need_fit_score": result.get("need_fit_score"),
        "price_risk": result.get("price_risk"),
        "decision": _decision_from_result(result),
        "evidence_quality_score": evidence_quality.get("score"),
        "evidence_quality_level": _safe_text(evidence_quality.get("level"), limit=40),
        "evidence_confidence": _safe_text(evidence_quality.get("confidence"), limit=40),
        "evidence_warning_count": _list_count(evidence_quality.get("warnings") or []),
        "actual_persona_calls": request_budget.get("solar_persona_calls") or request_budget.get("actual_persona_count"),
        "estimated_model_calls": request_budget.get("estimated_total_model_calls"),
        "planned_batches": request_budget.get("planned_batches"),
        "max_parallel_requests": request_budget.get("max_parallel_requests"),
        "request_budget_warning_count": _list_count(request_budget.get("warnings") or []),
        "panel_selection_mode": _safe_text(panel_profile.get("selection_mode"), limit=60),
        "panel_filter_source": _safe_text(panel_profile.get("persona_filter_source"), limit=60),
        "source_persona_count": panel_profile.get("source_persona_count"),
        "selected_persona_count": panel_profile.get("selected_persona_count"),
        "target_filter_match_count": panel_profile.get("target_filter_match_count"),
        "panel_warning_count": _list_count(panel_profile.get("warnings") or []),
    }


def compare_run_summaries(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Compare two compact run summaries for version-history trend display."""

    metrics: list[dict[str, Any]] = []
    for key, label, unit in (
        ("adoption_score", "Adoption", "p"),
        ("need_fit_score", "Need fit", "p"),
        ("evidence_quality_score", "Evidence quality", "p"),
    ):
        current_value = _as_number(current.get(key))
        baseline_value = _as_number(baseline.get(key))
        delta = None if current_value is None or baseline_value is None else round(current_value - baseline_value, 1)
        metrics.append(
            {
                "key": key,
                "label": label,
                "current": current.get(key),
                "baseline": baseline.get(key),
                "delta": delta,
                "unit": unit,
                "direction": _delta_direction(delta),
            }
        )

    decision_changed = _safe_text(current.get("decision")) != _safe_text(baseline.get("decision"))
    price_risk_changed = _safe_text(current.get("price_risk")) != _safe_text(baseline.get("price_risk"))
    changed_metrics = [metric for metric in metrics if metric["direction"] != "flat"]
    if changed_metrics:
        summary = ", ".join(
            f"{metric['label']} {metric['delta']:+g}{metric['unit']}" for metric in changed_metrics if metric["delta"] is not None
        )
    else:
        summary = "핵심 점수 변화가 거의 없습니다."
    if decision_changed:
        summary += f" Decision changed: {baseline.get('decision') or '-'} → {current.get('decision') or '-'}"

    return {
        "current_version_id": current.get("version_id"),
        "baseline_version_id": baseline.get("version_id"),
        "current": current,
        "baseline": baseline,
        "metrics": metrics,
        "price_risk_changed": price_risk_changed,
        "decision_changed": decision_changed,
        "summary": summary,
    }


def save_simulation_run(store_dir: str | Path, brief: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Save one simulation result as an immutable versioned JSON file."""

    root = Path(store_dir)
    root.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    version_id = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
    run = {
        "version_id": version_id,
        "created_at": created_at,
        "brief": brief,
        "result": result,
    }
    path = root / f"{version_id}.json"
    tmp_path = root / f".{version_id}.tmp"
    tmp_path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)
    return {"summary": summarize_run(run), "path": str(path)}


def list_simulation_runs(store_dir: str | Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """List recent saved simulation versions, newest first."""

    root = Path(store_dir)
    if not root.exists() or limit <= 0:
        return []
    summaries: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), reverse=True):
        run = _load_run_file(path)
        if run is None:
            continue
        summaries.append(summarize_run(run))
        if len(summaries) >= limit:
            break
    return summaries


def load_simulation_run(store_dir: str | Path, version_id: str) -> dict[str, Any]:
    """Load one saved simulation version by id."""

    if not RUN_ID_RE.fullmatch(version_id):
        raise ValueError("invalid_version_id")
    path = Path(store_dir) / f"{version_id}.json"
    if not path.exists():
        raise FileNotFoundError(version_id)
    run = _load_run_file(path)
    if run is None:
        raise ValueError("invalid_version_file")
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    result = dict(result)
    result["version"] = summarize_run(run)
    brief = run.get("brief") if isinstance(run.get("brief"), dict) else {}
    return {"version": summarize_run(run), "brief": brief, "result": result}


def compare_simulation_run(store_dir: str | Path, version_id: str, *, baseline_version_id: str | None = None) -> dict[str, Any]:
    """Compare one saved run with an older baseline run.

    If no explicit baseline is supplied, prefer the nearest older run with the
    same product name and research type; otherwise fall back to the nearest older
    run. This keeps repeated simulation reviews useful without requiring users
    to manually pick two versions.
    """

    if not RUN_ID_RE.fullmatch(version_id):
        raise ValueError("invalid_version_id")
    if baseline_version_id is not None and not RUN_ID_RE.fullmatch(baseline_version_id):
        raise ValueError("invalid_baseline_version_id")

    root = Path(store_dir)
    current_path = root / f"{version_id}.json"
    if not current_path.exists():
        raise FileNotFoundError(version_id)
    current_run = _load_run_file(current_path)
    if current_run is None:
        raise ValueError("invalid_version_file")
    current_summary = summarize_run(current_run)

    if baseline_version_id:
        baseline_path = root / f"{baseline_version_id}.json"
        if not baseline_path.exists():
            raise FileNotFoundError(baseline_version_id)
        baseline_run = _load_run_file(baseline_path)
        if baseline_run is None:
            raise ValueError("invalid_baseline_file")
        return compare_run_summaries(current_summary, summarize_run(baseline_run))

    runs: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), reverse=True):
        run = _load_run_file(path)
        if run is not None:
            runs.append(run)

    current_index = next((index for index, run in enumerate(runs) if _safe_text(run.get("version_id")) == version_id), None)
    if current_index is None:
        raise FileNotFoundError(version_id)

    current_product = _safe_text(current_summary.get("product_name"), limit=80)
    current_type = _safe_text(current_summary.get("research_type"), limit=80)
    older_runs = runs[current_index + 1 :]
    baseline_run = next(
        (
            run
            for run in older_runs
            if _safe_text(summarize_run(run).get("product_name"), limit=80) == current_product
            and _safe_text(summarize_run(run).get("research_type"), limit=80) == current_type
        ),
        older_runs[0] if older_runs else None,
    )
    if baseline_run is None:
        return {
            "current_version_id": version_id,
            "baseline_version_id": None,
            "current": current_summary,
            "baseline": None,
            "metrics": [],
            "price_risk_changed": False,
            "decision_changed": False,
            "summary": "비교할 이전 시뮬레이션 버전이 없습니다.",
        }
    return compare_run_summaries(current_summary, summarize_run(baseline_run))


def delete_simulation_run(store_dir: str | Path, version_id: str, *, trash_dir: str | Path | None = None) -> dict[str, Any]:
    """Remove one saved simulation version by moving it to trash."""

    if not RUN_ID_RE.fullmatch(version_id):
        raise ValueError("invalid_version_id")
    path = Path(store_dir) / f"{version_id}.json"
    if not path.exists():
        raise FileNotFoundError(version_id)
    run = _load_run_file(path)
    summary = summarize_run(run) if run is not None else {"version_id": version_id}
    dest = _trash_path(path, trash_dir)
    path.replace(dest)
    return {"deleted": 1, "version": summary, "trash_path": str(dest)}


def clear_simulation_runs(store_dir: str | Path, *, trash_dir: str | Path | None = None) -> dict[str, Any]:
    """Remove all saved simulation versions by moving them to trash."""

    root = Path(store_dir)
    if not root.exists():
        return {"deleted": 0, "versions": []}
    versions: list[dict[str, Any]] = []
    deleted = 0
    for path in sorted(root.glob("*.json")):
        run = _load_run_file(path)
        versions.append(summarize_run(run) if run is not None else {"version_id": path.stem})
        dest = _trash_path(path, trash_dir)
        path.replace(dest)
        deleted += 1
    return {"deleted": deleted, "versions": versions}
