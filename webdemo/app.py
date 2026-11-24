"""Local web demo: verifiable RAG on Arc testnet. Run: python app.py [--port 8765]"""
import argparse
import json
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import verify_citations as vc
from rag import CHAIN_ID, INFERENCE_LOG, SOURCE_REGISTRY, Engine

HERE = Path(__file__).resolve().parent
class UTF8JSON(JSONResponse):
    media_type = "application/json; charset=utf-8"


app = FastAPI(title="Verifiable RAG demo", default_response_class=UTF8JSON)
engine = Engine()


class Ask(BaseModel):
    question: str


class Verify(BaseModel):
    bundle: dict


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/verify.html")
def standalone_verifier():
    return FileResponse(HERE / "verify.html")


@app.get("/api/health")
def health():
    return dict(ok=True, chain_id=CHAIN_ID, source_registry=SOURCE_REGISTRY, inference_log=INFERENCE_LOG,
                corpus=len(engine.docs), fakes=sum(d["kind"] == "fake" for d in engine.docs),
                logger=engine.acct.address if engine.acct else None, openrouter_key_loaded=bool(engine.or_key))


@app.get("/api/example")
def example():
    return json.load(open(HERE / "data" / "example_bundle.json", encoding="utf-8"))


@app.get("/api/samples")
def samples():
    return engine.samples


@app.post("/api/ask")
def ask(a: Ask):
    if not a.question.strip():
        raise HTTPException(400, "empty question")
    try:
        return engine.ask(a.question[:500])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"{type(e).__name__}: {e}")


# Server-side reference verifier (the web page does NOT use it; it verifies in the browser).
@app.post("/api/verify")
def verify(v: Verify):
    try:
        return vc.verify_bundle(v.bundle, vc.DEFAULT_RPC)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
