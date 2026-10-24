"""Thin client for Nebius Token Factory (OpenAI-compatible) with a per-run cost meter.

Model ids are the exact strings from https://tokenfactory.nebius.com/api/public/models_info (checked 2026-10-22).
Prices are USD per million tokens from the same catalogue; the meter multiplies the `usage` field
of every response so the UI can show what a kitchen cost.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1")

MODELS = {
    "vl": "nvidia/Nemotron-3.5-Super-VL",          # reads the letter, boxes and organiser photos
    "reason": "nvidia/Nemotron-3-Ultra-550b-a55b",  # audits the letter, writes pharmacist questions
    "fast": "nvidia/Nemotron-3_5-Lightning",        # guards every sentence, answers the ask box
}
PRICES = {  # (input, output) USD per 1M tokens
    "nvidia/Nemotron-3.5-Super-VL": (0.30, 0.90),
    "nvidia/Nemotron-3-Ultra-550b-a55b": (1.00, 3.00),
    "nvidia/Nemotron-3_5-Lightning": (0.06, 0.24),
}


class Meter:
    def __init__(self):
        self.calls: list[dict] = []

    def add(self, model: str, usage: dict | None, ms: int, step: str):
        usage = usage or {}
        pi, po = PRICES.get(model, (0, 0))
        cost = (usage.get("prompt_tokens", 0) * pi + usage.get("completion_tokens", 0) * po) / 1e6
        self.calls.append({"step": step, "model": model, "ms": ms,
                           "in": usage.get("prompt_tokens", 0), "out": usage.get("completion_tokens", 0),
                           "usd": round(cost, 5)})

    @property
    def total_usd(self) -> float:
        return round(sum(c["usd"] for c in self.calls), 4)

    def summary(self) -> dict:
        return {"calls": self.calls, "total_usd": self.total_usd}


class TokenFactory:
    def __init__(self, api_key: str | None = None, meter: Meter | None = None, timeout: float = 120):
        self.api_key = api_key or os.environ.get("NEBIUS_API_KEY")
        self.meter = meter or Meter()
        self.timeout = timeout

    @property
    def live(self) -> bool:
        return bool(self.api_key)

    # Token Factory docs show both {"json_schema": <schema>} and the OpenAI wrapper {"json_schema": {"name", "schema", "strict"}}.
    # We send the OpenAI form and fall back to the bare form once if the server rejects it (400/422).
    SCHEMA_FORMS = ("openai", "bare")

    def chat(self, model: str, messages: list[dict], *, step: str, schema: dict | None = None,
             tools: list[dict] | None = None, temperature: float = 0.0, max_tokens: int = 4096,
             reasoning_effort: str | None = None, _form: str = "openai") -> dict:
        """Returns the assistant message dict ({'content': ..., 'tool_calls': ...}). Raises RuntimeError on failure."""
        body: dict = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if schema:
            body["response_format"] = ({"type": "json_schema", "json_schema": {"name": "out", "schema": schema, "strict": True}}
                                       if _form == "openai" else {"type": "json_schema", "json_schema": schema})
        if tools:
            body["tools"] = tools
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        req = urllib.request.Request(f"{BASE_URL}/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        t0 = time.time()
        for attempt in range(3):  # 429/5xx: back off and retry
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.load(r)
                break
            except urllib.error.HTTPError as e:
                detail = e.read()[:400]
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if e.code in (400, 422) and schema and _form == "openai":
                    return self.chat(model, messages, step=step, schema=schema, tools=tools, temperature=temperature,
                                     max_tokens=max_tokens, reasoning_effort=reasoning_effort, _form="bare")
                if e.code in (400, 422) and reasoning_effort:  # parameter not supported by this model
                    return self.chat(model, messages, step=step, schema=schema, tools=tools, temperature=temperature,
                                     max_tokens=max_tokens, reasoning_effort=None, _form=_form)
                raise RuntimeError(f"{model} HTTP {e.code}: {detail!r}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise RuntimeError(f"{model} unreachable: {e}") from e
        self.meter.add(model, data.get("usage"), int((time.time() - t0) * 1000), step)
        return data["choices"][0]["message"]

    def chat_json(self, model: str, messages: list[dict], schema: dict, **kw) -> dict:
        msg = self.chat(model, messages, schema=schema, **kw)
        content = msg.get("content") or ""
        # Some reasoning models wrap JSON in text; take the outermost object.
        start, end = content.find("{"), content.rfind("}")
        return json.loads(content[start:end + 1] if start >= 0 else content)


def shrink(data: bytes, mime: str, max_side: int = 1600) -> tuple[bytes, str]:
    """Phone photos are 12 MP; the model reads labels fine at 1600 px and it cuts tokens and upload time.
    Also applies EXIF rotation so text is upright. Returns the input unchanged if Pillow is missing or fails."""
    try:
        import io

        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        if max(im.size) <= max_side and mime == "image/jpeg":
            return data, mime
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=88)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return data, mime


def image_part(data: bytes, mime: str = "image/jpeg") -> dict:
    import base64
    data, mime = shrink(data, mime)
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}
