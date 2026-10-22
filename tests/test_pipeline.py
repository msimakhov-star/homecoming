import os

from homecoming import pipeline
from homecoming.guard import to_question, violates
from homecoming.llm import Meter, TokenFactory
from homecoming.normalise import Normaliser


def offline():
    return TokenFactory(api_key=None, meter=Meter()), Normaliser(cache={}, tavily_key=None, rxnorm=lambda s: [])


def test_recorded_run_end_to_end():
    tf, norm = offline()
    for k in pipeline.load_kitchens():
        res = pipeline.run(kitchen=k, tf=tf, norm=norm)
        assert res["recorded"] is True
        assert sorted({c["type"] for c in res["cards"]}) == sorted(k["expected"]), k["name"]
        for c in res["cards"]:
            assert c["question"].strip().endswith("?"), c
            assert not violates(c["question"])
        assert res["cost"]["total_usd"] == 0


def test_cards_sorted_by_severity_and_point_at_sources():
    tf, norm = offline()
    res = pipeline.run(kitchen=pipeline.load_kitchens()[0], tf=tf, norm=norm)
    sev = [c["severity"] for c in res["cards"]]
    assert sev == sorted(sev, key={"check-today": 0, "ask-when-convenient": 1, "info": 2}.get)
    stopped = next(c for c in res["cards"] if c["type"] == "STOPPED_BUT_PRESENT")
    assert stopped["found_ids"] == ["B1"] and stopped["letter_ids"] == ["L1"]


def test_edit_then_finish_recomputes():
    tf, norm = offline()
    rows = pipeline.recorded_extraction(pipeline.load_kitchens()[0])
    # carer corrects a misread: the Ramipril box is actually 5 mg, so the dose-change card must disappear
    rows["boxes"][1]["strength"] = "5mg"
    res = pipeline.finish(rows, tf, norm, recorded=True)
    assert "DOSE_CHANGED_BUT_OLD_STRENGTH_PRESENT" not in {c["type"] for c in res["cards"]}


def test_guard_blocklist():
    assert violates("You should stop giving amlodipine tonight.")
    assert violates("Stop the ramipril until Monday")
    assert violates("It is safe to take both.")
    assert not violates("The letter says STOP amlodipine, but a box of it is in the house")
    assert not violates("Should he stop the amlodipine box at home?")
    assert to_question("Stop the ramipril").endswith("?")


def test_live_rxnorm_when_network_allowed():
    if not os.environ.get("LIVE"):
        return
    n = Normaliser(cache={}, tavily_key=None)
    assert n.resolve("Lipitor 20mg")["ingredient"] == "atorvastatin"
    assert n.resolve("Zocor 20mg tablets")["ingredient"] == "simvastatin"
    assert n.resolve("Paracetamol 500 mg")["ingredient"] == "acetaminophen"      # UK name mapped
    assert n.resolve("Co-codamol 30/500")["confirmed"] is False                   # must never fuzzy-match a wrong drug
    assert n.resolve("Losec 20mg")["confirmed"] is False                          # UK brand: needs the Tavily fallback
