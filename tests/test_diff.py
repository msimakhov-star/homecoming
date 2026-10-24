import json
from pathlib import Path

from homecoming.diff import HouseItem, LetterItem, reconcile, strength_key

KITCHENS = json.loads((Path(__file__).parent.parent / "data" / "kitchens.json").read_text())["kitchens"]


def items(k):
    letter = [LetterItem(l["name"], l["ingredient"], l["action"], l.get("strength"), f"L{i}") for i, l in enumerate(k["letter"], 1)]
    house = [HouseItem(h["id"], h["name"], h["ingredient"], h["location"], h.get("strength"), h.get("slot")) for h in k["house"]]
    return letter, house


def test_every_kitchen_matches_expected_types():
    for k in KITCHENS:
        types = sorted({d.type for d in reconcile(*items(k))})
        assert types == sorted(k["expected"]), k["name"]


def test_stopped_drug_flags_exactly_the_boxes_that_hold_it():
    d = next(x for x in reconcile(*items(KITCHENS[0])) if x.type == "STOPPED_BUT_PRESENT")
    assert [h.id for h in d.items] == ["b1"]


def test_contradiction_suppresses_dependent_flags():
    # Kitchen 2: furosemide is both stopped and continued; the box in the house must NOT also raise STOPPED_BUT_PRESENT
    types = [d.type for d in reconcile(*items(KITCHENS[1]))]
    assert "LETTER_CONTRADICTION" in types and "STOPPED_BUT_PRESENT" not in types


def test_strength_units_normalise():
    assert strength_key("0.03 g") == strength_key("30mg") == "30mg"
    assert strength_key("500 microgram") == strength_key("0.5 MG") == strength_key("500mcg")
    assert strength_key("2.5mg") != strength_key("5 mg")
    assert strength_key("1 puff") == "1 puff"
    assert strength_key(None) is None


def test_base_name_strips_salts_forms_and_strengths():
    from homecoming.normalise import base_name
    assert base_name("Amlodipine besilate 5 mg tablets") == "amlodipine"
    assert base_name("Omeprazole 20mg gastro-resistant capsules") == "omeprazole"
    assert base_name("Bisoprolol fumarate 2.5mg film coated tablets") == "bisoprolol"
    assert base_name("Co-codamol 30/500 effervescent") == "co-codamol"
    assert base_name("Metformin hydrochloride") == "metformin"
