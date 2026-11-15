"""The one place models are called. Text in, text out: the agents use no tools.

Providers
  anthropic : Claude through the Anthropic API (default; model claude-haiku-5-5)
  openai    : any OpenAI-compatible /chat/completions endpoint (OpenAI, OpenRouter, Ollama, ...)

Cache modes (RULEBOOK_LLM_MODE)
  record : reuse a saved answer when the request is identical, otherwise call and save (default).
           A second review of an unchanged line costs nothing.
  live   : always call, save nothing.
  replay : saved answers only; a miss is an error. For tests and reproducible evals.

The cache key hashes model + system prompt + messages + temperature + max_tokens.

Settings come from the environment (the Action passes its inputs the same way):
  RULEBOOK_PROVIDER, RULEBOOK_MODEL, RULEBOOK_BASE_URL, RULEBOOK_API_KEY (or ANTHROPIC_API_KEY),
  RULEBOOK_LLM_MODE, RULEBOOK_CACHE_DIR, RULEBOOK_MAX_CALLS, RULEBOOK_TIMEOUT_S,
  RULEBOOK_PRICE_IN_PER_MTOK, RULEBOOK_PRICE_OUT_PER_MTOK (cost is reported only when set).

Ported from Docker Locker's llm_client.py (Hesham Rashid).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional, Protocol

DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5"}
PROVIDERS = ("anthropic", "openai")
MODES = ("record", "live", "replay")


class LLMError(Exception):
    """The model could not be reached or refused the request. Never treated as an answer."""


class ReplayMiss(LLMError):
    pass


class CallLimitReached(LLMError):
    pass


@dataclass
class RawCompletion:
    text: str
    stop_reason: str
    input_tokens: int
    output_tokens: int


@dataclass
class LLMResponse:
    text: str
    stop_reason: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    cached: bool = False


class Backend(Protocol):
    def complete(self, *, model: str, system: str, messages: list[dict], temperature: float,
                 max_tokens: int) -> RawCompletion: ...


class AnthropicBackend:
    def __init__(self, api_key: str, base_url: Optional[str] = None, timeout: float = 120.0,
                 http_client: Any = None):
        import inspect

        import anthropic

        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout, "max_retries": 2}
        if base_url:
            kwargs["base_url"] = base_url
        if http_client is not None:   # tests: a mocked transport
            kwargs["http_client"] = http_client
        self._client = anthropic.Anthropic(**kwargs)
        self._anthropic = anthropic
        # Newer SDKs dropped `temperature` from messages.create(); send it in the body then,
        # and drop it entirely if the API rejects it.
        self._sdk_has_temperature = "temperature" in inspect.signature(self._client.messages.create).parameters
        self._send_temperature = True

    def complete(self, *, model, system, messages, temperature, max_tokens) -> RawCompletion:
        kwargs: dict[str, Any] = dict(model=model, messages=messages, max_tokens=max_tokens)
        if system:
            kwargs["system"] = system
        if self._send_temperature:
            if self._sdk_has_temperature:
                kwargs["temperature"] = temperature
            else:
                kwargs["extra_body"] = {"temperature": temperature}
        try:
            msg = self._client.messages.create(**kwargs)
        except self._anthropic.BadRequestError as exc:
            if "temperature" not in str(exc).lower() or not self._send_temperature:
                raise
            self._send_temperature = False
            kwargs.pop("temperature", None)
            kwargs.pop("extra_body", None)
            msg = self._client.messages.create(**kwargs)
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return RawCompletion(text=text, stop_reason=msg.stop_reason or "",
                             input_tokens=msg.usage.input_tokens, output_tokens=msg.usage.output_tokens)


class OpenAICompatBackend:
    def __init__(self, base_url: str, api_key: Optional[str] = None, timeout: float = 120.0):
        import httpx

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def complete(self, *, model, system, messages, temperature, max_tokens) -> RawCompletion:
        wire = ([{"role": "system", "content": system}] if system else []) + [
            {"role": m["role"], "content": m["content"]} for m in messages]
        resp = self._http.post("/chat/completions", json={
            "model": model, "messages": wire, "temperature": temperature, "max_tokens": max_tokens})
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        return RawCompletion(text=choice["message"].get("content") or "",
                             stop_reason=choice.get("finish_reason") or "",
                             input_tokens=int(usage.get("prompt_tokens", 0)),
                             output_tokens=int(usage.get("completion_tokens", 0)))


class LLMClient:
    def __init__(self, model: str = DEFAULT_MODELS["anthropic"], *, provider: str = "anthropic",
                 mode: str = "record", cache_dir: str | Path = ".rulebook-cache/llm",
                 backend: Optional[Backend] = None, base_url: Optional[str] = None,
                 api_key: Optional[str] = None, temperature: float = 0.0, max_tokens: int = 1024,
                 max_calls: int = 200, timeout_s: float = 120.0,
                 price_in_per_mtok: float = 0.0, price_out_per_mtok: float = 0.0):
        if provider not in PROVIDERS:
            raise LLMError(f"provider must be one of {', '.join(PROVIDERS)}, not {provider!r}")
        if mode not in MODES:
            raise LLMError(f"RULEBOOK_LLM_MODE must be one of {', '.join(MODES)}, not {mode!r}")
        if not model:
            raise LLMError(f"no model set; set RULEBOOK_MODEL (provider {provider} has no default)")
        self.model, self.provider, self.mode = model, provider, mode
        self.cache_dir = Path(cache_dir)
        self.temperature, self.max_tokens, self.max_calls = temperature, max_tokens, max_calls
        self.timeout_s = timeout_s
        self.price_in, self.price_out = price_in_per_mtok, price_out_per_mtok
        self._backend, self._base_url, self._api_key = backend, base_url, api_key
        self.stats: dict[str, Any] = {"calls": 0, "live_calls": 0, "cache_hits": 0,
                                      "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    @classmethod
    def from_env(cls, model: str = "", **overrides: Any) -> "LLMClient":
        env = os.environ
        provider = env.get("RULEBOOK_PROVIDER", "anthropic").lower()
        api_key = env.get("RULEBOOK_API_KEY") or (env.get("ANTHROPIC_API_KEY") if provider == "anthropic" else None)
        try:
            cfg: dict[str, Any] = dict(
                model=env.get("RULEBOOK_MODEL") or model or DEFAULT_MODELS.get(provider, ""),
                provider=provider,
                mode=env.get("RULEBOOK_LLM_MODE", "record").lower(),
                cache_dir=env.get("RULEBOOK_CACHE_DIR", ".rulebook-cache/llm"),
                base_url=env.get("RULEBOOK_BASE_URL") or None,
                api_key=api_key or None,
                max_calls=int(env.get("RULEBOOK_MAX_CALLS", "200")),
                timeout_s=float(env.get("RULEBOOK_TIMEOUT_S", "120")),
                price_in_per_mtok=float(env.get("RULEBOOK_PRICE_IN_PER_MTOK", "0")),
                price_out_per_mtok=float(env.get("RULEBOOK_PRICE_OUT_PER_MTOK", "0")),
            )
        except ValueError as exc:
            raise LLMError(f"a RULEBOOK_ setting is not a number: {exc}") from exc
        cfg.update(overrides)
        return cls(**cfg)

    # ---- cache ---------------------------------------------------------------

    def _request(self, system: str, messages: list[dict], max_tokens: int) -> dict[str, Any]:
        return {"model": self.model, "system": system, "messages": messages,
                "temperature": self.temperature, "max_tokens": max_tokens}

    @staticmethod
    def cache_key(request: dict[str, Any]) -> str:
        canon = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()

    def _read_cache(self, key: str) -> Optional[LLMResponse]:
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))["response"]
            return LLMResponse(**{**data, "cached": True})
        except (ValueError, KeyError, TypeError, OSError):
            return None   # a damaged cache entry is a miss, not a crash

    def _write_cache(self, key: str, request: dict, response: LLMResponse) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        body = asdict(response)
        body.pop("cached", None)
        tmp = self.cache_dir / f"{key}.tmp"
        tmp.write_text(json.dumps({"key": key, "request": request, "response": body},
                                  indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.cache_dir / f"{key}.json")

    # ---- backend -------------------------------------------------------------

    def _get_backend(self) -> Backend:
        if self._backend is None:
            if self.provider == "openai":
                if not self._base_url:
                    raise LLMError("provider openai needs RULEBOOK_BASE_URL (for example "
                                   "https://api.openai.com/v1 or http://localhost:11434/v1)")
                self._backend = OpenAICompatBackend(self._base_url, self._api_key, self.timeout_s)
            else:
                if not self._api_key:
                    raise LLMError("no API key: set ANTHROPIC_API_KEY (or RULEBOOK_API_KEY)")
                self._backend = AnthropicBackend(self._api_key, self._base_url, self.timeout_s)
        return self._backend

    # ---- public --------------------------------------------------------------

    def complete(self, messages: list[dict], *, system: str = "",
                 max_tokens: Optional[int] = None) -> LLMResponse:
        request = self._request(system, messages, max_tokens or self.max_tokens)
        key = self.cache_key(request)
        self.stats["calls"] += 1

        if self.mode in ("record", "replay"):
            hit = self._read_cache(key)
            if hit is not None:
                self.stats["cache_hits"] += 1
                return hit
            if self.mode == "replay":
                raise ReplayMiss(f"no saved answer for request {key[:12]} (model {self.model})")

        if self.stats["live_calls"] >= self.max_calls:
            raise CallLimitReached(f"stopped after {self.max_calls} model calls (RULEBOOK_MAX_CALLS)")
        t0 = time.perf_counter()
        try:
            raw = self._get_backend().complete(model=self.model, system=system, messages=messages,
                                               temperature=self.temperature,
                                               max_tokens=request["max_tokens"])
        except LLMError:
            raise
        except Exception as exc:   # SDK, HTTP, auth, network: one error type for every caller
            raise LLMError(f"{self.provider} call to {self.model} failed: "
                           f"{type(exc).__name__}: {exc}") from exc
        latency = time.perf_counter() - t0
        self.stats["live_calls"] += 1
        self.stats["input_tokens"] += raw.input_tokens
        self.stats["output_tokens"] += raw.output_tokens
        self.stats["cost_usd"] += (raw.input_tokens * self.price_in + raw.output_tokens * self.price_out) / 1e6
        response = LLMResponse(text=raw.text, stop_reason=raw.stop_reason, model=self.model,
                               input_tokens=raw.input_tokens, output_tokens=raw.output_tokens,
                               latency_s=round(latency, 3))
        if self.mode == "record":
            self._write_cache(key, request, response)
        return response

    @property
    def priced(self) -> bool:
        return bool(self.price_in or self.price_out)
