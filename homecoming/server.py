"""The FastAPI app. `build()` takes any dict-like store so it runs identically on Modal (modal.Dict)
and locally (a plain dict): python local.py

Abuse limits live here, not in the Nebius account (Token Factory has no spend cap): per-IP requests
per hour, a global daily USD budget, image count and size caps. When a limit is hit the app serves a
recorded sample kitchen and says so instead of going dark.
"""
from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import pipeline
from .llm import Meter, TokenFactory
from .normalise import Normaliser

MAX_IMAGES, MAX_IMAGE_BYTES = 8, 6 * 1024 * 1024
PER_IP_PER_HOUR = int(os.environ.get("PER_IP_PER_HOUR", "12"))
DAILY_BUDGET_USD = float(os.environ.get("DAILY_BUDGET_USD", "2.0"))


def build(state, web_dir: Path, samples_dir: Path) -> FastAPI:
    api = FastAPI(title="Homecoming")

    def client_ip(req: Request) -> str:
        return (req.headers.get("x-forwarded-for") or (req.client.host if req.client else "?")).split(",")[0].strip()

    def throttle(req: Request) -> str | None:
        """Returns a reason string if this request must fall back to recorded mode."""
        key = f"ip:{client_ip(req)}:{int(time.time() // 3600)}"
        n = (state.get(key) or 0) + 1
        state[key] = n
        if n > PER_IP_PER_HOUR:
            return "the demo's hourly limit for your connection is reached"
        if (state.get(f"spend:{time.strftime('%Y-%m-%d')}") or 0.0) >= DAILY_BUDGET_USD:
            return "today's model budget for the public demo is spent"
        return None

    def record_spend(usd: float):
        k = f"spend:{time.strftime('%Y-%m-%d')}"
        state[k] = (state.get(k) or 0.0) + usd

    class KV:  # Normaliser cache backed by the shared store
        def get(self, k): return state.get(k)
        def __setitem__(self, k, v): state[k] = v

    @api.get("/", response_class=HTMLResponse)
    def index():
        return (web_dir / "index.html").read_text()

    @api.get("/api/health")
    def health():
        return {"ok": True, "live": bool(os.environ.get("NEBIUS_API_KEY")), "models": pipeline.MODELS}

    @api.get("/api/samples")
    def samples():
        return [{"index": i, "name": k["name"], "images": {g: f"/samples/{i}/{g}.png" for g in pipeline.GROUPS}}
                for i, k in enumerate(pipeline.load_kitchens())]

    @api.get("/samples/{i}/{group}.png")
    def sample_image(i: int, group: str):
        p = samples_dir / f"kitchen{i}-{group}.png"
        if group not in pipeline.GROUPS or not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="image/png")

    @api.post("/api/run")
    async def run(request: Request, sample: int | None = Form(None),
                  letter: list[UploadFile] = File([]), boxes: list[UploadFile] = File([]), organiser: list[UploadFile] = File([])):
        tf = TokenFactory(meter=Meter())
        norm = Normaliser(cache=KV())
        kitchens = pipeline.load_kitchens()
        if sample is not None:
            k = kitchens[sample] if 0 <= sample < len(kitchens) else kitchens[0]
            res = pipeline.run(kitchen=k, tf=tf, norm=norm)  # Ultra/Lightning run live on the fixture if a key exists
            record_spend(res["cost"]["total_usd"])
            res["recorded"] = True
            res["reason"] = "sample kitchen, the photos were not re-read"
            res["sample_images"] = {g: f"/samples/{kitchens.index(k)}/{g}.png" for g in pipeline.GROUPS}
            return res
        files = {"letter": letter, "boxes": boxes, "organiser": organiser}
        if sum(len(v) for v in files.values()) > MAX_IMAGES:
            raise HTTPException(413, f"at most {MAX_IMAGES} photos")
        images = {}
        for g, ups in files.items():
            images[g] = []
            for u in ups:
                data = await u.read()
                if len(data) > MAX_IMAGE_BYTES:
                    raise HTTPException(413, "each photo must be under 6 MB")
                images[g].append((data, u.content_type or "image/jpeg"))
        if not images["letter"] or not images["boxes"]:
            raise HTTPException(400, "need at least one letter photo and one photo of the boxes")
        reason = throttle(request) or (None if tf.live else "no model key is configured on this server")
        if reason:
            res = pipeline.run(kitchen=kitchens[0], tf=TokenFactory(api_key=None), norm=norm)
            res["reason"] = reason + ", so a recorded sample kitchen is shown instead of your photos"
            res["sample_images"] = {g: f"/samples/0/{g}.png" for g in pipeline.GROUPS}
            return res
        try:
            res = pipeline.run(images, tf=tf, norm=norm)
        except Exception as e:  # a model outage must degrade, not go dark
            res = pipeline.run(kitchen=kitchens[0], tf=TokenFactory(api_key=None), norm=norm)
            res["reason"] = f"the live run failed ({type(e).__name__}), so a recorded sample kitchen is shown"
            res["sample_images"] = {g: f"/samples/0/{g}.png" for g in pipeline.GROUPS}
            return res
        record_spend(res["cost"]["total_usd"])
        return res

    @api.post("/api/reconcile")
    def reconcile(body: dict):
        """Re-run everything after extraction with rows the carer has edited."""
        tf = TokenFactory(meter=Meter())
        rows = {g: [r for r in body.get("rows", {}).get(g, []) if isinstance(r, dict)][:40] for g in pipeline.GROUPS}
        res = pipeline.finish(rows, tf, Normaliser(cache=KV()), recorded=not tf.live)
        record_spend(res["cost"]["total_usd"])
        return res

    @api.post("/api/ask")
    def ask(request: Request, body: dict):
        if throttle(request):
            return {"answer": "The demo's hourly limit is reached. Please try again later.", "recorded": True}
        tf = TokenFactory(meter=Meter())
        res = pipeline.ask(tf, str(body.get("question", ""))[:500], body.get("context") or {})
        record_spend(tf.meter.total_usd)
        return res

    @api.post("/api/share")
    def share(body: dict):
        sid = secrets.token_urlsafe(8)
        state[f"share:{sid}"] = {"sheet": body.get("sheet"), "created": time.time()}
        return {"id": sid, "url": f"/s/{sid}"}

    @api.get("/api/share/{sid}")
    def get_share(sid: str):
        rec = state.get(f"share:{sid}")
        if not rec or time.time() - rec["created"] > 7 * 86400:
            raise HTTPException(404, "link expired")
        return rec["sheet"]

    @api.get("/s/{sid}", response_class=HTMLResponse)
    def share_page(sid: str):
        return (web_dir / "index.html").read_text()

    api.mount("/static", StaticFiles(directory=str(web_dir)), name="static")
    return api
