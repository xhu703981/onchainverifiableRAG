"""Parity: browser verifier (static/arcverify.js run in Node) vs verify_citations.verify_bundle on the same bundles."""
import glob, json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import verify_citations as vc

def norm(r):
    hd = [(x["check"], x["ok"]) if isinstance(x, dict) else (x[0], x[1]) for x in r["header"]]
    return dict(all_pass=r["all_pass"], header=hd, model=r.get("model"), rpc_calls=r["rpc_calls"],
                citations=[(c["id"], c["status"], c["reasons"], c.get("notes")) for c in r["citations"]],
                model_call=[(x["check"], x["ok"], x["detail"]) for x in r["model_call"]],
                corpus=[(x["check"], x["ok"], x["detail"]) for x in r.get("corpus", [])])

cases = [("example", os.path.join(HERE, "../data/example_bundle.json"), m) for m in ["", "char", "answer", "prompt", "model", "hide"]]
cases += [("inferencelog_v1", os.path.join(HERE, "../data/example_bundle_inferencelog.json"), m) for m in ["", "answer"]]
cases += [(os.path.basename(f), f, "") for f in sorted(glob.glob(os.path.join(HERE, "../../experiments/demo/bundle_*.json")))]
bad = 0
import time
for name, f, mode in cases:
    time.sleep(1.5)
    out = f"/tmp/parity_{name}_{mode or 'genuine'}.json"
    subprocess.run(["node", os.path.join(HERE, "run_js.js"), f, out] + ([mode] if mode else []), check=True)
    b = json.load(open(out + ".bundle.json")); b.pop("_t", None)
    js, pyr = norm(json.load(open(out))), norm(vc.verify_bundle(b, artifacts_dir=os.path.join(HERE, "../data")))
    same = js == pyr
    if not same:
        bad += 1
        for k in js:
            if js[k] != pyr[k]: print("   DIFF", k, "\n     js:", js[k], "\n     py:", pyr[k])
    fails = [c[0] for c in js["model_call"] + js["corpus"] if c[1] is False] + [f"{c[0]}:{c[1]}" for c in js["citations"] if c[1] != "PASS"]
    print(f"{'MATCH' if same else 'DIFF '} {name:42s} {mode or 'genuine':8s} all_pass={js['all_pass']!s:5s} rpc={js['rpc_calls']:2d} fails={[x[:24] for x in fails]}")
print("RESULT:", "all cases identical" if not bad else f"{bad} case(s) differ"); sys.exit(1 if bad else 0)
