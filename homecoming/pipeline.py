"""photos -> extraction (VL) -> normalisation (code) -> diff (code) -> interpretation (Ultra) -> guard (Lightning) -> sheet.

When no NEBIUS_API_KEY is present the model steps are replaced by recorded fixtures so the whole
product still runs end to end ("recorded mode"); the response says so.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .diff import Discrepancy, HouseItem, LetterItem, fallback_question, headline, label, reconcile
from .guard import guard
from .llm import MODELS, Meter, TokenFactory, image_part
from .normalise import Normaliser, base_name
from .prompts import EXTRACT_PROMPTS, EXTRACT_SCHEMA, INTERPRET_SCHEMA, INTERPRET_SYSTEM, ASK_SYSTEM

DATA = Path(__file__).resolve().parent.parent / "data"
GROUPS = ("letter", "boxes", "organiser")
ORDER = {"check-today": 0, "ask-when-convenient": 1, "info": 2}
SEVERITY = {"LETTER_CONTRADICTION": "check-today", "STOPPED_BUT_PRESENT": "check-today",
            "ORGANISER_CONTAINS_STOPPED": "check-today", "DOSE_CHANGED_BUT_OLD_STRENGTH_PRESENT": "check-today",
            "NEW_BUT_NOT_FOUND": "ask-when-convenient", "SAME_INGREDIENT_TWO_BRANDS": "ask-when-convenient",
            "UNMATCHED_ITEM": "info"}


def load_kitchens() -> list[dict]:
    return json.loads((DATA / "kitchens.json").read_text())["kitchens"]


# ---------- step 1: extraction ----------

def extract(tf: TokenFactory, group: str, images: list[tuple[bytes, str]]) -> list[dict]:
    """One VL call per photo. Returns rows with ids L1.., B1.., O1.. and the photo index they came from."""
    prefix = {"letter": "L", "boxes": "B", "organiser": "O"}[group]
    rows: list[dict] = []
    for photo_idx, (data, mime) in enumerate(images):
        out = tf.chat_json(MODELS["vl"], [{"role": "user", "content": [
            {"type": "text", "text": EXTRACT_PROMPTS[group]}, image_part(data, mime)]}],
            EXTRACT_SCHEMA, step=f"extract:{group}", max_tokens=2048)
        for it in out.get("items", []):
            if not it.get("name_as_printed") and not it.get("slot"):
                continue
            rows.append({**it, "id": f"{prefix}{len(rows) + 1}", "photo": photo_idx, "group": group})
    return rows


def recorded_extraction(kitchen: dict) -> dict[str, list[dict]]:
    """Fixture rows in the same shape extract() returns, from data/kitchens.json."""
    rows = {g: [] for g in GROUPS}
    for i, l in enumerate(kitchen["letter"], 1):
        rows["letter"].append({"id": f"L{i}", "name_as_printed": l["name"], "generic_as_printed": l["ingredient"],
                               "strength": l.get("strength", ""), "form": "", "instructions": l.get("dose", ""),
                               "action": l["action"], "cell": 0, "slot": "", "confidence": "high", "photo": 0,
                               "group": "letter", "_ingredient": l["ingredient"]})
    b = o = 0
    for h in kitchen["house"]:
        if h["location"] == "house":
            b += 1
            rows["boxes"].append({"id": f"B{b}", "name_as_printed": h["name"], "generic_as_printed": h.get("generic_printed", ""),
                                  "strength": h.get("strength", ""), "form": "", "instructions": "", "action": "none",
                                  "cell": h.get("cell", 0), "slot": "", "confidence": "high", "photo": 0, "group": "boxes",
                                  "_ingredient": h["ingredient"]})
        else:
            o += 1
            rows["organiser"].append({"id": f"O{o}", "name_as_printed": h["name"], "generic_as_printed": "",
                                      "strength": h.get("strength", ""), "form": "", "instructions": "", "action": "none",
                                      "cell": h.get("cell", 0), "slot": h.get("slot", h["name"]), "confidence": "high",
                                      "photo": 0, "group": "organiser", "_ingredient": h["ingredient"]})
    return rows


# ---------- step 2 + 3: normalise and diff ----------

def _resolve(r: dict, norm: Normaliser) -> dict:
    """Fixture rows carry their ingredient. Otherwise try the printed generic name first (small print on
    most boxes), then the main product name; an unconfirmed result keeps the printed name, flagged."""
    if "_ingredient" in r:
        return {"ingredient": r["_ingredient"], "confirmed": True, "source": "fixture", "url": None}
    generic = (r.get("generic_as_printed") or "").strip()
    if generic:
        res = norm.resolve(generic)
        if res["confirmed"]:
            return res
    res = norm.resolve(r["name_as_printed"])
    if not res["confirmed"]:
        # nothing confirmed it: fall back to the printed generic (else the product name) with salts and forms removed
        return {"ingredient": base_name(generic or r["name_as_printed"]), "confirmed": False,
                "source": "printed-generic" if generic else "printed", "url": None}
    return res


def to_items(rows: dict[str, list[dict]], norm: Normaliser) -> tuple[list[LetterItem], list[HouseItem], dict]:
    sources: dict[str, dict] = {}
    letter, house = [], []
    for r in rows["letter"]:
        res = _resolve(r, norm)
        sources[r["id"]] = res
        if r.get("action") in ("new", "stopped", "changed", "continued", "withheld"):
            letter.append(LetterItem(r["name_as_printed"], res["ingredient"], r["action"], r.get("strength") or None, r["id"]))
    for g, loc in (("boxes", "house"), ("organiser", "organiser")):
        for r in rows[g]:
            if not r.get("name_as_printed"):
                continue  # organiser slot with unidentifiable pills: shown in the UI, not matched
            res = _resolve(r, norm)
            sources[r["id"]] = res
            house.append(HouseItem(r["id"], r["name_as_printed"], res["ingredient"], loc, r.get("strength") or None, r.get("slot") or None))
    return letter, house, sources


# ---------- step 4: interpretation ----------

def interpret(tf: TokenFactory, letter: list[LetterItem], house: list[HouseItem], diffs: list[Discrepancy]) -> dict:
    ctx = {
        "letter": [{"id": l.id, "name": l.name, "ingredient": l.ingredient, "strength": l.strength, "action": l.action} for l in letter],
        "found": [{"id": h.id, "name": h.name, "ingredient": h.ingredient, "strength": h.strength, "where": h.location, "slot": h.slot} for h in house],
        "discrepancies": [{"index": i, "type": d.type, "ingredient": d.ingredient,
                           "letter_ids": [l.id for l in d.letter], "found_ids": [h.id for h in d.items]} for i, d in enumerate(diffs)],
    }
    return tf.chat_json(MODELS["reason"], [{"role": "system", "content": INTERPRET_SYSTEM},
                                           {"role": "user", "content": json.dumps(ctx)}],
                        INTERPRET_SCHEMA, step="interpret", max_tokens=3000, reasoning_effort="medium")


def recorded_interpretation(diffs: list[Discrepancy]) -> dict:
    cards = [{"index": i, "headline": headline(d), "detail": _detail(d), "question": fallback_question(d),
              "severity": SEVERITY[d.type]} for i, d in enumerate(diffs)]
    audit = [{"issue": f"{d.ingredient} appears under both 'stopped' and 'continue'",
              "medicines": [l.id for l in d.letter], "question": fallback_question(d)}
             for d in diffs if d.type == "LETTER_CONTRADICTION"]
    return {"letter_audit": audit, "cards": cards}


def _detail(d: Discrepancy) -> str:
    says = "; ".join(f"{l.id}: {label(l.name, l.strength)} ({l.action})" for l in d.letter) or "not on the letter"
    found = "; ".join(f"{h.id}: {label(h.name, h.strength)}" + (f" in {h.slot}" if h.slot else "") for h in d.items) or "not found"
    return f"Letter: {says}. Found: {found}."


# ---------- the whole run ----------

def _noop(*_a, **_k):
    pass


def run(images: dict[str, list[tuple[bytes, str]]] | None = None, *, kitchen: dict | None = None,
        tf: TokenFactory | None = None, norm: Normaliser | None = None, progress=_noop) -> dict:
    """Either real photos (images) or a recorded kitchen fixture. Returns the full result for the UI.
    progress(step, status, info) is called as each step starts and ends (used for the live progress rail)."""
    tf = tf or TokenFactory(meter=Meter())
    norm = norm or Normaliser()
    live = tf.live and images is not None
    if live:
        def one(g):  # the three photo groups are read in parallel
            if not images.get(g):
                progress(f"extract:{g}", "skipped", {})
                return g, []
            t0 = time.time()
            progress(f"extract:{g}", "running", {"model": MODELS["vl"], "photos": len(images[g])})
            out = extract(tf, g, images[g])
            progress(f"extract:{g}", "done", {"items": len(out), "ms": int((time.time() - t0) * 1000)})
            return g, out
        with ThreadPoolExecutor(3) as ex:
            rows = dict(ex.map(one, GROUPS))
    else:
        rows = recorded_extraction(kitchen or load_kitchens()[0])
        for g in GROUPS:
            progress(f"extract:{g}", "done", {"items": len(rows[g]), "recorded": True})
    return finish(rows, tf, norm, recorded=not live, progress=progress)


def finish(rows: dict[str, list[dict]], tf: TokenFactory, norm: Normaliser, *, recorded: bool, progress=_noop) -> dict:
    """Everything after extraction. Also used when the carer edits a row and re-runs."""
    t0 = time.time()
    progress("normalise", "running", {})
    letter, house, sources = to_items(rows, norm)
    progress("normalise", "done", {"confirmed": sum(1 for s in sources.values() if s.get("confirmed")), "total": len(sources),
                                   "ms": int((time.time() - t0) * 1000)})
    t0 = time.time()
    diffs = reconcile(letter, house)
    progress("compare", "done", {"discrepancies": len(diffs), "ms": int((time.time() - t0) * 1000)})
    t0 = time.time()
    progress("interpret", "running", {"model": MODELS["reason"]} if tf.live else {"recorded": True})
    interp = interpret(tf, letter, house, diffs) if tf.live else recorded_interpretation(diffs)
    progress("interpret", "done", {"cards": len(interp.get("cards", [])), "audit": len(interp.get("letter_audit", [])),
                                   "ms": int((time.time() - t0) * 1000), **({} if tf.live else {"recorded": True})})
    by_index = {c["index"]: c for c in interp.get("cards", []) if isinstance(c, dict) and isinstance(c.get("index"), int)}
    drafts = []
    for i, d in enumerate(diffs):
        c = by_index.get(i) or {}
        drafts.append({"headline": c.get("headline") or headline(d), "detail": c.get("detail") or _detail(d),
                       "question": c.get("question") or fallback_question(d),
                       "severity": c.get("severity") if c.get("severity") in ORDER else SEVERITY[d.type]})
    t0 = time.time()
    progress("guard", "running", {"model": MODELS["fast"], "sentences": 2 * len(drafts)} if tf.live else {"recorded": True})
    texts = [x for c in drafts for x in (c["question"], c["headline"])] + [a.get("question", "") for a in interp.get("letter_audit", [])]
    with ThreadPoolExecutor(8) as ex:  # every sentence is checked in parallel
        guarded = list(ex.map(lambda t: guard(t, tf, step="guard"), texts))
    progress("guard", "done", {"rewritten": sum(1 for _, r in guarded if r), "ms": int((time.time() - t0) * 1000),
                               **({} if tf.live else {"recorded": True})})
    cards = []
    for i, (d, c) in enumerate(zip(diffs, drafts)):
        (q, rewritten), (h, _) = guarded[2 * i], guarded[2 * i + 1]
        cards.append({"type": d.type, "ingredient": d.ingredient, "severity": c["severity"],
                      "headline": h, "detail": c["detail"], "question": q, "guard_rewrote": rewritten,
                      "letter_ids": [l.id for l in d.letter], "found_ids": [x.id for x in d.items],
                      "sources": {x.id: sources.get(x.id) for x in d.items if sources.get(x.id)}})
    audit = [{**a, "question": guarded[2 * len(drafts) + j][0]} for j, a in enumerate(interp.get("letter_audit", []))]
    cards.sort(key=lambda c: ORDER.get(c["severity"], 3))
    return {
        "recorded": recorded,
        "rows": {g: [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows[g]] for g in GROUPS},
        "sources": sources,
        "cards": cards,
        "letter_audit": audit,
        "columns": {
            "hospital": [{"id": l.id, "name": l.name, "strength": l.strength, "action": l.action} for l in letter],
            "house": [{"id": h.id, "name": h.name, "strength": h.strength} for h in house if h.location == "house"],
            "organiser": [{"id": h.id, "name": h.name, "strength": h.strength, "slot": h.slot} for h in house if h.location == "organiser"],
        },
        "models": MODELS,
        "cost": tf.meter.summary(),
    }


def ask(tf: TokenFactory, question: str, context: dict) -> dict:
    """Free-text box: Lightning may only answer by reframing as pharmacist questions."""
    if not tf.live:
        text = f"I can't advise on that. A question you could put to the pharmacist: \"{question.strip().rstrip('?')}?\""
        return {"answer": text, "recorded": True}
    ctx = {"cards": [{"headline": c["headline"], "question": c["question"]} for c in context.get("cards", [])],
           "hospital": context.get("columns", {}).get("hospital", []), "house": context.get("columns", {}).get("house", [])}
    msg = tf.chat(MODELS["fast"], [{"role": "system", "content": ASK_SYSTEM},
                                   {"role": "user", "content": f"Context: {json.dumps(ctx)}\n\nCarer asks: {question}"}],
                  step="ask", max_tokens=300)
    text, _ = guard(msg.get("content") or "", tf, step="guard")
    return {"answer": text, "recorded": False, "cost": tf.meter.summary()}
