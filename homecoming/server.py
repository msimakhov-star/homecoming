"""The FastAPI app. `build()` takes any dict-like store so it runs identically on Modal (modal.Dict)
and locally (a plain dict): python local.py

Abuse limits live here, not in the Nebius account (Token Factory has no spend cap): per-IP requests
per hour, a global daily USD budget, image count and size caps. When a limit is hit the app serves a
recorded sample kitchen and says so instead of going dark.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
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
        # rightmost hop is the one the platform proxy appended; leftmost entries are client-controlled
        return (req.headers.get("x-forwarded-for") or (req.client.host if req.client else "?")).split(",")[-1].strip()

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

    import hashlib
    _ver = hashlib.sha1(b"".join((web_dir / f).read_bytes() for f in ("app.js", "style.css"))).hexdigest()[:10]

    def page() -> str:  # stamp static URLs with a content hash so a redeploy never serves stale JS/CSS
        return (web_dir / "index.html").read_text().replace("/static/app.js", f"/static/app.js?v={_ver}").replace("/static/style.css", f"/static/style.css?v={_ver}")

    @api.middleware("http")
    async def cache_headers(request: Request, call_next):
        resp = await call_next(request)
        if request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable" if request.url.query else "no-cache"
        elif request.url.path == "/" or request.url.path.startswith("/s/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    @api.get("/", response_class=HTMLResponse)
    def index():
        return page()

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

    async def read_uploads(letter, boxes, organiser) -> dict:
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
        return images

    def execute(request: Request, sample: int | None, images: dict | None, progress=pipeline._noop) -> dict:
        """Shared by /api/run and /api/run-stream. Never raises for model failures: degrades to a recorded kitchen."""
        tf = TokenFactory(meter=Meter())
        norm = Normaliser(cache=KV())
        kitchens = pipeline.load_kitchens()
        if sample is not None:
            idx = sample if 0 <= sample < len(kitchens) else 0
            # Ultra/Lightning run live on the fixture once per deploy, then the result is cached for everyone
            res = state.get(f"sample:{idx}:{tf.live}") if tf.live else None
            if res is None:
                res = pipeline.run(kitchen=kitchens[idx], tf=tf if not throttle(request) else TokenFactory(api_key=None),
                                   norm=norm, progress=progress)
                record_spend(res["cost"]["total_usd"])
                if tf.live and res["cost"]["total_usd"] > 0:
                    state[f"sample:{idx}:{tf.live}"] = res
            res["recorded"] = True
            res["reason"] = "sample kitchen, the photos were not re-read"
            res["sample_images"] = {g: f"/samples/{idx}/{g}.png" for g in pipeline.GROUPS}
            return res
        reason = throttle(request) or (None if tf.live else "no model key is configured on this server")
        if reason is None:
            try:
                res = pipeline.run(images, tf=tf, norm=norm, progress=progress)
                record_spend(res["cost"]["total_usd"])
                return res
            except Exception as e:  # a model outage must degrade, not go dark
                record_spend(tf.meter.total_usd)
                reason = f"the live run failed ({type(e).__name__})"
        res = pipeline.run(kitchen=kitchens[0], tf=TokenFactory(api_key=None), norm=norm, progress=progress)
        res["reason"] = reason + ", so a recorded sample kitchen is shown instead of your photos"
        res["sample_images"] = {g: f"/samples/0/{g}.png" for g in pipeline.GROUPS}
        return res

    @api.post("/api/run")
    async def run(request: Request, sample: int | None = Form(None),
                  letter: list[UploadFile] = File([]), boxes: list[UploadFile] = File([]), organiser: list[UploadFile] = File([])):
        images = None if sample is not None else await read_uploads(letter, boxes, organiser)
        return await asyncio.to_thread(execute, request, sample, images)

    @api.post("/api/run-stream")
    async def run_stream(request: Request, sample: int | None = Form(None),
                         letter: list[UploadFile] = File([]), boxes: list[UploadFile] = File([]), organiser: list[UploadFile] = File([])):
        """Server-sent events: one {"step","status",...} event per pipeline step, then {"result": ...}."""
        images = None if sample is not None else await read_uploads(letter, boxes, organiser)
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        emit = lambda step, status, info: loop.call_soon_threadsafe(q.put_nowait, {"step": step, "status": status, **info})

        async def worker():
            try:
                res = await asyncio.to_thread(execute, request, sample, images, emit)
                await q.put({"result": res})
            except Exception as e:
                await q.put({"error": f"{type(e).__name__}: {e}"[:300]})

        async def events():
            task = asyncio.create_task(worker())
            while True:
                ev = await q.get()
                yield f"data: {json.dumps(ev)}\n\n"
                if "result" in ev or "error" in ev:
                    break
            await task

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @api.post("/api/reconcile")
    def reconcile(request: Request, body: dict):
        """Re-run everything after extraction with rows the carer has edited."""
        tf = TokenFactory(meter=Meter()) if not throttle(request) else TokenFactory(api_key=None)
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
    def share(request: Request, body: dict):
        if len(json.dumps(body.get("sheet"))) > 20_000:
            raise HTTPException(413, "sheet too large")
        if throttle(request) == "the demo's hourly limit for your connection is reached":
            raise HTTPException(429, "too many links this hour")
        sid = secrets.token_urlsafe(12)
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
        return page()

    api.mount("/static", StaticFiles(directory=str(web_dir)), name="static")
    return api
