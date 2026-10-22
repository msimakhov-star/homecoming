"""Go/no-go probe: can Nemotron 3.5 Super VL read medicine boxes and a discharge letter well enough?

    NEBIUS_API_KEY=... python scripts/probe.py                  # runs on the synthetic sample kitchens
    NEBIUS_API_KEY=... python scripts/probe.py photos/          # photos/truth.json: {"img.jpg": [{"name": "Ramipril", "strength": "2.5 mg"}]}

Prints HIT/MISS per expected item, tokens, latency and GO (>= 70% of items read with the right name and strength).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from homecoming.diff import strength_key  # noqa: E402
from homecoming.llm import Meter, TokenFactory  # noqa: E402
from homecoming.pipeline import extract  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def truth_from_samples() -> list[tuple[Path, str, list[dict]]]:
    ks = json.loads((ROOT / "data" / "kitchens.json").read_text())["kitchens"]
    cases = []
    for i, k in enumerate(ks):
        cases.append((ROOT / "data/samples" / f"kitchen{i}-letter.png", "letter",
                      [{"name": l["name"].split()[0], "strength": l["strength"], "action": l["action"]} for l in k["letter"]]))
        cases.append((ROOT / "data/samples" / f"kitchen{i}-boxes.png", "boxes",
                      [{"name": h["brand"], "strength": h["strength"], "cell": h["cell"]} for h in k["house"] if h["location"] == "house"]))
    return cases


def truth_from_dir(d: Path) -> list[tuple[Path, str, list[dict]]]:
    t = json.loads((d / "truth.json").read_text())
    return [(d / f, "letter" if "letter" in f.lower() else "boxes", items) for f, items in t.items()]


def main():
    tf = TokenFactory(meter=Meter())
    if not tf.live:
        sys.exit("Set NEBIUS_API_KEY first")
    cases = truth_from_dir(Path(sys.argv[1])) if len(sys.argv) > 1 else truth_from_samples()
    hit = total = cell_hit = cell_total = action_hit = action_total = 0
    for path, group, want in cases:
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        rows = extract(tf, group, [(path.read_bytes(), mime)])
        print(f"\n{path.name} ({group}): read {len(rows)} items in {tf.meter.calls[-1]['ms']} ms")
        for r in rows:
            print(f"   {r['id']}: {r['name_as_printed']!r} {r['strength']!r} action={r['action']} cell={r['cell']} conf={r['confidence']}")
        for w in want:
            total += 1
            match = next((r for r in rows if w["name"].lower() in r["name_as_printed"].lower()
                          and strength_key(r["strength"]) == strength_key(w["strength"])), None)
            ok = match is not None
            hit += ok
            extra = ""
            if ok and "cell" in w:
                cell_total += 1; cell_hit += match["cell"] == w["cell"]; extra += f" cell {match['cell']} vs {w['cell']}"
            if ok and "action" in w:
                action_total += 1; action_hit += match["action"] == w["action"]; extra += f" action {match['action']} vs {w['action']}"
            print(f"   {'HIT ' if ok else 'MISS'} {w['name']} {w['strength']}{extra}")
    pct = 100 * hit / total if total else 0
    print(f"\nname+strength {hit}/{total} = {pct:.0f}%  | cell {cell_hit}/{cell_total} | action {action_hit}/{action_total}")
    print(f"cost ${tf.meter.total_usd} for {len(tf.meter.calls)} calls")
    print("GO: build on Super VL" if pct >= 70 else "NO-GO: try per-item close-ups; if still under 70%, switch to the runner-up idea")


if __name__ == "__main__":
    main()
