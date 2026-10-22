"""Printed name ('Zocor 20mg') -> active ingredient(s).

Order: in-memory/KV cache -> RxNorm (free NIH API, no key) -> Tavily leaflet lookup (optional)
-> the printed name itself, marked unconfirmed. No model guesses an ingredient.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Callable

RXNAV = "https://rxnav.nlm.nih.gov/REST"
_STRIP = re.compile(r"\b(\d+(\.\d+)?\s*(mg|g|mcg|microgram|micrograms)|tablets?|capsules?|caps|tabs|oral|film[- ]coated|modified[- ]release|m/r|mr|sr|xl)\b", re.I)


def _get(url: str, timeout: float = 8.0) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "homecoming/0.1"}), timeout=timeout) as r:
        return json.load(r)


# British Approved Names that RxNorm (a US database) lists under the US name. Checked against the BNF
# "names of medicines" list; extend as needed. Both spellings are kept so the UI can show what was printed.
UK_TO_US = {
    "paracetamol": "acetaminophen", "adrenaline": "epinephrine", "noradrenaline": "norepinephrine",
    "salbutamol": "albuterol", "glyceryl trinitrate": "nitroglycerin", "frusemide": "furosemide",
    "lignocaine": "lidocaine", "amoxycillin": "amoxicillin", "beclometasone": "beclomethasone",
    "hyoscine": "scopolamine", "co-codamol": "acetaminophen + codeine", "colecalciferol": "cholecalciferol",
    "ciclosporin": "cyclosporine", "mesalazine": "mesalamine", "levothyroxine": "levothyroxine",
}


def rxnorm_ingredients(printed: str) -> list[str]:
    """Strict: every word of the printed name (4+ letters) must appear in the RxNorm concept we matched,
    otherwise return []. approximateTerm alone maps 'Co-codamol' to 'carbon monoxide'; that must never pass."""
    term = _STRIP.sub(" ", printed).strip() or printed
    for uk, us in UK_TO_US.items():
        if term.lower().strip() == uk:
            return [us]
    words = [w for w in re.findall(r"[a-z]+", term.lower()) if len(w) >= 4]
    approx = _get(f"{RXNAV}/approximateTerm.json?term={urllib.parse.quote(term)}&maxEntries=3")
    for cand in (approx.get("approximateGroup") or {}).get("candidate") or []:
        rxcui = cand["rxcui"]
        props = _get(f"{RXNAV}/rxcui/{rxcui}/properties.json").get("properties") or {}
        rel = _get(f"{RXNAV}/rxcui/{rxcui}/related.json?tty=IN+MIN+BN")
        groups = (rel.get("relatedGroup") or {}).get("conceptGroup") or []
        names = [c["name"].lower() for g in groups for c in g.get("conceptProperties", [])] + [(props.get("name") or "").lower()]
        haystack = " ".join(names)
        if words and not all(w in haystack for w in words):
            continue
        pick = lambda tty: [c["name"].lower() for g in groups if g.get("tty") == tty for c in g.get("conceptProperties", [])]
        found = pick("IN") or pick("MIN")
        if found:
            return found
    return []


def tavily_ingredients(printed: str, api_key: str) -> tuple[list[str], str | None]:
    """Search official leaflet sites for the product and read the active ingredient line. Returns (ingredients, source_url)."""
    body = json.dumps({
        "query": f"{printed} active ingredient patient information leaflet",
        "include_domains": ["medicines.org.uk", "nhs.uk", "dailymed.nlm.nih.gov"],
        "max_results": 3,
        "include_raw_content": False,
    }).encode()
    req = urllib.request.Request("https://api.tavily.com/search", data=body,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=12) as r:
        res = json.load(r)
    for hit in res.get("results", []):
        text = (hit.get("content") or "")
        m = re.search(r"(?:active (?:substance|ingredient)s?|contains)\s*(?:is|are|:)?\s*([a-z][a-z\- ]{2,40})", text, re.I)
        if m:
            return [m.group(1).strip().lower()], hit.get("url")
    return [], None


class Normaliser:
    """cache is any mapping-like object with get/__setitem__ (dict locally, modal.Dict in the cloud)."""

    def __init__(self, cache=None, tavily_key: str | None = None, rxnorm: Callable = rxnorm_ingredients):
        self.cache = cache if cache is not None else {}
        self.tavily_key = tavily_key or os.environ.get("TAVILY_API_KEY")
        self.rxnorm = rxnorm

    def resolve(self, printed: str) -> dict:
        """-> {'ingredient': str, 'confirmed': bool, 'source': 'rxnorm'|'tavily'|'printed', 'url': str|None}"""
        key = "ing:" + printed.strip().lower()
        hit = self.cache.get(key)
        if hit:
            return hit
        result = {"ingredient": printed.strip().lower(), "confirmed": False, "source": "printed", "url": None}
        try:
            ings = self.rxnorm(printed)
            if ings:
                result = {"ingredient": " + ".join(sorted(ings)), "confirmed": True, "source": "rxnorm",
                          "url": f"https://mor.nlm.nih.gov/RxNav/search?searchBy=String&searchTerm={urllib.parse.quote(printed)}"}
        except Exception:
            pass
        if not result["confirmed"] and self.tavily_key:
            try:
                ings, url = tavily_ingredients(printed, self.tavily_key)
                if ings:
                    result = {"ingredient": ings[0], "confirmed": True, "source": "tavily", "url": url}
            except Exception:
                pass
        if result["confirmed"]:
            self.cache[key] = result
        return result
