"""Non-secret provenance for comparing synthetic research runs.

Hashes identify the actual selected panel and prompts without duplicating their
content. A sampling seed alone is not a promise of identical model responses.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import __version__


def content_sha256(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise ValueError("Provenance input must contain finite JSON-compatible values") from None
    return hashlib.sha256(encoded).hexdigest()


def build_provenance(*, brief: dict, source_count: int, selected_personas: list[dict],
                     model: str | None, system_prompt: str, persona_prompts: list[str],
                     started_at: str, duration_seconds: float) -> dict[str, Any]:
    sources = set()
    observed_seeds: set[int] = set()
    missing_seed_count = 0
    for persona in selected_personas:
        sampling = persona.get("sampling") if isinstance(persona.get("sampling"), dict) else {}
        seed = sampling.get("seed")
        if type(seed) is int:
            observed_seeds.add(seed)
        else:
            missing_seed_count += 1
        dataset_id = persona.get("dataset_id") or sampling.get("dataset_id")
        revision = sampling.get("revision") or sampling.get("dataset_revision")
        if isinstance(dataset_id, str):
            sources.add((dataset_id, revision if isinstance(revision, str) else None))
    return {
        "schema_version": 1,
        "application_version": __version__,
        "model": model if isinstance(model, str) else None,
        # A caller can supply an externally sampled panel. The brief seed is a
        # request parameter, not proof of how those records were sampled.
        "requested_seed": brief.get("seed"),
        "sampling_seed": next(iter(observed_seeds)) if len(observed_seeds) == 1 and not missing_seed_count else None,
        "observed_sampling_seeds": sorted(observed_seeds),
        "sampling_seed_missing_count": missing_seed_count,
        "source_persona_count": source_count,
        "selected_persona_count": len(selected_personas),
        "panel_sha256": content_sha256(selected_personas),
        "system_prompt_sha256": content_sha256(system_prompt),
        "persona_prompts_sha256": content_sha256(persona_prompts),
        "dataset_sources": [
            {"dataset_id": dataset_id, "revision": revision}
            for dataset_id, revision in sorted(sources, key=lambda item: (item[0], item[1] or ""))
        ],
        "started_at": started_at,
        "duration_seconds": round(max(0, duration_seconds), 3),
        "limitations": [
            "A fixed sampling seed does not make model completions deterministic.",
            "Missing dataset revisions or model identifiers are not inferred.",
            "Logical request counts exclude provider retries and are not billing totals.",
        ],
    }
