"""Desktop (1280) + mobile (390) screenshots with Python Playwright and system Chrome.
usage: python parity/screens_playwright.py http://localhost:8799 <out dir>"""
# webapp-testing pattern: navigate, wait for networkidle, then wait for the verify result, screenshot.
import sys
from playwright.sync_api import sync_playwright
base, out = sys.argv[1], sys.argv[2]
with sync_playwright() as p:
    br = p.chromium.launch(headless=True, executable_path="/usr/bin/google-chrome")
    for w, h, tag in [(1280, 860, "d"), (390, 844, "m")]:
        for demo in ["answer", "verify", "hide", "corpus"]:
            pg = br.new_page(viewport={"width": w, "height": h})
            pg.goto(f"{base}/?demo={demo}"); pg.wait_for_load_state("networkidle")
            if demo != "answer":
                pg.wait_for_function("[...document.querySelectorAll('.how')].some(e=>/in your browser/.test(e.textContent))", timeout=90000)
                pg.wait_for_timeout(2600)
            if demo == "corpus": pg.add_style_tag(content=".ask{display:none}")
            pg.screenshot(path=f"{out}/{tag}_{demo}.png", full_page=(demo == "corpus"))
            pg.close()
    br.close()
