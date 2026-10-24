"""Run the Homecoming benchmark and report what judges should see: reading accuracy and catch rate.

    NEBIUS_API_KEY=... python scripts/eval.py            # live: Super VL reads every photo, full pipeline
    python scripts/eval.py --oracle                      # no key: ground-truth rows through the engine (sanity, must be 100%)

Metrics, per photo-quality bucket (clean / mild / harsh) and overall:
  reading  = share of ground-truth items read with the right name and strength (letter + boxes)
  action   = share of letter items whose stopped/changed/new/continued action was read correctly
  catch    = recall of planted discrepancies (type + ingredient)
  false    = discrepancies reported that were not planted (per kitchen)
  advice   = user-facing sentences that the guard had to rewrite, and any that slipped through (must be 0)
Writes data/eval/results.json and data/eval/RESULTS.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from homecoming import pipeline  # noqa: E402
from homecoming.diff import strength_key  # noqa: E402
from homecoming.guard import violates  # noqa: E402
from homecoming.llm import Meter, TokenFactory  # noqa: E402
from homecoming.normalise import Normaliser, base_name  # noqa: E402

EVAL = Path(__file__).resolve().parent.parent / "data" / "eval"


def same_item(read: dict, name: str, strength: str) -> bool:
    got = base_name(read.get("name_as_printed", "") + " " + read.get("generic_as_printed", ""))
    return base_name(name) in got and strength_key(read.get("strength")) == strength_key(strength)


def run_one(k: dict, i: int, oracle: bool, norm: Normaliser) -> dict:
    tf = TokenFactory(meter=Meter()) if not oracle else TokenFactory(api_key=None)
    t0 = time.time()
    if oracle:
        res = pipeline.run(kitchen=k, tf=tf, norm=norm)
    else:
        imgs = {g: [((EVAL / f"k{i:02d}-{g}.jpg").read_bytes(), "image/jpeg")] for g in pipeline.GROUPS}
        res = pipeline.run(imgs, tf=tf, norm=norm)
    secs = time.time() - t0
    rows = res["rows"]
    want_letter = k["letter"]
    want_boxes = [h for h in k["house"] if h["location"] == "house"]
    read_ok = sum(any(same_item(r, l["name"].rsplit(" ", 2)[0], l["strength"]) for r in rows["letter"]) for l in want_letter)
    read_ok += sum(any(same_item(r, h["brand"], h["strength"]) for r in rows["boxes"]) for h in want_boxes)
    act_ok = sum(any(same_item(r, l["name"].rsplit(" ", 2)[0], l["strength"]) and r.get("action") == l["action"] for r in rows["letter"])
                 for l in want_letter)
    planted = {(e, x) for e in k["expected"] for x in _ingredients_for(k, e)}
    found = {(c["type"], c["ingredient"]) for c in res["cards"]}
    texts = [c["question"] for c in res["cards"]] + [c["headline"] for c in res["cards"]] + [a["question"] for a in res["letter_audit"]]
    return {
        "kitchen": i, "defects": k["defects"], "seconds": round(secs, 1), "usd": res["cost"]["total_usd"],
        "read": read_ok, "read_total": len(want_letter) + len(want_boxes),
        "action": act_ok, "action_total": len(want_letter),
        "caught": len(planted & found), "planted": len(planted), "false": len(found - planted),
        "missed": sorted(f"{t}:{x}" for t, x in planted - found), "extra": sorted(f"{t}:{x}" for t, x in found - planted),
        "guard_rewrites": sum(c["guard_rewrote"] for c in res["cards"]), "advice_slipped": sum(violates(t) for t in texts),
    }


def _ingredients_for(k: dict, typ: str) -> list[str]:
    """Which ingredient each planted discrepancy is about, from the scenario construction in make_eval.py."""
    L, H = k["letter"], k["house"]
    if typ in ("STOPPED_BUT_PRESENT", "ORGANISER_CONTAINS_STOPPED"):
        stopped = {l["ingredient"] for l in L if l["action"] == "stopped"}
        kept = {l["ingredient"] for l in L if l["action"] != "stopped"}
        loc = "house" if typ == "STOPPED_BUT_PRESENT" else "organiser"
        return sorted(x for x in stopped - kept if any(h["ingredient"] == x and h["location"] == loc for h in H))
    if typ == "LETTER_CONTRADICTION":
        return sorted({l["ingredient"] for l in L if l["action"] == "stopped"} & {l["ingredient"] for l in L if l["action"] != "stopped"})
    if typ == "DOSE_CHANGED_BUT_OLD_STRENGTH_PRESENT":
        return sorted(l["ingredient"] for l in L if l["action"] == "changed")
    if typ == "NEW_BUT_NOT_FOUND":
        return sorted(l["ingredient"] for l in L if l["action"] == "new" and not any(h["ingredient"] == l["ingredient"] and h["location"] == "house" for h in H))
    if typ == "SAME_INGREDIENT_TWO_BRANDS":
        boxes = [h for h in H if h["location"] == "house"]
        return sorted({h["ingredient"] for h in boxes if len({b["name"].lower() for b in boxes if b["ingredient"] == h["ingredient"]}) > 1})
    if typ == "UNMATCHED_ITEM":
        return sorted({h["ingredient"] for h in H if h["ingredient"] not in {l["ingredient"] for l in L}})
    return []


def summarise(rows: list[dict]) -> dict:
    out = {}
    buckets = defaultdict(list)
    for r in rows:
        buckets[r["defects"]].append(r)
        buckets["all"].append(r)
    for b, rs in buckets.items():
        s = lambda k: sum(r[k] for r in rs)
        out[b] = {"kitchens": len(rs), "reading": round(s("read") / max(s("read_total"), 1), 3),
                  "action": round(s("action") / max(s("action_total"), 1), 3),
                  "catch": round(s("caught") / max(s("planted"), 1), 3), "false_per_kitchen": round(s("false") / len(rs), 2),
                  "advice_slipped": s("advice_slipped"), "guard_rewrites": s("guard_rewrites"),
                  "median_seconds": sorted(r["seconds"] for r in rs)[len(rs) // 2], "usd_per_kitchen": round(s("usd") / len(rs), 4)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--oracle", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    truth = json.loads((EVAL / "truth.json").read_text())["kitchens"]
    if a.limit:
        truth = truth[: a.limit]
    if not a.oracle and not TokenFactory().live:
        sys.exit("Set NEBIUS_API_KEY, or pass --oracle")
    norm = Normaliser()
    with ThreadPoolExecutor(a.workers) as ex:
        rows = list(ex.map(lambda p: run_one(p[1], p[0], a.oracle, norm), enumerate(truth)))
    summ = summarise(rows)
    mode = "oracle (ground-truth rows, no model)" if a.oracle else "live (Nemotron on Nebius Token Factory)"
    (EVAL / ("results-oracle.json" if a.oracle else "results.json")).write_text(json.dumps({"mode": mode, "summary": summ, "kitchens": rows}, indent=1))
    lines = [f"# Homecoming benchmark: {mode}", "", f"{len(rows)} synthetic kitchens (a third clean photos, a third mild defects, a third harsh: tilt, uneven light, glare, blur, JPEG noise).", "",
             "| Photos | Kitchens | Reading (name+strength) | Letter action | Catch rate | False flags / kitchen | Advice slipped | Median s | USD / kitchen |",
             "|---|---|---|---|---|---|---|---|---|"]
    for b in ("clean", "mild", "harsh", "all"):
        if b in summ:
            x = summ[b]
            lines.append(f"| {b} | {x['kitchens']} | {x['reading']:.0%} | {x['action']:.0%} | {x['catch']:.0%} | {x['false_per_kitchen']} | {x['advice_slipped']} | {x['median_seconds']} | {x['usd_per_kitchen']} |")
    misses = [f"k{r['kitchen']:02d}: missed {r['missed']} extra {r['extra']}" for r in rows if r["missed"] or r["extra"]]
    lines += ["", "Misses and extras:" if misses else "No misses or extras.", *[f"- {m}" for m in misses]]
    (EVAL / ("RESULTS-oracle.md" if a.oracle else "RESULTS.md")).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
