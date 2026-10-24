"""Exercise the code that only runs with a real NEBIUS_API_KEY, against a fake OpenAI-compatible server.

The fake rejects the OpenAI json_schema wrapper with 400 (to prove the bare-schema fallback works),
returns VL extractions for the sample kitchen, an Ultra answer containing one piece of advice (which the
guard must rewrite), and Lightning guard verdicts.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from homecoming import llm, pipeline
from homecoming.guard import violates
from homecoming.llm import Meter, TokenFactory
from homecoming.normalise import Normaliser

ROOT = Path(__file__).resolve().parent.parent
K = pipeline.load_kitchens()[0]
SEEN = []


def vl_items(group):
    if group == "letter":
        return [{"name_as_printed": l["name"], "generic_as_printed": l["ingredient"], "strength": l["strength"], "form": "",
                 "instructions": "", "action": l["action"], "cell": 0, "slot": "", "confidence": "high"} for l in K["letter"]]
    loc = "house" if group == "boxes" else "organiser"
    return [{"name_as_printed": h["name"], "generic_as_printed": h.get("generic_printed") or h["ingredient"], "strength": h["strength"],
             "form": "", "instructions": "", "action": "none", "cell": h["cell"], "slot": h.get("slot", ""), "confidence": "high"}
            for h in K["house"] if h["location"] == loc]


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body)
        parts = body["messages"][0]["content"] if isinstance(body["messages"][0]["content"], list) else []
        if any(p.get("type") == "video_url" for p in parts):
            return self._send(400, {"error": "video input not supported in this test"})
        rf = body.get("response_format", {}).get("json_schema", {})
        if isinstance(rf, dict) and "name" in rf and "schema" in rf:
            return self._send(400, {"error": "json_schema must be a JSON Schema"})
        model = body["model"]
        if model == llm.MODELS["vl"]:
            text = body["messages"][0]["content"][0]["text"]
            group = "letter" if "discharge letter" in text else "organiser" if "organiser" in text else "boxes"
            content = json.dumps({"items": vl_items(group), "notes": ""})
        elif model == llm.MODELS["reason"]:
            ctx = json.loads(body["messages"][1]["content"])
            cards = [{"index": d["index"], "headline": f"About {d['ingredient']}", "detail": "see sources",
                      "question": "You should stop giving it tonight." if d["type"] == "STOPPED_BUT_PRESENT" else f"Is the {d['ingredient']} right?",
                      "severity": "check-today"} for d in ctx["discrepancies"]]
            content = "Thinking done.\n" + json.dumps({"letter_audit": [], "cards": cards})
        else:
            text = body["messages"][-1]["content"]
            bad = "should stop" in text.lower()
            content = json.dumps({"ok": not bad, "rewritten": "Should we stop giving it tonight, or wait to ask?" if bad else ""})
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": content}}],
                         "usage": {"prompt_tokens": 1000, "completion_tokens": 200}})

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def test_live_pipeline_against_fake_token_factory(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(llm, "BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1")
    try:
        tf = TokenFactory(api_key="test", meter=Meter())
        norm = Normaliser(cache={}, tavily_key=None, rxnorm=lambda s: [])  # offline: ingredients come from printed generics
        imgs = {g: [((ROOT / "data/samples" / f"kitchen0-{g}.png").read_bytes(), "image/png")] for g in pipeline.GROUPS}
        res = pipeline.run(imgs, tf=tf, norm=norm)
    finally:
        srv.shutdown()

    assert res["recorded"] is False
    assert sorted({c["type"] for c in res["cards"]}) == sorted(K["expected"])
    stopped = next(c for c in res["cards"] if c["type"] == "STOPPED_BUT_PRESENT")
    assert stopped["guard_rewrote"] and not violates(stopped["question"]), stopped["question"]
    assert res["cost"]["total_usd"] > 0 and {c["step"] for c in res["cost"]["calls"]} >= {"extract:letter", "interpret", "guard"}
    # the bare-schema fallback was used after the OpenAI wrapper was rejected
    assert any(isinstance(b.get("response_format", {}).get("json_schema"), dict) and "properties" in b["response_format"]["json_schema"] for b in SEEN)
    # photos were sent as JPEG data URIs after shrinking
    first_vl = next(b for b in SEEN if b["model"] == llm.MODELS["vl"])
    assert first_vl["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_video_walkthrough_falls_back_to_frames_and_dedupes(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(llm, "BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1")
    try:
        tf = TokenFactory(api_key="test", meter=Meter())
        norm = Normaliser(cache={}, tavily_key=None, rxnorm=lambda s: [])
        box = ((ROOT / "data/samples/kitchen0-boxes.png").read_bytes(), "image/png")
        imgs = {"letter": [((ROOT / "data/samples/kitchen0-letter.png").read_bytes(), "image/png")],
                "boxes": [box, box, box],  # three "frames" showing the same boxes
                "organiser": [((ROOT / "data/samples/kitchen0-organiser.png").read_bytes(), "image/png")]}
        res = pipeline.run(imgs, tf=tf, norm=norm, video=(b"fake-mp4", "video/mp4"))
    finally:
        srv.shutdown()
    names = [r["name_as_printed"] for r in res["rows"]["boxes"]]
    assert len(names) == len(set(names)) == 5, names          # merged across frames
    assert sorted({c["type"] for c in res["cards"]}) == sorted(K["expected"])


def test_rescan_reads_one_close_up(monkeypatch):
    from fastapi.testclient import TestClient
    from homecoming import server
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(llm, "BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1")
    monkeypatch.setenv("NEBIUS_API_KEY", "test")
    try:
        c = TestClient(server.build({}, ROOT / "web", ROOT / "data" / "samples"))
        img = (ROOT / "data/samples/kitchen0-boxes.png").read_bytes()
        r = c.post("/api/rescan", data={"group": "boxes"}, files={"image": ("b.png", img, "image/png")})
    finally:
        srv.shutdown()
    assert r.status_code == 200 and r.json()["item"]["name_as_printed"]
    monkeypatch.delenv("NEBIUS_API_KEY")
    assert c.post("/api/rescan", data={"group": "boxes"}, files={"image": ("b.png", img, "image/png")}).status_code == 409
