"""Build verify.html: one self-contained file (all CSS/JS inlined) that verifies an answer bundle
in the browser, talking only to the public Arc testnet RPC. Open it from disk (file://), no server needed.
usage: python build_verify_html.py   -> writes webdemo/verify.html and ../verify.html"""
import base64
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
S = HERE / "static"
read = lambda n: (S / n).read_text(encoding="utf-8")


def css_inline_fonts(css: str) -> str:
    """Embed the self-hosted OFL fonts as data: URIs so the file stays standalone."""
    return re.sub(r'url\("fonts/([^"]+)"\)', lambda m: 'url("data:font/woff2;base64,' + base64.b64encode((S / "fonts" / m.group(1)).read_bytes()).decode() + '")', css)

html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<link rel="icon" href="data:,">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verify an answer bundle · Arc testnet</title>
<!-- Fonts: Atkinson Hyperlegible Next and Mono (SIL Open Font License 1.1), embedded. Standalone verifier. Everything below is inlined; the only network requests go to https://rpc.testnet.arc.io and, for the retrieval replay, the committed corpus/model files on https://raw.githubusercontent.com (sha256-checked) -->
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src https://rpc.testnet.arc.io https://raw.githubusercontent.com">
<style>
{css_inline_fonts(read("ui.css"))}
</style>
</head>
<body>
<div class="wrap">
  <header class="top"><h1>Verify an answer</h1><p>Runs in this page. It talks only to rpc.testnet.arc.io and fetches the replay files from raw.githubusercontent.com, hash-checked.</p></header>
  <main class="main"><div class="zone" id="zone">Drop an answer bundle here, or choose a file</div>
  <div id="thread" style="margin-top:48px"></div></main>
</div>
<div class="drop" id="drop">Drop to verify</div>
<script>
{read("noble-hashes.min.js")}
</script>
<script>
{read("replay.js")}
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
