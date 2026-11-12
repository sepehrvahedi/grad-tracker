"""Optional LLM scorer client (any OpenAI-compatible endpoint) with retries, a disk cache and robust JSON parsing.

Not needed for normal use: scoring is local by default. Install the extra with `pip install "grad-tracker[llm]"`.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

try:
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        InternalServerError,
        OpenAI,
        RateLimitError,
    )
except ImportError:  # the optional extra is not installed; only a caller-supplied client can be used
    OpenAI = None

    class _Never(Exception):
        pass

    APIConnectionError = APIStatusError = APITimeoutError = InternalServerError = RateLimitError = _Never

from .config import Settings

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524}


class LLMError(RuntimeError):
    pass


class EmptyResponse(Exception):
    pass


def extract_json(text: str) -> Any:
    """Parse JSON from a model reply that may include code fences or prose."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON found in model output")
    start = min(starts)
    closer = "}" if text[start] == "{" else "]"
    end = text.rfind(closer)
    if end <= start:
        raise ValueError("unterminated JSON in model output")
    return json.loads(text[start:end + 1])


class LLM:
    def __init__(self, settings: Settings, client: Any = None, sleep: Callable[[float], None] = time.sleep):
        if not settings.api_key and client is None:
            raise LLMError("GRADTRACKER_LLM_API_KEY is not set (put it in .env in your grad-tracker folder)")
        if client is None and OpenAI is None:
            raise LLMError('the LLM scorer needs the openai package: pip install "grad-tracker[llm]"')
        if client is None and not settings.model:
            raise LLMError("GRADTRACKER_LLM_MODEL is not set")
        self.s = settings
        self.client = client or OpenAI(
            base_url=settings.base_url, api_key=settings.api_key,
            timeout=settings.llm_timeout, max_retries=0,
        )
        self.cache_dir: Path = settings.llm_cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.sleep = sleep
        self.calls = 0
        self.cache_hits = 0
        self.cost_usd = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ core
    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def chat(self, messages: list[dict], *, temperature: float = 0.3, use_cache: bool = True) -> str:
        payload = {"model": self.s.model, "messages": messages, "temperature": temperature,
                   "effort": self.s.reasoning_effort}
        key = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]
        path = self._cache_path(key)
        if use_cache and path.exists():
            with self._lock:
                self.cache_hits += 1
            return json.loads(path.read_text(encoding="utf-8"))["text"]

        last: Exception | None = None
        for attempt in range(1, self.s.llm_max_attempts + 1):
            try:
                kwargs: dict = {"model": self.s.model, "messages": messages, "temperature": temperature}
                if self.s.reasoning_effort:
                    kwargs["reasoning_effort"] = self.s.reasoning_effort
                resp = self.client.chat.completions.create(**kwargs)
                with self._lock:
                    self.calls += 1
                text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
                if not text:
                    raise EmptyResponse("empty completion")
                usage = getattr(resp, "usage", None)
                cost = getattr(usage, "cost", None)
                if cost is None and usage is not None and getattr(usage, "model_extra", None):
                    cost = usage.model_extra.get("cost")
                with self._lock:
                    self.cost_usd += float(cost or 0.0)
                path.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
                return text
            except (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError, EmptyResponse) as e:
                last = e
            except APIStatusError as e:
                if e.status_code not in RETRYABLE_STATUS:
                    raise LLMError(f"The LLM endpoint rejected the request ({e.status_code}): {e}") from e
                last = e
            if attempt < self.s.llm_max_attempts:
                self.sleep(min(60.0, 2 ** attempt) + random.uniform(0, 1))
        raise LLMError(f"The LLM endpoint failed after {self.s.llm_max_attempts} attempts: {last}")

    def json(self, messages: list[dict], *, temperature: float = 0.2) -> Any:
        """Ask for JSON; if the reply does not parse, retry once with a stricter reminder."""
        text = self.chat(messages, temperature=temperature)
        try:
            return extract_json(text)
        except (ValueError, json.JSONDecodeError):
            fix = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": "That was not valid JSON. Reply with the JSON only, no prose, no code fences."},
            ]
            return extract_json(self.chat(fix, temperature=0.0, use_cache=False))

    def stats(self) -> str:
        return f"{self.calls} API calls, {self.cache_hits} cache hits, ~${self.cost_usd:.4f}"
