"""Build verify.html: one self-contained file (all CSS/JS inlined) that verifies an answer bundle
in the browser, talking only to the public Arc testnet RPC. Open it from disk (file://), no server needed.
usage: python build_verify_html.py   -> writes webdemo/verify.html and ../verify.html"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
S = HERE / "static"
read = lambda n: (S / n).read_text(encoding="utf-8")

html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verify an answer bundle · Arc testnet</title>
<!-- Standalone verifier. Everything below is inlined; the only network requests go to https://rpc.testnet.arc.io -->
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src https://rpc.testnet.arc.io">
<style>
{read("ui.css")}
</style>
</head>
<body>
<div class="wrap">
  <div class="top"><h1><b>Verify</b> an answer</h1></div>
  <div class="zone" id="zone">Drop an answer bundle (.json) here, or click to choose a file
    <div class="how" style="margin-top:8px">runs entirely in this page · talks only to rpc.testnet.arc.io</div></div>
  <div id="thread" style="margin-top:48px"></div>
</div>
<div class="drop" id="drop">Drop to verify</div>
<script>
{read("noble-hashes.min.js")}
</script>
<script>
{read("arcverify.js")}
</script>
<script>
{read("ui.js")}
</script>
<script>
VUI.initFileInput(document.getElementById("thread"), document.getElementById("zone"), document.getElementById("drop"));
</script>
</body>
</html>
"""
for out in (HERE / "verify.html", HERE.parent / "verify.html"):
    out.write_text(html, encoding="utf-8")
    print("wrote", out, len(html), "bytes")
