"""Render the three synthetic sample kitchens as images (letter page, boxes on a table, pill organiser).

Everything is fictional: hospital, patient, brand names on boxes. Cells in data/kitchens.json say which
3x3 grid cell each box is drawn in, so the UI can outline it in recorded mode and the VL probe has ground truth.

    python scripts/make_samples.py   -> data/samples/kitchen{i}-{letter,boxes,organiser}.png
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "samples"
OUT.mkdir(parents=True, exist_ok=True)


def font(size: int, bold: bool = False):
    for name in (["/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def letter_page(k: dict, path: Path):
    W, H = 1240, 1754
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    d.text((80, 70), "St Example's Hospital NHS Trust (FICTIONAL)", font=font(34, True), fill="#1b3a6b")
    d.text((80, 120), "Discharge summary: medicines on discharge", font=font(40, True), fill="black")
    d.text((80, 180), "Patient: Mr A. Example (fictional)   DOB: 01/01/1948   Discharged: Fri 16 Oct 2026", font=font(26), fill="black")
    d.line((80, 230, W - 80, 230), fill="#999", width=3)
    sections = [("stopped", "Medicines STOPPED in hospital"), ("changed", "Medicines CHANGED"),
                ("new", "NEW medicines started"), ("continued", "Medicines to CONTINUE unchanged"),
                ("withheld", "Medicines WITHHELD pending GP review")]
    y = 270
    for action, title in sections:
        rows = [l for l in k["letter"] if l["action"] == action]
        if not rows:
            continue
        d.text((80, y), title, font=font(32, True), fill="#1b3a6b")
        y += 50
        for l in rows:
            note = {"stopped": "Do not restart unless advised by GP.", "changed": "Dose changed: see strength.",
                    "new": "Started on the ward.", "continued": "No change.", "withheld": "Review at GP in 2 weeks."}[action]
            d.text((110, y), f"- {l['name']}", font=font(30), fill="black")
            d.text((560, y), f"{l.get('dose', 'one tablet daily')}   {note}", font=font(24), fill="#333")
            y += 44
        y += 30
    d.text((80, H - 120), "This letter is a synthetic demo document generated for the Homecoming hackathon project. "
           "It contains no real patient data.", font=font(20), fill="#777")
    im.save(path)


def box(d: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, brand: str, generic: str, strength: str, colour: str):
    d.rounded_rectangle((x, y, x + w, y + h), radius=18, fill=colour, outline="#333", width=3)
    d.rectangle((x + 12, y + 12, x + w - 12, y + 70), fill="white")
    size = 36
    while size > 18 and d.textlength(brand, font=font(size, True)) > w - 48:
        size -= 2
    d.text((x + 24, y + 20 + (36 - size) // 2), brand, font=font(size, True), fill="#1a1a1a")
    d.text((x + 24, y + 84), f"{strength}", font=font(42, True), fill="white")
    gsize = 26
    while gsize > 14 and d.textlength(generic, font=font(gsize)) > w - 48:
        gsize -= 2
    d.text((x + 24, y + 140), generic, font=font(gsize), fill="white")
    d.text((x + 24, y + 176), "28 film-coated tablets", font=font(22), fill="white")
    # dispensing label strip
    d.rectangle((x + 12, y + h - 70, x + w - 12, y + h - 12), fill="#fffbe6", outline="#aaa")
    d.text((x + 20, y + h - 64), "Take ONE tablet in the morning", font=font(20), fill="#222")
    d.text((x + 20, y + h - 38), "Example Pharmacy (fictional)  16/10/2026", font=font(16), fill="#555")


def boxes_photo(k: dict, path: Path):
    W = H = 1200
    im = Image.new("RGB", (W, H), "#b58b5a")  # table
    d = ImageDraw.Draw(im)
    for i in range(0, W, 60):  # wood grain
        d.line((i, 0, i + 40, H), fill="#a77d4f", width=2)
    colours = ["#2f6f9f", "#3c8c5a", "#b0462c", "#6b4a9c", "#c58a1a", "#2c7a7b", "#8a3a62"]
    cell_w, cell_h = W // 3, H // 3
    items = [h for h in k["house"] if h["location"] == "house"]
    for n, h in enumerate(items):
        c = h["cell"] - 1
        cx, cy = (c % 3) * cell_w, (c // 3) * cell_h
        box(d, cx + 30, cy + 30, cell_w - 60, cell_h - 60, h["brand"], h["generic_printed"], h["strength"], colours[n % len(colours)])
    im.save(path)


def organiser_photo(k: dict, path: Path):
    W, H = 1200, 700
    im = Image.new("RGB", (W, H), "#e9e4d8")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((40, 40, W - 40, H - 40), radius=24, fill="#d9e8f5", outline="#7a8ea3", width=4)
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    cw, ch = (W - 160) // 7, 200
    for r, period in enumerate(["AM", "PM"]):
        for c, day in enumerate(days):
            x, y = 80 + c * cw, 100 + r * (ch + 30)
            d.rounded_rectangle((x, y, x + cw - 12, y + ch), radius=14, fill="#f4f8fc", outline="#7a8ea3", width=3)
            d.text((x + 10, y + 8), f"{day} {period}", font=font(24, True), fill="#1b3a6b")
            slot = f"{day} {period}"
            pills = [h for h in k["house"] if h["location"] == "organiser" and h["slot"] == slot]
            for p, h in enumerate(pills):
                d.ellipse((x + 30 + p * 40, y + 90, x + 62 + p * 40, y + 122), fill=h.get("pill_colour", "white"), outline="#555", width=2)
    # the carer's own label strip on the lid, listing what each filled slot holds
    d.rectangle((80, H - 110, W - 80, H - 60), fill="#fff9c4", outline="#c9b458", width=2)
    text = "   ".join(f"{h['slot']}: {h['name']} {h['strength']}" for h in k["house"] if h["location"] == "organiser")
    d.text((92, H - 100), (text or "empty")[:140], font=font(22), fill="#333")
    im.save(path)


if __name__ == "__main__":
    ks = json.loads((ROOT / "data" / "kitchens.json").read_text())["kitchens"]
    for i, k in enumerate(ks):
        letter_page(k, OUT / f"kitchen{i}-letter.png")
        boxes_photo(k, OUT / f"kitchen{i}-boxes.png")
        organiser_photo(k, OUT / f"kitchen{i}-organiser.png")
        print("wrote kitchen", i)
