"""Compare the real-browser results dumped by browser_test.js with verify_citations.verify_bundle."""
import json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import verify_citations as vc

def norm(r):
    hd = [(x["check"], x["ok"]) if isinstance(x, dict) else (x[0], x[1]) for x in r["header"]]
    return dict(all_pass=r["all_pass"], header=hd, model=r.get("model"), rpc_calls=r["rpc_calls"],
                citations=[(c["id"], c["status"], c["reasons"], c.get("notes")) for c in r["citations"]],
                model_call=[(x["check"], x["ok"], x["detail"]) for x in r["model_call"]],
                corpus=[(x["check"], x["ok"], x["detail"]) for x in r.get("corpus", [])])

rep = json.load(open(sys.argv[1])); bad = 0
for m, x in rep["parity"].items():
    time.sleep(1.5)
    py = norm(vc.verify_bundle(x["bundle"], artifacts_dir=os.path.join(HERE, "../data"))); js = norm(x["result"])
    same = py == js; bad += not same
    print("MATCH" if same else "DIFF ", m, "all_pass", js["all_pass"], "rpc", js["rpc_calls"])
    if not same:
        for k in js:
            if js[k] != py[k]: print("  ", k, "\n   js", js[k], "\n   py", py[k])
print("RESULT:", "browser == python" if not bad else f"{bad} differ"); sys.exit(1 if bad else 0)
