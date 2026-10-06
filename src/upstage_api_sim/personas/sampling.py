"""Validated, revision-aware Nemotron panel caching without eager dependencies.

``revision`` is the requested Hugging Face reference, not a resolved commit.
Passing a mutable name such as ``main`` does not make a panel reproducible across
fresh installations. Retain the manifest and JSONL, or request a fixed commit.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any, Callable
import uuid

from .nemotron import DATASET_ID, compact_persona_from_row

DEFAULT_BUFFER_SIZE = 10_000
MANIFEST_VERSION = 1
_METHODS = {"streaming_shuffle_take", "server_streaming_shuffle_take"}
_CACHE_LOCK = threading.Lock()


def _options(sample_size: int, seed: int, revision: str | None, buffer_size: int) -> str | None:
    for name, value, minimum in (("sample_size", sample_size, 1), ("seed", seed, 0), ("buffer_size", buffer_size, 1)):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}")
    if revision is not None and not isinstance(revision, str):
        raise ValueError("revision must be a string or None")
    return (revision.strip() or None) if revision is not None else None


def _cache_path(root: Path, sample_size: int, seed: int, revision: str | None, buffer_size: int) -> Path:
    # Preserve the original server filename only for its original sampling mode.
    suffix = ""
    if revision is not None or buffer_size != DEFAULT_BUFFER_SIZE:
        identity = json.dumps([DATASET_ID, revision, buffer_size], separators=(",", ":"))
        suffix = "_ref" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return root / f"nemotron{suffix}_seed{seed}_n{sample_size}.jsonl"


def _manifest_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".manifest.json")


def _reject_constant(value: str) -> None:
    raise ValueError("persona sample must contain finite JSON values")


def _decode_json(text: str) -> Any:
    value = json.loads(text, parse_constant=_reject_constant)
    # Also reject exponent overflow (e.g. 1e999), which parse_constant does not see.
    json.dumps(value, allow_nan=False)
    return value


def _validate_records(records: list[dict[str, Any]], *, sample_size: int, seed: int, revision: str | None, buffer_size: int) -> str:
    if len(records) != sample_size:
        raise ValueError(f"persona sample count mismatch: expected {sample_size}, got {len(records)}")
    methods: set[str] = set()
    for row in records:
        if not isinstance(row, dict) or row.get("dataset_id") != DATASET_ID:
            raise ValueError("persona sample dataset mismatch")
        sampling = row.get("sampling")
        if not isinstance(sampling, dict) or sampling.get("dataset_id") != DATASET_ID:
            raise ValueError("persona sample sampling source mismatch")
        if type(sampling.get("seed")) is not int or sampling["seed"] != seed:
            raise ValueError("persona sample seed mismatch")
        if type(sampling.get("buffer_size")) is not int or sampling["buffer_size"] != buffer_size:
            raise ValueError("persona sample buffer size mismatch")
        if sampling.get("split") != "train" or sampling.get("revision") != revision:
            raise ValueError("persona sample split/revision mismatch")
        method = sampling.get("method")
        if not isinstance(method, str) or method not in _METHODS:
            raise ValueError("persona sample method mismatch")
        methods.add(method)
    if len(methods) != 1:
        raise ValueError("persona sample contains mixed sampling methods")
    return methods.pop()


def _manifest(path: Path, content: bytes, records: list[dict[str, Any]], *, seed: int, revision: str | None, buffer_size: int, method: str) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_VERSION,
        "dataset_id": DATASET_ID,
        "split": "train",
        "revision": revision,
        "revision_kind": "requested_reference" if revision is not None else "default_reference",
        "sampling_seed": seed,
        "sample_size": len(records),
        "buffer_size": buffer_size,
        "sampling_method": method,
        "output": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "sampled_uuids": [str(row["uuid"]) if row.get("uuid") is not None else None for row in records],
    }


def _read_cache(path: Path, *, sample_size: int, seed: int, revision: str | None, buffer_size: int) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    content = path.read_bytes()
    records = [_decode_json(line) for line in content.decode("utf-8").splitlines() if line.strip()]
    method = _validate_records(records, sample_size=sample_size, seed=seed, revision=revision, buffer_size=buffer_size)
    expected = _manifest(path, content, records, seed=seed, revision=revision, buffer_size=buffer_size, method=method)
    manifest_path = _manifest_path(path)
    if not manifest_path.exists():
        return records, expected, True
    actual = _decode_json(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(actual, dict):
        raise ValueError("persona manifest must contain an object")
    # The original CLI emitted unversioned manifests without a checksum. Verify
    # their provenance before upgrading them, keeping the JSONL itself intact.
    legacy = "schema_version" not in actual and "sha256" not in actual
    keys = ("dataset_id", "split", "sampling_seed", "sample_size", "buffer_size", "sampling_method", "revision", "sampled_uuids")
    if any(actual.get(key) != expected[key] for key in keys):
        raise ValueError("persona manifest provenance mismatch")
    if not legacy and any(actual.get(key) != expected[key] for key in expected):
        raise ValueError("persona manifest integrity mismatch")
    return records, expected, legacy


def _quarantine(root: Path, *paths: Path) -> None:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return
    destination = root / ".trash" / uuid.uuid4().hex
    destination.mkdir(parents=True, exist_ok=False)
    for path in existing:
        path.replace(destination / path.name)


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        # Preserve interrupted writes for inspection without exposing them as a
        # usable cache. Incomplete data never gets a normal cache filename.
        _quarantine(path.parent, temporary)


def sample_personas(*, sample_size: int, seed: int, revision: str | None = None, loader: Callable[..., Any] | None = None, buffer_size: int = DEFAULT_BUFFER_SIZE) -> list[dict[str, Any]]:
    """Sample an exact-size panel; this performs no cache/file writes."""

    revision = _options(sample_size, seed, revision, buffer_size)
    if loader is None:
        try:
            from datasets import load_dataset
        except ImportError as exc:
            raise RuntimeError("Nemotron sampling requires datasets; install with python -m pip install '.[persona]'") from exc
        loader = load_dataset
    kwargs: dict[str, Any] = {"split": "train", "streaming": True}
    if revision is not None:
        kwargs["revision"] = revision
    stream = loader(DATASET_ID, **kwargs)
    rows = stream.shuffle(seed=seed, buffer_size=buffer_size).take(sample_size)
    records: list[dict[str, Any]] = []
    for row in rows:
        compact = compact_persona_from_row(dict(row))
        compact["sampling"] = {
            "dataset_id": DATASET_ID,
            "split": "train",
            "seed": seed,
            "buffer_size": buffer_size,
            "method": "streaming_shuffle_take",
            "revision": revision,
        }
        records.append(compact)
    _validate_records(records, sample_size=sample_size, seed=seed, revision=revision, buffer_size=buffer_size)
    return records


def write_persona_sample(path: str | Path, records: list[dict[str, Any]], *, sample_size: int, seed: int, revision: str | None = None, buffer_size: int = DEFAULT_BUFFER_SIZE) -> Path:
    """Atomically write a validated panel and checksum manifest; return manifest path."""

    revision = _options(sample_size, seed, revision, buffer_size)
    method = _validate_records(records, sample_size=sample_size, seed=seed, revision=revision, buffer_size=buffer_size)
    content = "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in records).encode("utf-8")
    path = Path(path)
    manifest = _manifest(path, content, records, seed=seed, revision=revision, buffer_size=buffer_size, method=method)
    encoded_manifest = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Existing output belongs to the caller; preserve it if explicitly replacing
    # an export. Cache callers arrive here only after validation/quarantine.
    _quarantine(path.parent, path, _manifest_path(path))
    try:
        _atomic_write(path, content)
        _atomic_write(_manifest_path(path), encoded_manifest)
    except Exception:
        _quarantine(path.parent, path, _manifest_path(path))
        raise
    return _manifest_path(path)


def load_or_sample_personas(cache_dir: str | Path, *, sample_size: int, seed: int, revision: str | None = None, loader: Callable[..., Any] | None = None, buffer_size: int = DEFAULT_BUFFER_SIZE) -> list[dict[str, Any]]:
    """Reuse a validated exact-size panel, or quarantine and resample it.

    ``loader`` is an optional datasets.load_dataset-compatible callable for tests.
    The in-process lock prevents readers seeing a partially published pair;
    deployments should not share this writable cache across multiple processes.
    """

    revision = _options(sample_size, seed, revision, buffer_size)
    root = Path(cache_dir)
    path = _cache_path(root, sample_size, seed, revision, buffer_size)
    with _CACHE_LOCK:
        if path.exists():
            try:
                records, manifest, needs_manifest = _read_cache(path, sample_size=sample_size, seed=seed, revision=revision, buffer_size=buffer_size)
            except (OSError, UnicodeError, ValueError, RecursionError):
                _quarantine(root, path, _manifest_path(path))
            else:
                if needs_manifest:
                    _quarantine(root, _manifest_path(path))
                    _atomic_write(_manifest_path(path), (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
                return records
        elif _manifest_path(path).exists():
            _quarantine(root, _manifest_path(path))
        records = sample_personas(sample_size=sample_size, seed=seed, revision=revision, loader=loader, buffer_size=buffer_size)
        write_persona_sample(path, records, sample_size=sample_size, seed=seed, revision=revision, buffer_size=buffer_size)
        return records
