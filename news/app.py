"""Verifiable NEWS demo server (Arc testnet only). Run: $NEWS_DATA/venv/bin/python news/app.py [--port 8766]
The page verifies bundles in the browser (newsverify.js + arcverify.js); this server only answers questions."""
import argparse
import json
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from newsreg.chain import Signer  # noqa: E402
from newsreg.db import connect  # noqa: E402
from newsreg.engine import NewsEngine, iso  # noqa: E402

STATIC = HERE.parent / "webdemo" / "static"


class UTF8JSON(JSONResponse):
    media_type = "application/json; charset=utf-8"


app = FastAPI(title="Verifiable news demo", default_response_class=UTF8JSON)
engine = NewsEngine()
engine.signer = Signer.from_env(engine.rpc)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class Ask(BaseModel):
    question: str


@app.get("/")
def index():
    return FileResponse(STATIC / "news.html")


@app.get("/api/news/status")
def status():
    sc = engine.scope()
    db = connect()
    outlets = [dict(r) for r in db.execute(
        "SELECT outlet, COUNT(*) AS articles FROM articles WHERE batch_id IS NOT NULL GROUP BY outlet ORDER BY outlet")]
    return dict(registry=engine.registry, as_of=iso(sc["as_of_ts"]), batches=len(sc["roots"]),
                articles=sum(o["articles"] for o in outlets), outlets=outlets,
                latest=[dict(root=b["root"], tx=b["tx"], block_time=iso(b["block_time"]), articles=b["article_count"]) for b in sc["batches"][-5:]])


@app.get("/api/news/example")
def example():
    return json.loads((HERE / "examples" / "bundle_genuine.json").read_text())


@app.post("/api/news/ask")
def ask(a: Ask):
    if not a.question.strip():
        raise HTTPException(400, "empty question")
    try:
        return engine.ask(a.question[:500])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
