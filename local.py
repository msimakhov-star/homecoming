"""Run Homecoming on your own machine:  python local.py   (then open http://localhost:8787)

Set NEBIUS_API_KEY (and optionally TAVILY_API_KEY) to call the models; without them it runs in recorded mode.
"""
from pathlib import Path

import uvicorn

from homecoming.server import build

ROOT = Path(__file__).parent
app = build({}, ROOT / "web", ROOT / "data" / "samples")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8787)
