#!/usr/bin/env python3
"""Run the local Upkinsey prototype server.

Serves the static prototype and exposes POST /api/simulate backed by Upstage.
"""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import math
import os
import re
from email.parser import BytesParser
from email.policy import default as email_policy
import sys
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from upstage_api_sim.document_parse import parse_document_to_brief  # noqa: E402
from upstage_api_sim.env import load_env  # noqa: E402
from upstage_api_sim.market_research import analyst_question_personas, chat_with_persona, simulate_market_research  # noqa: E402
from upstage_api_sim.personas.io import load_personas_jsonl  # noqa: E402
from upstage_api_sim.personas.nemotron import DATASET_ID, compact_persona_from_row  # noqa: E402
from upstage_api_sim.run_store import (  # noqa: E402
    clear_simulation_runs,
    compare_simulation_run,
    delete_simulation_run,
    list_simulation_runs,
    load_simulation_run,
    save_simulation_run,
)

RUN_STORE = ROOT / "data" / "simulation_runs"
RUN_TRASH = ROOT / ".trash" / "simulation_runs_deleted"
PERSONA_CACHE = ROOT / "data" / "personas"
DEFAULT_MAX_BODY_BYTES = 1_000_000
DEFAULT_MAX_DOCUMENT_BYTES = 8_000_000
DEFAULT_JOB_TTL_SECONDS = 60 * 60
SIMULATION_JOBS: dict[str, dict] = {}
SIMULATION_JOBS_LOCK = threading.Lock()
PERSONA_CACHE_LOCK = threading.Lock()
RATE_LIMIT_STATE: dict[str, list[float]] = {}
RATE_LIMIT_LOCK = threading.Lock()

MUTATING_API_PATHS = {"/api/simulate", "/api/simulate/start", "/api/persona-chat", "/api/analyst-question", "/api/document-brief"}
REDACTION_PATTERNS = [
    re.compile(r"(?:Bearer|Basic)\s+[A-Za-z0-9._~+\-/]+=*", re.I),
    re.compile(r"(api[_-]?key|authorization|token|password)['\"]?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,}]+)", re.I),
]


def _basic_auth_enabled() -> bool:
    """Return whether Basic auth is required for this deployment."""

    require_auth = os.environ.get("UPKINSEY_REQUIRE_BASIC_AUTH", "").strip().lower()
    return require_auth in {"1", "true", "yes", "on"}


def _basic_auth_configured() -> bool:
    return bool(os.environ.get("UPKINSEY_BASIC_AUTH_USER") and os.environ.get("UPKINSEY_BASIC_AUTH_PASSWORD"))


def _basic_auth_allowed(header: str | None) -> bool:
    """Validate optional HTTP Basic auth without leaking credentials in errors."""

    if not _basic_auth_enabled():
        return True
    if not _basic_auth_configured():
        return False
    if not header or header.partition(" ")[0].lower() != "basic":
        return False
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1], validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        return False
    expected_user = os.environ.get("UPKINSEY_BASIC_AUTH_USER", "")
    expected_password = os.environ.get("UPKINSEY_BASIC_AUTH_PASSWORD", "")
    return hmac.compare_digest(username.encode("utf-8"), expected_user.encode("utf-8")) and hmac.compare_digest(
        password.encode("utf-8"), expected_password.encode("utf-8")
    )


def _destructive_api_enabled() -> bool:
    value = os.environ.get("UPKINSEY_ALLOW_DESTRUCTIVE_API", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _max_active_jobs() -> int:
    return _bounded_int(os.environ.get("UPKINSEY_MAX_ACTIVE_JOBS"), default=2, min_value=1, max_value=20)


def _active_job_count() -> int:
    _cleanup_finished_jobs()
    with SIMULATION_JOBS_LOCK:
        return sum(1 for job in SIMULATION_JOBS.values() if job.get("status") in {"queued", "running"})


def _reserve_simulation_job(payload: dict) -> tuple[str | None, int]:
    """Reserve a shared paid-operation slot (async jobs retain their snapshot)."""

    _cleanup_finished_jobs()
    limit = _max_active_jobs()
    requested_total = _bounded_int(payload.get("sample_size"), default=100, min_value=1, max_value=200)
    with SIMULATION_JOBS_LOCK:
        active = sum(1 for job in SIMULATION_JOBS.values() if job.get("status") in {"queued", "running"})
        if active >= limit:
            return None, limit
        job_id = uuid.uuid4().hex[:12]
        SIMULATION_JOBS[job_id] = {
            "status": "queued",
            "stage": "queued",
            "message": "시뮬레이션 작업 대기 중",
            "completed": 0,
            "total": requested_total,
            "percent": 0,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        return job_id, limit


def _client_id(handler: SimpleHTTPRequestHandler) -> str:
    # Forwarding headers are caller-controlled without an explicit trusted-proxy
    # configuration. Behind a proxy this intentionally shares the peer's budget.
    host, *_ = getattr(handler, "client_address", ("unknown",))
    return str(host)[:80]


def _rate_limit_per_minute() -> int:
    return _bounded_int(os.environ.get("UPKINSEY_RATE_LIMIT_PER_MINUTE"), default=30, min_value=1, max_value=600)


def _check_rate_limit(client_id: str, *, now: float | None = None) -> tuple[bool, int]:
    current = time.time() if now is None else now
    window_start = current - 60
    limit = _rate_limit_per_minute()
    with RATE_LIMIT_LOCK:
        for stale_client in [key for key, hits in RATE_LIMIT_STATE.items() if not hits or hits[-1] < window_start]:
            RATE_LIMIT_STATE.pop(stale_client, None)
        hits = [ts for ts in RATE_LIMIT_STATE.get(client_id, []) if ts >= window_start]
        if len(hits) >= limit:
            retry_after = max(1, int(round(60 - (current - hits[0])))) if hits else 60
            RATE_LIMIT_STATE[client_id] = hits
            return False, retry_after
        hits.append(current)
        RATE_LIMIT_STATE[client_id] = hits
    return True, 0


def _safe_error_message(exc: Exception | str, *, limit: int = 240) -> str:
    text = str(exc)
    for name in ("UPSTAGE_API_KEY", "UPKINSEY_BASIC_AUTH_PASSWORD"):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    for pattern in REDACTION_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text[:limit]


def _bounded_int(value, *, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def _max_body_bytes() -> int:
    return _bounded_int(
        os.environ.get("UPKINSEY_MAX_BODY_BYTES"),
        default=DEFAULT_MAX_BODY_BYTES,
        min_value=1_024,
        max_value=10_000_000,
    )


def _job_ttl_seconds() -> int:
    return _bounded_int(
        os.environ.get("UPKINSEY_JOB_TTL_SECONDS"),
        default=DEFAULT_JOB_TTL_SECONDS,
        min_value=60,
        max_value=86_400,
    )


def _cleanup_finished_jobs(now: float | None = None) -> None:
    """Drop completed in-memory job snapshots after a bounded TTL."""

    current_time = time.time() if now is None else now
    ttl = _job_ttl_seconds()
    expired: list[str] = []
    with SIMULATION_JOBS_LOCK:
        for job_id, job in SIMULATION_JOBS.items():
            if job.get("status") not in {"done", "error"}:
                continue
            timestamp = job.get("updated_at")
            if timestamp is None:
                timestamp = job.get("created_at")
            updated_at = float(current_time if timestamp is None else timestamp)
            if current_time - updated_at > ttl:
                expired.append(job_id)
        for job_id in expired:
            SIMULATION_JOBS.pop(job_id, None)


def _read_bounded_body(handler: SimpleHTTPRequestHandler, *, limit: int, too_large: str, empty: str) -> bytes:
    """Reject ambiguous framing and read exactly one bounded request body."""

    if handler.headers.get("Transfer-Encoding") is not None:
        raise ValueError("unsupported_transfer_encoding")
    if hasattr(handler.headers, "get_all") and len(handler.headers.get_all("Content-Length", [])) > 1:
        raise ValueError("invalid_content_length")
    raw_length = handler.headers.get("Content-Length")
    if raw_length is None:
        raise ValueError("missing_content_length")
    if not re.fullmatch(r"[0-9]+", raw_length):
        raise ValueError("invalid_content_length")
    try:
        length = int(raw_length)
    except ValueError as exc:
        raise ValueError("invalid_content_length") from exc
    if length <= 0:
        raise ValueError(empty)
    if length > limit:
        raise ValueError(too_large)
    try:
        raw = handler.rfile.read(length)
    except TimeoutError as exc:
        raise ValueError("request_body_timeout") from exc
    if len(raw) != length:
        raise ValueError("incomplete_request_body")
    return raw


def _read_json_body(handler: SimpleHTTPRequestHandler) -> dict:
    """Read a bounded JSON object request body with finite JSON numbers."""

    content_type = handler.headers.get("Content-Type")
    if content_type and content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise ValueError("application_json_required")
    raw = _read_bounded_body(handler, limit=_max_body_bytes(), too_large="request_body_too_large", empty="empty_request_body")

    def finite_number(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("invalid_json_body")
        return parsed

    try:
        payload = json.loads(raw.decode("utf-8"), parse_float=finite_number, parse_constant=finite_number)
    except UnicodeDecodeError as exc:
        raise ValueError("request_body_must_be_utf8") from exc
    except (ValueError, RecursionError) as exc:
        raise ValueError("invalid_json_body") from exc
    if not isinstance(payload, dict):
        raise ValueError("request_body_must_be_object")
    return payload


def _max_document_bytes() -> int:
    return _bounded_int(
        os.environ.get("UPKINSEY_MAX_DOCUMENT_BYTES"),
        default=DEFAULT_MAX_DOCUMENT_BYTES,
        min_value=10_000,
        max_value=50_000_000,
    )


def _read_uploaded_document(handler: SimpleHTTPRequestHandler) -> tuple[bytes, str, str]:
    """Read a bounded multipart PDF upload from `file` or `document`."""

    content_type = handler.headers.get("Content-Type", "")
    if content_type.split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise ValueError("multipart_form_data_required")
    raw = _read_bounded_body(handler, limit=_max_document_bytes(), too_large="document_too_large", empty="empty_document_upload")
    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode("utf-8") + b"\r\n\r\n" + raw
    )
    for part in message.iter_parts():
        disposition = part.get("Content-Disposition", "")
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        if "form-data" not in disposition or name not in {"file", "document"}:
            continue
        data = part.get_payload(decode=True) or b""
        if not data:
            raise ValueError("uploaded_document_empty")
        if len(data) > _max_document_bytes():
            raise ValueError("document_too_large")
        part_type = part.get_content_type() or "application/pdf"
        if part_type not in {"application/pdf", "application/octet-stream"} and not (filename or "").lower().endswith(".pdf"):
            raise ValueError("only_pdf_uploads_supported")
        if not data.startswith(b"%PDF-"):
            raise ValueError("uploaded_document_must_be_pdf")
        return data, filename or "document.pdf", part_type
    raise ValueError("missing_pdf_file")


def load_or_sample_personas(payload: dict) -> list[dict]:
    """Return exactly the requested simulation panel from Nemotron-Personas-Korea.

    The old prototype silently fell back to four built-in sample personas. For
    the web app that is misleading: if the UI requests 100, Layer 4 must expose
    100 persona-level responses and Layer 5 must summarize the same 100. This
    helper caches the sampled panel by seed and sample size so repeat runs do
    not re-stream the dataset.
    """

    sample_size = _bounded_int(payload.get("sample_size"), default=100, min_value=1, max_value=200)
    seed = _bounded_int(payload.get("seed"), default=42, min_value=0, max_value=2_147_483_647)
    cache_path = PERSONA_CACHE / f"nemotron_seed{seed}_n{sample_size}.jsonl"

    with PERSONA_CACHE_LOCK:
        if cache_path.exists():
            return load_personas_jsonl(cache_path, limit=sample_size)

        try:
            from datasets import load_dataset
        except ImportError as exc:  # pragma: no cover - depends on optional local extra
            raise RuntimeError(
                "Nemotron persona sampling requires the optional dependency `datasets`. "
                "Install with: python3 -m pip install -e '.[persona]'"
            ) from exc

        PERSONA_CACHE.mkdir(parents=True, exist_ok=True)
        tmp_path = PERSONA_CACHE / f".{cache_path.stem}-{uuid.uuid4().hex[:8]}.tmp"
        try:
            stream = load_dataset(DATASET_ID, split="train", streaming=True)
            rows = stream.shuffle(seed=seed, buffer_size=10_000).take(sample_size)
            with tmp_path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    compact = compact_persona_from_row(dict(row))
                    compact["sampling"] = {
                        "dataset_id": DATASET_ID,
                        "split": "train",
                        "seed": seed,
                        "buffer_size": 10_000,
                        "method": "server_streaming_shuffle_take",
                    }
                    handle.write(json.dumps(compact, ensure_ascii=False) + "\n")
            tmp_path.replace(cache_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    return load_personas_jsonl(cache_path, limit=sample_size)


def _job_snapshot(job_id: str) -> dict:
    _cleanup_finished_jobs()
    with SIMULATION_JOBS_LOCK:
        job = dict(SIMULATION_JOBS.get(job_id) or {})
    if not job:
        raise KeyError(job_id)
    job["job_id"] = job_id
    return job


def _update_job(job_id: str, **patch) -> None:
    _cleanup_finished_jobs()
    with SIMULATION_JOBS_LOCK:
        job = SIMULATION_JOBS.setdefault(job_id, {})
        job.update(patch)
        job["updated_at"] = time.time()


def _run_simulation_job(job_id: str, payload: dict) -> None:
    try:
        max_workers = _bounded_int(
            payload.get("max_parallel_requests") or os.environ.get("UPKINSEY_MAX_PARALLEL_REQUESTS", "2"),
            default=2,
            min_value=1,
            max_value=8,
        )
        requested = _bounded_int(payload.get("sample_size"), default=100, min_value=1, max_value=200)
        _update_job(
            job_id,
            status="running",
            stage="sampling",
            message=f"Nemotron persona {requested}명 패널 준비 중",
            completed=0,
            total=requested,
            percent=3,
        )
        personas = load_or_sample_personas(payload)
        _update_job(
            job_id,
            stage="persona_calls",
            message=f"Solar Pro 3로 {len(personas)}명 persona 응답 생성 시작",
            completed=0,
            total=len(personas),
            percent=8,
        )

        def progress(event: dict) -> None:
            completed = int(event.get("completed") or 0)
            total = max(1, int(event.get("total") or len(personas) or 1))
            stage = event.get("stage") or "persona_calls"
            base = 8 if stage == "persona_calls" else 92
            percent = min(95, base + round((completed / total) * 84)) if stage == "persona_calls" else 96
            _update_job(
                job_id,
                status="running",
                stage=stage,
                message=event.get("message") or "진행 중",
                completed=completed,
                total=total,
                percent=percent,
            )

        result = simulate_market_research(payload, personas=personas, max_workers=max_workers, progress_callback=progress)
        _update_job(job_id, stage="saving", message="결과 버전 저장 중", completed=len(personas), total=len(personas), percent=98)
        saved = save_simulation_run(RUN_STORE, payload, result)
        result = dict(result)
        result["version"] = saved["summary"]
        _update_job(
            job_id,
            status="done",
            stage="complete",
            message="시뮬레이션 완료",
            completed=len(personas),
            total=len(personas),
            percent=100,
            result=result,
        )
    except Exception as exc:
        _update_job(
            job_id,
            status="error",
            stage="error",
            message=_safe_error_message(exc),
            error="simulation_failed",
            percent=100,
        )


def _validate_followup_payload(payload: dict, path: str) -> None:
    """Validate nested containers before constructing prompts or using quota."""

    object_fields = ("brief", "persona") if path == "/api/persona-chat" else ("brief",)
    for field in object_fields:
        if field in payload and payload[field] is not None and not isinstance(payload[field], dict):
            raise ValueError(f"{field} must be an object")
    list_field = "history" if path == "/api/persona-chat" else "persona_reactions"
    items = payload.get(list_field)
    if items is not None and (not isinstance(items, list) or any(not isinstance(item, dict) for item in items)):
        raise ValueError(f"{list_field} must be a list of objects")


class Handler(SimpleHTTPRequestHandler):
    # Bound idle header/body reads; upstream calls retain their own API timeouts.
    timeout = 30

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "prototype"), **kwargs)

    def send_head(self):
        """Keep static responses inside the public root, including index links."""

        static_directory = Path(self.directory)
        root = static_directory.resolve()
        path = Path(self.translate_path(self.path))
        candidates = [path]
        try:
            if path.is_dir():
                candidates.extend(path / name for name in ("index.html", "index.htm"))
            for candidate in candidates:
                relative = candidate.resolve().relative_to(root)
                lexical = candidate.relative_to(static_directory)
                if any(part.startswith(".") for part in (*relative.parts, *lexical.parts)):
                    raise ValueError("private_path")
        except (ValueError, OSError, RuntimeError):
            self.send_error(404, "File not found")
            return None
        return super().send_head()

    def list_directory(self, path):
        self.send_error(404, "File not found")
        return None

    def _require_auth(self) -> bool:
        if _basic_auth_allowed(self.headers.get("Authorization")):
            return True
        if _basic_auth_enabled() and not _basic_auth_configured():
            body = json.dumps({"error": "auth_not_configured"}).encode("utf-8")
            self.send_response(503)
        else:
            body = json.dumps({"error": "authentication_required"}).encode("utf-8")
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Upkinsey"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        return False

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _require_same_origin(self) -> bool:
        """Reject browser cross-site mutations, including multipart PDF posts.

        CLI/API clients without Origin remain supported. Reverse proxies must
        preserve the public Host header; untrusted forwarding headers are ignored.
        """

        origin = self.headers.get("Origin")
        cross_site = self.headers.get("Sec-Fetch-Site", "").lower() == "cross-site"
        if origin:
            try:
                parsed = urlparse(origin)
                cross_site = cross_site or parsed.scheme not in {"http", "https"} or (
                    parsed.netloc.lower() != self.headers.get("Host", "").lower()
                )
            except ValueError:
                cross_site = True
        if cross_site:
            self._send_json(403, {"error": "cross_origin_request_denied"})
            return False
        return True

    def _check_mutating_api_budget(self) -> bool:
        ok, retry_after = _check_rate_limit(_client_id(self))
        if ok:
            return True
        body = {"error": "rate_limited", "retry_after_seconds": retry_after}
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(429)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Retry-After", str(retry_after))
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        return False

    def do_HEAD(self):
        # Use the same auth and API routing as GET, without response bodies.
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json(
                200,
                {
                    "ok": bool(os.environ.get("UPSTAGE_API_KEY")),
                    "model": os.environ.get("UPSTAGE_MODEL", "solar-pro3"),
                    "runs": len(list_simulation_runs(RUN_STORE)),
                    "auth_required": _basic_auth_enabled(),
                    "auth_configured": _basic_auth_configured(),
                    "active_jobs": _active_job_count(),
                },
            )
            return
        if not self._require_auth():
            return
        if parsed.path == "/api/runs":
            self._send_json(200, {"runs": list_simulation_runs(RUN_STORE)})
            return
        if parsed.path.startswith("/api/simulate/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            try:
                self._send_json(200, _job_snapshot(job_id))
            except KeyError:
                self._send_json(404, {"error": "job_not_found"})
            return
        if parsed.path.startswith("/api/runs/compare/"):
            version_id = parsed.path.rsplit("/", 1)[-1]
            try:
                self._send_json(200, compare_simulation_run(RUN_STORE, version_id))
            except FileNotFoundError:
                self._send_json(404, {"error": "version_not_found"})
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
            return
        if parsed.path.startswith("/api/runs/"):
            version_id = parsed.path.rsplit("/", 1)[-1]
            try:
                self._send_json(200, load_simulation_run(RUN_STORE, version_id))
            except FileNotFoundError:
                self._send_json(404, {"error": "version_not_found"})
            except ValueError:
                self._send_json(400, {"error": "invalid_version_id"})
            return
        if self.command == "HEAD":
            super().do_HEAD()
        else:
            super().do_GET()

    def do_DELETE(self):
        if not self._require_auth():
            return
        if not self._require_same_origin():
            return
        if not _destructive_api_enabled():
            self._send_json(403, {"error": "destructive_api_disabled"})
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/runs":
            self._send_json(200, clear_simulation_runs(RUN_STORE, trash_dir=RUN_TRASH))
            return
        if parsed.path.startswith("/api/runs/"):
            version_id = parsed.path.rsplit("/", 1)[-1]
            try:
                self._send_json(200, delete_simulation_run(RUN_STORE, version_id, trash_dir=RUN_TRASH))
            except FileNotFoundError:
                self._send_json(404, {"error": "version_not_found"})
            except ValueError:
                self._send_json(400, {"error": "invalid_version_id"})
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self):
        if not self._require_auth():
            return
        if not self._require_same_origin():
            return
        parsed = urlparse(self.path)
        if parsed.path not in {"/api/simulate", "/api/simulate/start", "/api/persona-chat", "/api/analyst-question", "/api/document-brief"}:
            self._send_json(404, {"error": "not_found"})
            return
        if parsed.path in MUTATING_API_PATHS and not self._check_mutating_api_budget():
            return
        synchronous_job_id = None
        try:
            if parsed.path == "/api/document-brief":
                file_bytes, filename, content_type = _read_uploaded_document(self)
                payload = {}
            else:
                payload = _read_json_body(self)
                if parsed.path in {"/api/persona-chat", "/api/analyst-question"}:
                    _validate_followup_payload(payload, parsed.path)

            # Every paid route shares the same process-wide concurrency budget.
            job_id, max_active_jobs = _reserve_simulation_job(payload)
            if not job_id:
                self._send_json(429, {"error": "too_many_active_jobs", "max_active_jobs": max_active_jobs})
                return
            if parsed.path == "/api/simulate/start":
                try:
                    threading.Thread(target=_run_simulation_job, args=(job_id, payload), daemon=True).start()
                except Exception:
                    with SIMULATION_JOBS_LOCK:
                        SIMULATION_JOBS.pop(job_id, None)
                    raise
                result = _job_snapshot(job_id)
            else:
                synchronous_job_id = job_id
                if parsed.path == "/api/document-brief":
                    result = parse_document_to_brief(file_bytes, filename=filename, content_type=content_type)
                elif parsed.path == "/api/simulate":
                    max_workers = _bounded_int(
                        payload.get("max_parallel_requests") or os.environ.get("UPKINSEY_MAX_PARALLEL_REQUESTS", "2"),
                        default=2,
                        min_value=1,
                        max_value=8,
                    )
                    personas = load_or_sample_personas(payload)
                    result = simulate_market_research(payload, personas=personas, max_workers=max_workers)
                    saved = save_simulation_run(RUN_STORE, payload, result)
                    result = dict(result)
                    result["version"] = saved["summary"]
                elif parsed.path == "/api/persona-chat":
                    result = chat_with_persona(
                        payload.get("brief") or {},
                        payload.get("persona") or {},
                        payload.get("message") or "",
                        history=payload.get("history") or [],
                    )
                else:
                    result = analyst_question_personas(
                        payload.get("brief") or {},
                        payload.get("persona_reactions") or [],
                        payload.get("question") or "",
                        target_limit=_bounded_int(payload.get("target_limit"), default=4, min_value=1, max_value=8),
                        max_workers=_bounded_int(payload.get("max_parallel_requests"), default=4, min_value=1, max_value=6),
                        max_rounds=_bounded_int(payload.get("max_rounds"), default=5, min_value=1, max_value=5),
                    )
        except ValueError as exc:
            self._send_json(400, {"error": _safe_error_message(exc)})
            return
        except RuntimeError as exc:
            self._send_json(502, {"error": "upstream_or_runtime_failure", "message": _safe_error_message(exc)})
            return
        except Exception as exc:
            # Never expose secrets; only return sanitized error text.
            self._send_json(500, {"error": "simulation_failed", "message": _safe_error_message(exc)})
            return
        finally:
            if synchronous_job_id:
                with SIMULATION_JOBS_LOCK:
                    SIMULATION_JOBS.pop(synchronous_job_id, None)
        self._send_json(200, result)


def main() -> None:
    load_env(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("UPKINSEY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", os.environ.get("UPKINSEY_PORT", "5173"))))
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving Upkinsey prototype at http://{args.host}:{args.port}")
    print("POST /api/simulate is backed by Upstage Solar.")
    print("POST /api/document-brief is backed by Upstage Document Parse + Solar extraction.")
    server.serve_forever()


if __name__ == "__main__":
    main()
