"""1B router model client (Ollama). The model may only return one of the offered choices.

Output is constrained with a JSON schema whose `choice` is an enum of the candidate branch ids
plus "none" and "ask"; anything else is treated as a failure, never as an answer.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class ModelChoice:
    choice: str | None
    raw: str
    seconds: float
    error: str = ""


def router_prompt(question: str, options: list[tuple[str, str]], context: str = "") -> tuple[str, dict, list[str]]:
    ids = [o[0] for o in options]
    lines = "\n".join(f"- {oid}: {desc}" for oid, desc in options)
    prompt = (
        "You route a user's message to ONE help topic. Pick the option that best fits, even if the wording "
        "is informal, vague, or misspelled. Reply \"none\" only if the message is about something none of the "
        "options cover. Reply \"ask\" only if two options fit equally well.\n"
        + (f"Earlier topic: {context}\n" if context else "")
        + f"Message: {question}\nOptions:\n{lines}\n"
        'Return JSON: {"choice": "<option id | none | ask>"}'
    )
    schema = {"type": "object", "properties": {"choice": {"type": "string", "enum": ids + ["none", "ask"]}},
              "required": ["choice"]}
    return prompt, schema, ids


def parse_choice(raw: str, ids: list[str], seconds: float) -> ModelChoice:
    try:
        choice = json.loads(raw).get("choice")
    except (ValueError, AttributeError):
        return ModelChoice(None, raw, seconds, "malformed")
    if choice not in ids + ["none", "ask"]:
        return ModelChoice(None, raw, seconds, "invalid choice")
    return ModelChoice(choice, raw, seconds)


class OpenAICompatRouterModel:
    """Same contract against an OpenAI-compatible server (llama.cpp server, vLLM) with JSON-schema output."""

    def __init__(self, model: str, url: str, timeout: float = 60):
        self.model, self.url, self.timeout = model, url.rstrip("/"), timeout

    def choose(self, question: str, options: list[tuple[str, str]], context: str = "") -> ModelChoice:
        prompt, schema, ids = router_prompt(question, options, context)
        body = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "temperature": 0,
                "seed": 42, "max_tokens": 32, "response_format": {"type": "json_object", "schema": schema}}
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(self.url + "/v1/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            return ModelChoice(None, "", time.monotonic() - t0, f"transport: {e}")
        raw = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return parse_choice(raw, ids, time.monotonic() - t0)


class OllamaRouterModel:
    def __init__(self, model: str, url: str = "http://127.0.0.1:11434", timeout: float = 60):
        self.model, self.url, self.timeout = model, url.rstrip("/"), timeout

    def choose(self, question: str, options: list[tuple[str, str]], context: str = "") -> ModelChoice:
        prompt, schema, ids = router_prompt(question, options, context)
        body = {"model": self.model, "stream": False, "format": schema, "keep_alive": "30m",
                "messages": [{"role": "user", "content": prompt}],
                "options": {"temperature": 0, "seed": 42, "num_predict": 32}}
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(self.url + "/api/chat", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            return ModelChoice(None, "", time.monotonic() - t0, f"transport: {e}")
        return parse_choice((data.get("message") or {}).get("content", ""), ids, time.monotonic() - t0)
