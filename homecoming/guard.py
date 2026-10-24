"""No-advice guard: a deterministic blocklist first, then Nemotron 3.5 Lightning rewrites anything that slips through.

Describing a letter ("the letter says stop amlodipine") is allowed. Telling the carer what to do
("stop giving amlodipine") is not. The regex catches imperative advice; the model catches the rest.
"""
from __future__ import annotations

import re

from .llm import MODELS, TokenFactory
from .prompts import GUARD_SCHEMA, GUARD_SYSTEM

# Imperative advice patterns. Lines that are questions ("Should he stop...?") are exempt.
_ADVICE = re.compile(
    r"\b(you|he|she|they|your (?:father|mother|dad|mum|parent)|dad|mum)\s+(should|must|need to|needs to|can|could|may)\s+"
    r"(?:now\s+|also\s+)?(stop|start|take|skip|hold|withhold|double|halve|increase|reduce|lower|raise|continue|restart|swap|switch|use)\b"
    r"|^(?:please\s+)?(stop|start|take|skip|give|withhold|double|halve|increase|reduce|discontinue|resume|restart|keep|carry on|"
    r"discard|throw|bin|dispose|remove|swap|switch|replace|refill|crush|split|cut)\b"
    r"|\b(keep|carry on|continue)\s+(taking|giving|using)\b"
    r"|\b(throw|bin|discard|dispose of)\b.{0,30}\b(box|boxes|tablets?|capsules?|pills?|medicine|it|them)\b"
    r"|\byou\s+(can|could|may)\s+(still\s+)?(use|give|take|keep)\b"
    r"|\bjust\s+(take|give|use)\b"
    r"|\b(do not|don't|never)\s+(take|give|stop|skip|mix|combine)\b"
    r"|\b(is|are|it's|it is)\s+(safe|unsafe|dangerous|fine|ok|okay)\s+to\b"
    r"|\b(interact|interaction|overdose|contraindicat)",
    re.I,
)


def violates(text: str) -> bool:
    t = text.strip()
    if t.endswith("?"):
        return False
    return bool(_ADVICE.search(t))


def to_question(text: str) -> str:
    """Deterministic fallback rewrite: wrap the concern as a pharmacist question."""
    t = text.strip().rstrip(".")
    return f"Question for the pharmacist: {t[0].lower() + t[1:] if t else t}. Is that right?"


def guard(text: str, tf: TokenFactory | None = None, step: str = "guard") -> tuple[str, bool]:
    """-> (safe_text, was_rewritten)."""
    if not text.strip():
        return text, False
    if violates(text):
        return to_question(text), True
    if tf is None or not tf.live:
        return text, False
    try:
        out = tf.chat_json(MODELS["fast"], [{"role": "system", "content": GUARD_SYSTEM},
                                            {"role": "user", "content": text}], GUARD_SCHEMA, step=step, max_tokens=400)
    except Exception:
        return text, False
    if out.get("ok", True):
        return text, False
    rewritten = out.get("rewritten") or to_question(text)
    # the model's rewrite must itself pass the regex, else fall back to the deterministic form
    return (rewritten if not violates(rewritten) else to_question(text)), True
