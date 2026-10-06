"""Upstage Document Parse helpers for extracting a market-research brief from PDFs."""

from __future__ import annotations

import json
import os
import re
import uuid
import urllib.error
import urllib.request
from typing import Any

from .market_research import SYSTEM_PROMPT, _extract_json
from .upstage_client import RETRYABLE_STATUS_CODES, UpstageClient, UpstageConfig, _redact, _retry_after_from_headers

DEFAULT_DOCUMENT_PARSE_URL = "https://api.upstage.ai/v1/document-ai/document-parse"


def _document_parse_url() -> str:
    return os.environ.get("UPSTAGE_DOCUMENT_PARSE_URL", DEFAULT_DOCUMENT_PARSE_URL).strip() or DEFAULT_DOCUMENT_PARSE_URL


def _safe_filename(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9가-힣._ -]+", "_", filename or "document.pdf").strip()
    return cleaned[:120] or "document.pdf"


def _multipart_body(fields: dict[str, str], file_field: str, filename: str, content_type: str, data: bytes) -> tuple[bytes, str]:
    boundary = f"----upkinsey-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{file_field}"; filename="{_safe_filename(filename)}"\r\n'.encode(),
            f"Content-Type: {content_type or 'application/pdf'}\r\n\r\n".encode(),
            data,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), boundary


def call_document_parse_api(file_bytes: bytes, *, filename: str = "document.pdf", content_type: str = "application/pdf") -> dict[str, Any]:
    """Call Upstage Document Parse with a PDF payload.

    The public API currently accepts multipart uploads at the Document AI
    document-parse endpoint. We keep provider-specific knobs in env vars so the
    prototype can follow API changes without frontend changes.
    """

    if not file_bytes:
        raise ValueError("empty document")
    if len(file_bytes) > int(os.environ.get("UPKINSEY_MAX_DOCUMENT_BYTES", "20000000")):
        raise ValueError("document is too large")

    config = UpstageConfig.from_env()
    client_guard = UpstageClient(config)
    fields = {
        "model": os.environ.get("UPSTAGE_DOCUMENT_PARSE_MODEL", "document-parse"),
        "output_formats": os.environ.get("UPSTAGE_DOCUMENT_PARSE_OUTPUT_FORMATS", "text,html"),
    }
    # Upstage docs/examples have used `document` as the file field. Keep it
    # configurable to avoid shipping a frontend change if the API variant differs.
    file_field = os.environ.get("UPSTAGE_DOCUMENT_PARSE_FILE_FIELD", "document")
    body, boundary = _multipart_body(fields, file_field, filename, content_type, file_bytes)
    attempts = max(0, config.max_retries) + 1
    timeout = int(os.environ.get("UPSTAGE_DOCUMENT_PARSE_TIMEOUT", "120"))
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            _document_parse_url(),
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
            },
        )
        try:
            client_guard._wait_for_rate_window()
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"text": text, "raw_text_response": True}
        except urllib.error.HTTPError as exc:
            detail = _redact(exc.read().decode("utf-8", errors="replace"), config.api_key)[:1200]
            last_error = RuntimeError(f"Upstage Document Parse API error {exc.code}: {detail}")
            if exc.code not in RETRYABLE_STATUS_CODES or attempt == attempts - 1:
                raise last_error from None
            client_guard._sleep_before_retry(attempt, _retry_after_from_headers(exc.headers))
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = RuntimeError(f"Upstage Document Parse transport error: {_redact(str(exc), config.api_key)}")
            if attempt == attempts - 1:
                raise last_error from None
            client_guard._sleep_before_retry(attempt, None)

    raise RuntimeError(f"Upstage Document Parse failed: {last_error}")


def _collect_text(value: Any, out: list[str], *, depth: int = 0) -> None:
    if depth > 8 or value is None:
        return
    if isinstance(value, str):
        text = re.sub(r"\s+", " ", value).strip()
        if text:
            out.append(text)
        return
    if isinstance(value, list):
        for item in value:
            _collect_text(item, out, depth=depth + 1)
        return
    if isinstance(value, dict):
        # Prefer semantic fields before traversing everything.
        for key in ("text", "content", "markdown", "html", "ocr_text", "value"):
            if key in value:
                _collect_text(value[key], out, depth=depth + 1)
        for key in ("elements", "pages", "paragraphs", "tables", "chunks"):
            if key in value:
                _collect_text(value[key], out, depth=depth + 1)


def extract_document_text(parse_response: dict[str, Any]) -> str:
    parts: list[str] = []
    _collect_text(parse_response, parts)
    seen: set[str] = set()
    deduped: list[str] = []
    for part in parts:
        normalized = part[:500]
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(part)
    return "\n".join(deduped)[:24000]


def extract_brief_from_document_text(document_text: str, *, client: UpstageClient | None = None) -> dict[str, Any]:
    if not document_text.strip():
        raise ValueError("Document Parse returned no readable text")
    client = client or UpstageClient()
    prompt = f"""
다음은 사용자가 업로드한 제품/서비스 소개 PDF에서 Document Parse API로 추출한 텍스트입니다.
업킨지 시장 반응 시뮬레이션의 제품 정보 입력 폼을 자동으로 채우기 위한 JSON만 반환하세요.

규칙:
- 원문에 근거가 있는 내용만 채우세요. 모르면 빈 문자열 또는 빈 배열.
- `description`은 고객에게 보여줄 1~2문장 소개로 압축하세요.
- `features`는 핵심 기능 3~6개.
- `pricing`은 가격/요금/플랜 정보가 명시된 경우만.
- `target`은 타깃 고객/사용자/구매자를 한 문장으로.
- `alternatives`는 경쟁재/대체재/현재 해결방식이 있으면.
- `hypothesis`는 시장 검증 질문 1개를 제안하세요.
- 한국어로 작성하세요.

반환 JSON 스키마:
{{
  "productName": "",
  "description": "",
  "features": [],
  "pricing": [],
  "target": "",
  "alternatives": "",
  "hypothesis": "",
  "confidence": 0,
  "evidence": ["어떤 문구에서 추출했는지 짧게"]
}}

문서 텍스트:
{document_text[:22000]}
""".strip()
    raw = client.complete_text(prompt, system=SYSTEM_PROMPT, temperature=0.1, max_tokens=1800, timeout=120)
    data = _extract_json(raw)
    return normalize_extracted_brief(data)


def normalize_extracted_brief(data: dict[str, Any]) -> dict[str, Any]:
    def text(key: str, limit: int = 1200) -> str:
        return str(data.get(key) or "").strip()[:limit]

    def list_text(key: str, *, limit: int = 8) -> list[str]:
        value = data.get(key)
        if isinstance(value, list):
            items = value
        elif value:
            items = re.split(r"[,\n;]+", str(value))
        else:
            items = []
        return [str(item).strip()[:160] for item in items if str(item).strip()][:limit]

    product_name = text("productName", 160)
    description = text("description", 1200)
    features = list_text("features", limit=8)
    pricing = list_text("pricing", limit=6)
    target = text("target", 800)
    alternatives = text("alternatives", 800)
    hypothesis = text("hypothesis", 800)
    try:
        raw_confidence = float(data.get("confidence") or 0)
        confidence = int(round(raw_confidence * 100 if 0 < raw_confidence <= 1 else raw_confidence))
    except (TypeError, ValueError, OverflowError):
        confidence = 0
    if confidence <= 0:
        filled = sum(bool(value) for value in [product_name, description, target, alternatives, hypothesis])
        filled += min(2, len(features)) + min(1, len(pricing))
        confidence = min(92, 35 + filled * 8) if filled else 0
    confidence = max(0, min(100, confidence))
    return {
        "productName": product_name,
        "description": description,
        "features": features,
        "pricing": pricing,
        "target": target,
        "alternatives": alternatives,
        "hypothesis": hypothesis,
        "confidence": confidence,
        "evidence": list_text("evidence", limit=8),
    }


def parse_document_to_brief(file_bytes: bytes, *, filename: str = "document.pdf", content_type: str = "application/pdf") -> dict[str, Any]:
    parse_response = call_document_parse_api(file_bytes, filename=filename, content_type=content_type)
    document_text = extract_document_text(parse_response)
    brief = extract_brief_from_document_text(document_text)
    return {
        "brief": brief,
        "document_text_preview": document_text[:1800],
        "document_parse": {
            "endpoint": _document_parse_url(),
            "text_length": len(document_text),
            "top_level_keys": sorted(parse_response.keys())[:20] if isinstance(parse_response, dict) else [],
        },
    }
