"""Minimal, reviewed Upstage Solar chat client.

The client keeps API keys out of logs/errors, validates request payloads before
sending, and retries transient failures with bounded exponential backoff.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

DEFAULT_BASE_URL = "https://api.upstage.ai/v1/solar/chat/completions"
DEFAULT_MODEL = "solar-pro3"
ALLOWED_ROLES = {"system", "user", "assistant"}
RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0


@dataclass(frozen=True)
class UpstageConfig:
    api_key: str
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    max_retries: int = 8
    retry_backoff_seconds: float = 1.5
    max_retry_delay_seconds: float = 60.0
    min_request_interval_seconds: float = 0.0

    @classmethod
    def from_env(cls) -> "UpstageConfig":
        api_key = os.environ.get("UPSTAGE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("UPSTAGE_API_KEY is not set")
        return cls(
            api_key=api_key,
            model=os.environ.get("UPSTAGE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            base_url=os.environ.get("UPSTAGE_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
            max_retries=int(os.environ.get("UPSTAGE_MAX_RETRIES", "8")),
            retry_backoff_seconds=float(os.environ.get("UPSTAGE_RETRY_BACKOFF_SECONDS", "1.5")),
            max_retry_delay_seconds=float(os.environ.get("UPSTAGE_MAX_RETRY_DELAY_SECONDS", "60")),
            min_request_interval_seconds=float(os.environ.get("UPSTAGE_MIN_REQUEST_INTERVAL_SECONDS", "1.1")),
        )


def _validate_messages(messages: list[dict[str, str]]) -> None:
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty list")
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValueError(f"message[{index}] must be an object")
        role = message.get("role")
        content = message.get("content")
        if role not in ALLOWED_ROLES:
            raise ValueError(f"message[{index}].role must be one of {sorted(ALLOWED_ROLES)}")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"message[{index}].content must be a non-empty string")


def _redact(text: str, secret: str) -> str:
    if not secret:
        return text
    return text.replace(secret, "[REDACTED_UPSTAGE_API_KEY]")


class UpstageClient:
    def __init__(self, config: UpstageConfig | None = None):
        self.config = config or UpstageConfig.from_env()

    def build_payload(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, str] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1400,
    ) -> dict[str, Any]:
        """Validate and build an Upstage chat completion payload."""

        _validate_messages(messages)
        if not isinstance(temperature, (int, float)) or not 0 <= float(temperature) <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if not isinstance(max_tokens, int) or not 1 <= max_tokens <= 8192:
            raise ValueError("max_tokens must be an integer between 1 and 8192")

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": float(temperature),
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format
        return payload

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, str] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1400,
        timeout: int = 60,
    ) -> dict[str, Any]:
        payload = self.build_payload(
            messages,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        body = json.dumps(payload).encode("utf-8")

        last_error: Exception | None = None
        attempts = max(0, self.config.max_retries) + 1
        for attempt in range(attempts):
            self._wait_for_rate_window()
            request = urllib.request.Request(
                self.config.base_url,
                data=body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = _redact(exc.read().decode("utf-8", errors="replace"), self.config.api_key)[:1200]
                retry_after = _retry_after_from_headers(exc.headers)
                last_error = RuntimeError(f"Upstage API error {exc.code}: {detail}")
                if exc.code not in RETRYABLE_STATUS_CODES or attempt == attempts - 1:
                    raise last_error from None
                self._sleep_before_retry(attempt, retry_after)
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = RuntimeError(f"Upstage API transport error: {_redact(str(exc), self.config.api_key)}")
                if attempt == attempts - 1:
                    raise last_error from None
                self._sleep_before_retry(attempt, None)

        raise RuntimeError(f"Upstage API failed: {last_error}")

    def _wait_for_rate_window(self) -> None:
        """Apply a small process-wide request spacing guard before Upstage calls.

        Upstage enforces account-level rate limits that can be shared by chat,
        document parse, and concurrent persona workers. This does not replace
        server-side Retry-After handling; it simply avoids bursting many
        requests at the same millisecond when a panel run starts.
        """

        global _LAST_REQUEST_AT
        interval = max(0.0, float(self.config.min_request_interval_seconds or 0.0))
        if interval <= 0:
            return
        with _REQUEST_LOCK:
            now = time.monotonic()
            wait_for = interval - (now - _LAST_REQUEST_AT)
            if wait_for > 0:
                time.sleep(wait_for)
                now = time.monotonic()
            _LAST_REQUEST_AT = now

    def _sleep_before_retry(self, attempt: int, retry_after: float | None) -> None:
        delay = retry_after
        if delay is None:
            delay = self.config.retry_backoff_seconds * (2**attempt)
        # Small jitter prevents all persona workers from retrying together.
        delay = min(max(0.0, delay), max(1.0, float(self.config.max_retry_delay_seconds or 60.0)))
        if retry_after is None and delay > 0:
            delay += random.uniform(0, min(0.35, delay * 0.1))
        delay = min(delay, max(1.0, float(self.config.max_retry_delay_seconds or 60.0)))
        time.sleep(delay)

    def complete_text(self, prompt: str, *, system: str | None = None, **kwargs: Any) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        data = self.chat_completion(messages, **kwargs)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("Upstage API returned no text completion") from None
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Upstage API returned no text completion")
        return content


def _retry_after_from_headers(headers: Any) -> float | None:
    if not headers:
        return None
    retry_after = headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                return max(0.0, parsedate_to_datetime(retry_after).timestamp() - time.time())
            except Exception:
                return None
    for key in ("X-RateLimit-Reset", "X-Rate-Limit-Reset"):
        value = headers.get(key)
        if not value:
            continue
        try:
            reset_at = float(value)
        except ValueError:
            continue
        # Some providers send epoch seconds, others send seconds-until-reset.
        return max(0.0, reset_at - time.time()) if reset_at > 1_000_000_000 else max(0.0, reset_at)
    return None
