"""Deterministic reconciliation: hospital letter vs boxes in the house vs pill organiser.

No model does matching or arithmetic here. Nemotron reads the photos before this step and
phrases pharmacist questions after it; everything in between is plain, testable code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

LETTER_ACTIONS = ("new", "stopped", "changed", "continued", "withheld")
LOCATIONS = ("house", "organiser")


@dataclass
class LetterItem:
    name: str                 # as printed on the letter
    ingredient: str           # normalised active ingredient, lower case
    action: str               # one of LETTER_ACTIONS
    strength: str | None = None
    id: str = ""


@dataclass
class HouseItem:
    id: str                   # stable id, used to outline the item on the photo
    name: str                 # as printed on the box / written on the slot
    ingredient: str
    location: str             # "house" or "organiser"
    strength: str | None = None
    slot: str | None = None   # organiser compartment, e.g. "Mon AM"


@dataclass
class Discrepancy:
    type: str
    ingredient: str
    letter: list[LetterItem] = field(default_factory=list)
    items: list[HouseItem] = field(default_factory=list)


_STRENGTH = re.compile(r"^([\d.]+)\s*(mg|g|mcg|µg|ug|microgram|micrograms|milligram|milligrams)\b")


def strength_key(s: str | None) -> str | None:
    """'5mg', '5 MG', '0.005 g', '5000 microgram' all become '5mg'. Unknown units compare as text."""
    if not s:
        return None
    m = _STRENGTH.match(s.strip().lower())
    if not m:
        return s.strip().lower()
    n = float(m.group(1))
    unit = m.group(2)
    mg = n * 1000 if unit == "g" else n if unit.startswith("m") and unit != "mcg" and not unit.startswith("micro") else n / 1000
    return f"{mg:g}mg"


def _n(s: str) -> str:
    return s.strip().lower()


def reconcile(letter: list[LetterItem], house: list[HouseItem]) -> list[Discrepancy]:
    out: list[Discrepancy] = []
    by_ing = lambda ing: [h for h in house if _n(h.ingredient) == ing]
    letter_ings = {_n(l.ingredient) for l in letter}

    # The letter is checked against itself first: one drug both stopped and kept.
    contradicted: set[str] = set()
    for ing in sorted(letter_ings):
        rows = [l for l in letter if _n(l.ingredient) == ing]
        stops = any(r.action == "stopped" for r in rows)
        keeps = any(r.action in ("new", "continued", "changed") for r in rows)
        if stops and keeps:
            out.append(Discrepancy("LETTER_CONTRADICTION", ing, rows, by_ing(ing)))
            contradicted.add(ing)

    for l in letter:
        ing = _n(l.ingredient)
        if ing in contradicted:
            continue  # the pharmacist must settle the letter before anything else
        present = by_ing(ing)
        if l.action == "stopped":
            in_house = [h for h in present if h.location == "house"]
            in_org = [h for h in present if h.location == "organiser"]
            if in_house:
                out.append(Discrepancy("STOPPED_BUT_PRESENT", ing, [l], in_house))
            if in_org:
                out.append(Discrepancy("ORGANISER_CONTAINS_STOPPED", ing, [l], in_org))
        if l.action == "changed" and l.strength:
            old = [h for h in present if h.strength and strength_key(h.strength) != strength_key(l.strength)]
            if old:
                out.append(Discrepancy("DOSE_CHANGED_BUT_OLD_STRENGTH_PRESENT", ing, [l], old))
        if l.action == "new" and not any(h.location == "house" for h in present):
            out.append(Discrepancy("NEW_BUT_NOT_FOUND", ing, [l], []))

    # Same active ingredient under two different printed names among the boxes.
    boxes = [h for h in house if h.location == "house"]
    for ing in sorted({_n(h.ingredient) for h in boxes}):
        same = [h for h in boxes if _n(h.ingredient) == ing]
        if len({_n(h.name) for h in same}) > 1:
            out.append(Discrepancy("SAME_INGREDIENT_TWO_BRANDS", ing,
                                   [l for l in letter if _n(l.ingredient) == ing], same))

    # Anything in the house the letter never mentions.
    for h in house:
        if _n(h.ingredient) not in letter_ings:
            out.append(Discrepancy("UNMATCHED_ITEM", _n(h.ingredient), [], [h]))
    return out


# Plain-English headlines. The pharmacist QUESTION for each card is written by Nemotron 3 Ultra
# from these facts, then checked by the guard; these strings are the fallback when no model runs.
HEADLINES = {
    "LETTER_CONTRADICTION": "The letter lists {ing} as both stopped and continued",
    "STOPPED_BUT_PRESENT": "Letter says STOP {ing}, but a box of it is in the house",
    "ORGANISER_CONTAINS_STOPPED": "Letter says STOP {ing}, but it is still in the pill organiser",
    "DOSE_CHANGED_BUT_OLD_STRENGTH_PRESENT": "Letter changes the {ing} dose, but a box of the old strength is in the house",
    "NEW_BUT_NOT_FOUND": "Letter adds {ing}, but no box of it was found",
    "SAME_INGREDIENT_TWO_BRANDS": "Two boxes with different names both contain {ing}",
    "UNMATCHED_ITEM": "{ing} is in the house but the letter does not mention it",
}

FALLBACK_QUESTIONS = {
    "LETTER_CONTRADICTION": "The discharge letter lists {ing} under both 'stopped' and 'continue'. Which is correct?",
    "STOPPED_BUT_PRESENT": "The letter stops {ing}. Should the box at home be set aside or returned to you?",
    "ORGANISER_CONTAINS_STOPPED": "The letter stops {ing}, but it is already in this week's organiser. Should the organiser be refilled?",
    "DOSE_CHANGED_BUT_OLD_STRENGTH_PRESENT": "The letter changes the {ing} dose to {strength}, but the box at home is {old}. Can the old box still be used, and how?",
    "NEW_BUT_NOT_FOUND": "The letter adds {ing}, but we could not find it. Was it dispensed, or should we collect it?",
    "SAME_INGREDIENT_TWO_BRANDS": "Two boxes with different names both contain {ing}. Are these the same medicine, and which one should be used?",
    "UNMATCHED_ITEM": "{ing} is in the house but not on the discharge letter. Should it continue?",
}


def label(name: str, strength: str | None) -> str:
    """'Amlodipine 5 mg' + '5 mg' -> 'Amlodipine 5 mg' (no doubled strength)."""
    if not strength:
        return name
    squash = lambda s: re.sub(r"\s+", "", s.lower())
    return name if squash(strength) in squash(name) else f"{name} {strength}"


def _subject(d: Discrepancy) -> str:
    """What to call the medicine in prose: the printed name for an item the letter never mentions
    (a UK carer reads 'Paracetamol', not 'acetaminophen'), the shared ingredient otherwise."""
    if d.type == "UNMATCHED_ITEM" and d.items:
        return d.items[0].name.split(" ")[0]
    return d.ingredient


def headline(d: Discrepancy) -> str:
    s = HEADLINES[d.type].format(ing=_subject(d))
    return s[0].upper() + s[1:]


def fallback_question(d: Discrepancy) -> str:
    strength = d.letter[0].strength if d.letter and d.letter[0].strength else "the new strength"
    old = ", ".join(sorted({h.strength or "?" for h in d.items})) or "a different strength"
    s = FALLBACK_QUESTIONS[d.type].format(ing=_subject(d), strength=strength, old=old)
    return s[0].upper() + s[1:]
