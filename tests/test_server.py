from pathlib import Path

from fastapi.testclient import TestClient

from homecoming import server

ROOT = Path(__file__).resolve().parent.parent


def client(**env):
    state = {}
    return TestClient(server.build(state, ROOT / "web", ROOT / "data" / "samples")), state


def test_sample_run_and_reconcile_roundtrip(monkeypatch):
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    c, _ = client()
    r = c.post("/api/run", data={"sample": "0"})
    assert r.status_code == 200
    res = r.json()
    assert res["recorded"] and len(res["cards"]) == 6
    rows = res["rows"]
    rows["boxes"][1]["strength"] = "5 mg"  # carer corrects the ramipril box
    r2 = c.post("/api/reconcile", json={"rows": rows})
    assert r2.status_code == 200
    assert "DOSE_CHANGED_BUT_OLD_STRENGTH_PRESENT" not in {x["type"] for x in r2.json()["cards"]}


def test_upload_requires_letter_and_boxes(monkeypatch):
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    c, _ = client()
    r = c.post("/api/run", files={"boxes": ("b.jpg", b"x", "image/jpeg")})
    assert r.status_code == 400


def test_share_caps_and_roundtrip():
    c, _ = client()
    big = {"sheet": {"cards": ["x" * 30_000]}}
    assert c.post("/api/share", json=big).status_code == 413
    sid = c.post("/api/share", json={"sheet": {"cards": [], "date": "2026-10-24"}}).json()["id"]
    assert c.get(f"/api/share/{sid}").json()["date"] == "2026-10-24"
    assert c.get("/api/share/nope").status_code == 404


def test_rate_limit_uses_rightmost_forwarded_ip(monkeypatch):
    monkeypatch.setattr(server, "PER_IP_PER_HOUR", 2)
    c, _ = client()
    for i in range(2):  # spoofing the leftmost hop must not reset the counter
        c.post("/api/ask", json={"question": "q"}, headers={"x-forwarded-for": f"1.1.1.{i}, 9.9.9.9"})
    r = c.post("/api/ask", json={"question": "q"}, headers={"x-forwarded-for": "8.8.8.8, 9.9.9.9"})
    assert "limit" in r.json()["answer"]


def test_sample_image_paths_are_whitelisted():
    c, _ = client()
    assert c.get("/samples/0/boxes.png").status_code == 200
    assert c.get("/samples/0/..%2F..%2Fkitchens.png").status_code == 404


def test_run_stream_emits_steps_then_result(monkeypatch):
    import json as _json
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    c, _ = client()
    with c.stream("POST", "/api/run-stream", data={"sample": "1"}) as r:
        events = [_json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    steps = [e["step"] for e in events if "step" in e]
    assert steps[:3] == ["extract:letter", "extract:boxes", "extract:organiser"] and "guard" in steps
    assert "result" in events[-1] and {x["type"] for x in events[-1]["result"]["cards"]} == {"LETTER_CONTRADICTION", "SAME_INGREDIENT_TWO_BRANDS"}
