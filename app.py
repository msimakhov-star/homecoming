"""Homecoming on Modal: one scale-to-zero container serving the web UI and the API.

    modal deploy app.py     # production URL
    modal serve app.py      # live-reloading dev URL
    modal secret create homecoming NEBIUS_API_KEY=... TAVILY_API_KEY=...
        # once. Without the secret the app deploys fine and runs in recorded mode.
"""
from pathlib import Path

import modal

ROOT = Path(__file__).parent
app = modal.App("homecoming")
image = (modal.Image.debian_slim(python_version="3.12")
         .pip_install("fastapi[standard]>=0.115", "pillow>=10")
         .add_local_dir(ROOT / "homecoming", remote_path="/root/homecoming")
         .add_local_dir(ROOT / "web", remote_path="/root/web")
         .add_local_dir(ROOT / "data", remote_path="/root/data"))
state = modal.Dict.from_name("homecoming-state", create_if_missing=True)  # rate limits, ingredient cache, share links

try:
    secrets = [modal.Secret.from_name("homecoming")]
except Exception:  # not created yet: recorded mode
    secrets = []


@app.function(image=image, secrets=secrets, scaledown_window=120, timeout=300)
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def web():
    from homecoming.server import build
    return build(state, Path("/root/web"), Path("/root/data/samples"))
