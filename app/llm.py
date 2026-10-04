"""Gemma client. Runs against a local Ollama server or Google AI Studio.

Both paths use the same open-weight model family, so the prompts in this
project are written once and work on either.
"""
import json
import re
from typing import Iterator

import httpx

from . import config

GOOGLE_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class LLMError(RuntimeError):
    pass


def model_name() -> str:
    return config.OLLAMA_MODEL if config.LLM_PROVIDER == "ollama" else config.GOOGLE_MODEL


def status() -> dict:
    """Report whether Gemma is reachable, without generating anything."""
    try:
        if config.LLM_PROVIDER == "ollama":
            r = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=4)
            r.raise_for_status()
            names = [m["name"] for m in r.json().get("models", [])]
            ok = any(n == config.OLLAMA_MODEL or n.startswith(config.OLLAMA_MODEL + ":") for n in names)
            return {"ok": ok, "provider": "ollama", "model": config.OLLAMA_MODEL,
                    "detail": "ready" if ok else f"run: ollama pull {config.OLLAMA_MODEL}"}
        if not config.GOOGLE_API_KEY:
            return {"ok": False, "provider": "google", "model": config.GOOGLE_MODEL, "detail": "GOOGLE_API_KEY not set"}
        r = httpx.get(f"{GOOGLE_BASE}/{config.GOOGLE_MODEL}",
                      headers={"x-goog-api-key": config.GOOGLE_API_KEY}, timeout=6)
        return {"ok": r.status_code == 200, "provider": "google", "model": config.GOOGLE_MODEL,
                "detail": "ready" if r.status_code == 200 else r.text[:200]}
    except httpx.HTTPError as e:
        return {"ok": False, "provider": config.LLM_PROVIDER, "model": model_name(), "detail": str(e)}


def _google_contents(messages: list[dict]) -> list[dict]:
    # Gemma on AI Studio has no system role, so the system text leads the first user turn.
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = []
    for m in messages:
        if m["role"] == "system":
            continue
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    if system and contents:
        contents[0]["parts"][0]["text"] = f"{system}\n\n{contents[0]['parts'][0]['text']}"
    return contents


def _num_ctx(messages: list[dict], max_tokens: int) -> int:
    # A smaller context window is much faster on CPU, so size it to the prompt.
    need = sum(len(m["content"]) for m in messages) // 3 + max_tokens + 256
    for size in (2048, 4096, 8192):
        if need <= size:
            return min(size, config.OLLAMA_NUM_CTX)
    return config.OLLAMA_NUM_CTX


def _google_config(temperature: float, max_tokens: int) -> dict:
    # Gemma 4 reasons before answering by default. These are short, grounded tasks, so keep that minimal.
    return {"temperature": temperature, "maxOutputTokens": max_tokens, "thinkingConfig": {"thinkingLevel": "minimal"}}


def chat(messages: list[dict], temperature: float = 0.3, max_tokens: int = 1024, json_mode: bool = False) -> str:
    if config.LLM_PROVIDER == "ollama":
        body = {"model": config.OLLAMA_MODEL, "messages": messages, "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": _num_ctx(messages, max_tokens)}}
        if json_mode:
            body["format"] = "json"
        try:
            r = httpx.post(f"{config.OLLAMA_URL}/api/chat", json=body, timeout=600)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama request failed: {e}") from e
        return r.json()["message"]["content"]

    if not config.GOOGLE_API_KEY:
        raise LLMError("GOOGLE_API_KEY is not set")
    body = {"contents": _google_contents(messages),
            "generationConfig": _google_config(temperature, max_tokens)}
    try:
        r = httpx.post(f"{GOOGLE_BASE}/{config.GOOGLE_MODEL}:generateContent",
                       headers={"x-goog-api-key": config.GOOGLE_API_KEY}, json=body, timeout=180)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise LLMError(f"Google AI Studio request failed: {e}") from e
    data = r.json()
    try:
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"] if not p.get("thought"))
    except (KeyError, IndexError) as e:
        raise LLMError(f"Unexpected response: {str(data)[:300]}") from e


def stream(messages: list[dict], temperature: float = 0.3, max_tokens: int = 700) -> Iterator[str]:
    """Yield answer text as it is generated."""
    if config.LLM_PROVIDER == "ollama":
        body = {"model": config.OLLAMA_MODEL, "messages": messages, "stream": True,
                "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": _num_ctx(messages, max_tokens)}}
        try:
            with httpx.stream("POST", f"{config.OLLAMA_URL}/api/chat", json=body, timeout=600) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    piece = json.loads(line)
                    if piece.get("message", {}).get("content"):
                        yield piece["message"]["content"]
                    if piece.get("done"):
                        break
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama request failed: {e}") from e
        return

    if not config.GOOGLE_API_KEY:
        raise LLMError("GOOGLE_API_KEY is not set")
    body = {"contents": _google_contents(messages),
            "generationConfig": _google_config(temperature, max_tokens)}
    url = f"{GOOGLE_BASE}/{config.GOOGLE_MODEL}:streamGenerateContent?alt=sse"
    try:
        with httpx.stream("POST", url, headers={"x-goog-api-key": config.GOOGLE_API_KEY}, json=body, timeout=180) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = json.loads(line[5:])
                for cand in data.get("candidates", []):
                    for part in cand.get("content", {}).get("parts", []):
                        if part.get("text") and not part.get("thought"):
                            yield part["text"]
    except httpx.HTTPError as e:
        raise LLMError(f"Google AI Studio request failed: {e}") from e


def parse_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (handles ```json fences)."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError(f"Model did not return JSON: {text[:200]}")
    blob = text[start:end + 1]
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        # Small models sometimes leave trailing commas.
        return json.loads(re.sub(r",\s*([}\]])", r"\1", blob))


def chat_json(messages: list[dict], max_tokens: int = 1200) -> dict:
    return parse_json(chat(messages, temperature=0.1, max_tokens=max_tokens, json_mode=True))
