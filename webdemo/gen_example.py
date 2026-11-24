"""Regenerate data/example_bundle.json with ONE real model call and ONE AnswerRegistry tx (Arc testnet only)."""
import json, sys
from pathlib import Path
from rag import Engine

q = sys.argv[1] if len(sys.argv) > 1 else "who plays general hux in the last jedi"
out = Engine().ask(q)
p = Path(__file__).resolve().parent / "data" / "example_bundle.json"
p.write_text(json.dumps(out["bundle"], indent=1, ensure_ascii=False), encoding="utf-8")
r = out["bundle"]["answer_root"]
print("answer:", out["bundle"]["answer"], "| cites", [c["id"] for c in out["bundle"]["citations"]])
print("root:", r["root"], "| tx", r["record_tx"], "| block", r["record_block"], "| gas_usdc", out["stats"]["gas_usdc"])
