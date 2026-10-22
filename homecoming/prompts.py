"""Prompts and JSON schemas for the three Nemotron steps."""

# ---------- 1. Extraction: Nemotron 3.5 Super VL ----------

EXTRACT_ITEM = {
    "type": "object",
    "properties": {
        "name_as_printed": {"type": "string", "description": "the main product name as printed (brand or generic)"},
        "generic_as_printed": {"type": "string", "description": "the active-ingredient name if it is printed anywhere on the item (often small print under the brand); empty if not printed"},
        "strength": {"type": "string", "description": "exactly as printed, e.g. '2.5 mg'; empty if unreadable"},
        "form": {"type": "string", "description": "tablets, capsules, liquid, inhaler... or empty"},
        "instructions": {"type": "string", "description": "dose instructions as printed, or empty"},
        "action": {"type": "string", "enum": ["new", "stopped", "changed", "continued", "withheld", "none"],
                   "description": "letter only: what the letter says about this medicine; 'none' for photos of boxes"},
        "cell": {"type": "integer", "minimum": 0, "maximum": 9,
                 "description": "which cell of a 3x3 grid over the photo (1 top-left .. 9 bottom-right) the item sits in; 0 if unknown"},
        "slot": {"type": "string", "description": "pill organiser compartment label if any, e.g. 'Mon AM'; else empty"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["name_as_printed", "generic_as_printed", "strength", "form", "instructions", "action", "cell", "slot", "confidence"],
    "additionalProperties": False,
}
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": EXTRACT_ITEM},
                   "notes": {"type": "string", "description": "anything unreadable or ambiguous, briefly"}},
    "required": ["items", "notes"],
    "additionalProperties": False,
}

EXTRACT_PROMPTS = {
    "letter": (
        "This is a hospital discharge letter or medication list. List EVERY medicine it mentions. "
        "For each, copy the name and strength exactly as printed, and set `action` to what the letter says: "
        "new (started in hospital), stopped, changed (dose or frequency changed), continued (no change), "
        "withheld (paused pending review). If a medicine appears in two sections with different actions, "
        "output it twice, once per action. Do not infer or guess anything that is not printed. Set cell to 0."
    ),
    "boxes": (
        "This photo shows medicine boxes, bottles or blister packs on a surface. List EVERY item you can read. "
        "Copy the product name and strength exactly as printed. Set `action` to 'none'. "
        "Imagine a 3x3 grid over the photo and set `cell` to the cell containing most of the item "
        "(1 = top-left, 3 = top-right, 9 = bottom-right). If text is unreadable, leave strength empty "
        "and confidence low rather than guessing."
    ),
    "organiser": (
        "This is a weekly pill organiser with the lid open. For each compartment that contains pills, "
        "report the compartment label (e.g. 'Mon AM') in `slot` and, if the pills or a label are legible, "
        "the medicine name and strength. Set `action` to 'none'. Use `cell` for the compartment's position "
        "on a 3x3 grid. If the pills cannot be identified, output the slot with an empty name and low confidence."
    ),
}

# ---------- 2. Interpretation: Nemotron 3 Ultra ----------

INTERPRET_SCHEMA = {
    "type": "object",
    "properties": {
        "letter_audit": {
            "type": "array",
            "description": "problems inside the letter itself: contradictions, ambiguous wording, missing strengths",
            "items": {"type": "object",
                      "properties": {"issue": {"type": "string"}, "medicines": {"type": "array", "items": {"type": "string"}},
                                     "question": {"type": "string"}},
                      "required": ["issue", "medicines", "question"], "additionalProperties": False},
        },
        "cards": {
            "type": "array",
            "items": {"type": "object",
                      "properties": {
                          "index": {"type": "integer", "description": "index of the discrepancy this card explains"},
                          "headline": {"type": "string", "description": "one short factual line, no advice"},
                          "detail": {"type": "string", "description": "what the letter says vs what was found, citing sources by id"},
                          "question": {"type": "string", "description": "ONE question for the pharmacist, phrased as a question"},
                          "severity": {"type": "string", "enum": ["check-today", "ask-when-convenient", "info"]},
                      },
                      "required": ["index", "headline", "detail", "question", "severity"], "additionalProperties": False},
        },
    },
    "required": ["letter_audit", "cards"],
    "additionalProperties": False,
}

INTERPRET_SYSTEM = (
    "You help a family carer prepare questions for a community pharmacist after a hospital discharge. "
    "You are given (a) the medicines the discharge letter lists with their actions, (b) what was found in the "
    "house and the pill organiser, (c) a list of discrepancies computed by code. Your job: explain each "
    "discrepancy plainly and turn it into ONE question for the pharmacist, and audit the letter for internal "
    "contradictions or ambiguous wording ('as before', 'reduce', missing strengths). "
    "HARD RULES: never tell anyone to start, stop, take, skip, change, split or double a medicine or dose. "
    "Never diagnose or assess interactions. Every actionable sentence must be a question addressed to the "
    "pharmacist. Cite sources by their ids (e.g. L2, B4, O1). Be brief and concrete. Do not add advice, "
    "reassurance or disclaimers."
)

# ---------- 3. Guard: Nemotron 3.5 Lightning ----------

GUARD_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean", "description": "true if the text contains NO instruction to start, stop, take, skip or change a medicine or dose"},
        "rewritten": {"type": "string", "description": "if not ok: the same content rephrased as a question for the pharmacist; else empty"},
    },
    "required": ["ok", "rewritten"],
    "additionalProperties": False,
}

GUARD_SYSTEM = (
    "You are a policy checker for text shown to a family carer. The text must NOT instruct anyone to start, "
    "stop, take, skip, hold, split, double, increase or reduce any medicine or dose, and must not give medical "
    "advice or interaction warnings. Describing what a letter says or what was found is fine. If the text breaks "
    "the policy, rewrite it so the same concern becomes a question for the pharmacist, keeping all facts."
)

ASK_SYSTEM = (
    "You are the question desk of a medicines-reconciliation app for family carers. The user may ask anything. "
    "You must not give medical advice, dosing instructions or interaction assessments. Instead, turn what they "
    "asked into one or two precise questions they can put to their pharmacist, using the facts in the context "
    "(the discharge letter, the boxes found, the discrepancies). If the question is not about medicines, answer "
    "briefly. Keep it under 80 words."
)
