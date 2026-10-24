"""Generate the Homecoming benchmark: N synthetic kitchens with ground truth and realistic photo defects.

    python scripts/make_eval.py --n 24 --seed 7     -> data/eval/kNN-{letter,boxes,organiser}.jpg + data/eval/truth.json

Every kitchen is fictional (patient, hospital, brand names). Medicines are real generic names with real
UK strengths. Each kitchen plants a known set of discrepancies (stop still at home, dose change with old
box, new not dispensed, two brands of one ingredient, stopped drug in the organiser, letter contradiction,
an extra box not on the letter) and the photos get tilt, blur, uneven light, glare and JPEG noise so the
benchmark measures reading under kitchen conditions, not clean renders.
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from homecoming.diff import HouseItem, LetterItem, reconcile  # noqa: E402
from scripts.make_samples import box, font  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "eval"

# generic, salt printed on box, strengths (mg) commonly dispensed in the UK, fictional brand
MEDS = [
    ("amlodipine", "amlodipine besilate", [5, 10], "Amlocard"),
    ("ramipril", "ramipril", [1.25, 2.5, 5, 10], "Ramitec"),
    ("bisoprolol", "bisoprolol fumarate", [1.25, 2.5, 5, 10], "Bisocor"),
    ("atorvastatin", "atorvastatin calcium", [10, 20, 40, 80], "Atorvatab"),
    ("simvastatin", "simvastatin", [20, 40], "Simvalin"),
    ("metformin", "metformin hydrochloride", [500, 850], "Glucomet"),
    ("omeprazole", "omeprazole", [10, 20, 40], "Gastrizol"),
    ("lansoprazole", "lansoprazole", [15, 30], "Lansopral"),
    ("furosemide", "furosemide", [20, 40], "Diurofen"),
    ("bendroflumethiazide", "bendroflumethiazide", [2.5], "Bendrofix"),
    ("clopidogrel", "clopidogrel hydrogen sulfate", [75], "Clopivas"),
    ("apixaban", "apixaban", [2.5, 5], "Apixalin"),
    ("levothyroxine", "levothyroxine sodium", [0.025, 0.05, 0.1], "Thyrolev"),
    ("sertraline", "sertraline hydrochloride", [50, 100], "Sertrane"),
    ("amitriptyline", "amitriptyline hydrochloride", [10, 25], "Amitrin"),
    ("gabapentin", "gabapentin", [100, 300], "Gabaneur"),
    ("losartan", "losartan potassium", [25, 50, 100], "Losacar"),
    ("doxazosin", "doxazosin mesilate", [1, 2, 4], "Doxapro"),
    ("tamsulosin", "tamsulosin hydrochloride", [0.4], "Tamsulex"),
    ("allopurinol", "allopurinol", [100, 300], "Allopurin"),
    ("prednisolone", "prednisolone", [5], "Predsolo"),
    ("paracetamol", "paracetamol", [500], "Paramol"),
]
COLOURS = ["#2f6f9f", "#3c8c5a", "#b0462c", "#6b4a9c", "#c58a1a", "#2c7a7b", "#8a3a62", "#4a5b6c", "#9a5b2c"]


def fmt(mg: float) -> str:
    return f"{mg * 1000:g} micrograms" if mg < 1 else f"{mg:g} mg"


def kitchen(rng: random.Random, idx: int) -> dict:
    meds = rng.sample(MEDS, 7)
    letter, house, planted = [], [], []
    cell = iter(rng.sample(range(1, 10), 9))

    def add_box(generic, salt, mg, brand=None):
        name = f"{brand or generic.capitalize()} {fmt(mg)}"
        house.append({"id": f"b{len(house) + 1}", "name": name, "brand": brand or generic.capitalize(), "generic_printed": salt,
                      "ingredient": generic, "strength": fmt(mg), "location": "house", "cell": next(cell)})

    g, salt, s, brand = meds[0]  # stopped but still in the house (and sometimes in the organiser)
    letter.append({"name": f"{g.capitalize()} {fmt(s[0])}", "ingredient": g, "strength": fmt(s[0]), "action": "stopped"})
    add_box(g, salt, s[0])
    planted.append("STOPPED_BUT_PRESENT")
    if rng.random() < 0.6:
        house.append({"id": f"o{len(house)}", "name": g.capitalize(), "ingredient": g, "strength": fmt(s[0]), "location": "organiser",
                      "slot": rng.choice(["Mon AM", "Tue AM", "Wed PM"]), "cell": 0, "pill_colour": "white"})
        planted.append("ORGANISER_CONTAINS_STOPPED")

    g, salt, s, brand = meds[1]  # dose changed, old strength at home
    if len(s) > 1:
        old, new = rng.sample(s, 2)
        letter.append({"name": f"{g.capitalize()} {fmt(new)}", "ingredient": g, "strength": fmt(new), "action": "changed"})
        add_box(g, salt, old)
        planted.append("DOSE_CHANGED_BUT_OLD_STRENGTH_PRESENT")

    g, salt, s, brand = meds[2]  # new, sometimes not dispensed yet
    letter.append({"name": f"{g.capitalize()} {fmt(s[0])}", "ingredient": g, "strength": fmt(s[0]), "action": "new"})
    if rng.random() < 0.5:
        planted.append("NEW_BUT_NOT_FOUND")
    else:
        add_box(g, salt, s[0])

    g, salt, s, brand = meds[3]  # continued, two brands at home
    letter.append({"name": f"{g.capitalize()} {fmt(s[0])}", "ingredient": g, "strength": fmt(s[0]), "action": "continued"})
    add_box(g, salt, s[0])
    if rng.random() < 0.6:
        add_box(g, g, s[0], brand)
        planted.append("SAME_INGREDIENT_TWO_BRANDS")

    g, salt, s, brand = meds[4]  # plain continued, matches
    letter.append({"name": f"{g.capitalize()} {fmt(s[-1])}", "ingredient": g, "strength": fmt(s[-1]), "action": "continued"})
    add_box(g, salt, s[-1])

    if rng.random() < 0.35:  # the letter contradicts itself
        g, salt, s, brand = meds[5]
        letter.append({"name": f"{g.capitalize()} {fmt(s[0])}", "ingredient": g, "strength": fmt(s[0]), "action": "stopped"})
        letter.append({"name": f"{g.capitalize()} {fmt(s[0])}", "ingredient": g, "strength": fmt(s[0]), "action": "continued"})
        planted.append("LETTER_CONTRADICTION")

    if rng.random() < 0.5:  # an extra box the letter never mentions
        g, salt, s, brand = meds[6]
        add_box(g, salt, s[0])
        planted.append("UNMATCHED_ITEM")

    # ground truth discrepancies come from the planted scenario, cross-checked against the engine below
    li = [LetterItem(l["name"], l["ingredient"], l["action"], l["strength"]) for l in letter]
    hi = [HouseItem(h["id"], h["name"], h["ingredient"], h["location"], h["strength"], h.get("slot")) for h in house]
    engine = sorted({d.type for d in reconcile(li, hi)})
    assert engine == sorted(set(planted)), (idx, engine, planted)
    return {"name": f"Eval kitchen {idx:02d}", "letter": letter, "house": house, "expected": sorted(set(planted))}


def degrade(im: Image.Image, rng: random.Random, strength: float) -> Image.Image:
    """Kitchen-photo defects: tilt, perspective-ish shear, uneven light, glare spot, blur, JPEG noise."""
    im = im.convert("RGB")
    W, H = im.size
    im = im.rotate(rng.uniform(-8, 8) * strength, expand=True, fillcolor=(120, 96, 70), resample=Image.BICUBIC)
    sh = rng.uniform(-0.08, 0.08) * strength
    im = im.transform(im.size, Image.AFFINE, (1, sh, -sh * im.size[1] / 2, 0, 1, 0), resample=Image.BICUBIC, fillcolor=(120, 96, 70))
    grad = Image.linear_gradient("L").resize(im.size).rotate(rng.choice([0, 90, 180, 270]))
    dark = ImageEnhance.Brightness(im).enhance(1 - 0.45 * strength)
    im = Image.composite(im, dark, grad)
    if rng.random() < 0.5:
        glare = Image.new("L", im.size, 0)
        d = ImageDraw.Draw(glare)
        x, y, r = rng.randint(0, im.size[0]), rng.randint(0, im.size[1]), int(min(im.size) * 0.12)
        d.ellipse((x - r, y - r, x + r, y + r), fill=int(170 * strength))
        im = Image.composite(Image.new("RGB", im.size, "white"), im, glare.filter(ImageFilter.GaussianBlur(r / 2)))
    im = im.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 1.6) * strength))
    im.thumbnail((1600, 1600))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=int(85 - 30 * strength))
    return Image.open(io.BytesIO(buf.getvalue()))


def render(k: dict, rng: random.Random, strength: float, stem: Path):
    from scripts.make_samples import letter_page, organiser_photo
    tmp = stem.parent / "_tmp.png"
    letter_page(k, tmp); degrade(Image.open(tmp), rng, strength).save(f"{stem}-letter.jpg", quality=92)
    W = H = 1200
    im = Image.new("RGB", (W, H), "#b58b5a")
    d = ImageDraw.Draw(im)
    for i in range(0, W, 60):
        d.line((i, 0, i + 40, H), fill="#a77d4f", width=2)
    for n, h in enumerate([h for h in k["house"] if h["location"] == "house"]):
        c = h["cell"] - 1
        box(d, (c % 3) * 400 + 30, (c // 3) * 400 + 30, 340, 340, h["brand"], h["generic_printed"], h["strength"], COLOURS[n % len(COLOURS)])
    degrade(im, rng, strength).save(f"{stem}-boxes.jpg", quality=92)
    organiser_photo(k, tmp); degrade(Image.open(tmp), rng, strength * 0.7).save(f"{stem}-organiser.jpg", quality=92)
    tmp.unlink()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)
    truth = []
    for i in range(a.n):
        k = kitchen(rng, i)
        strength = [0.0, 0.5, 1.0][i % 3]  # a third clean, a third mild, a third harsh
        k["defects"] = {0.0: "clean", 0.5: "mild", 1.0: "harsh"}[strength]
        render(k, rng, strength, OUT / f"k{i:02d}")
        truth.append(k)
    (OUT / "truth.json").write_text(json.dumps({"seed": a.seed, "kitchens": truth}, indent=1))
    counts = {}
    for k in truth:
        for e in k["expected"]:
            counts[e] = counts.get(e, 0) + 1
    print(f"wrote {a.n} kitchens to {OUT}; planted discrepancies: {counts}")
