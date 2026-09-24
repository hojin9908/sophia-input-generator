"""Capture README screenshots from a running demo server.

    SOPHIA_DEMO=1 python app.py          # terminal 1
    pip install playwright && python -m playwright install chromium
    python scripts/make_screenshots.py http://127.0.0.1:5000/
"""
from playwright.sync_api import sync_playwright
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs")
PDF = os.path.join(OUT, "demo_paper.pdf")
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000/"
if not os.path.exists(PDF):
    open(PDF, 'wb').write(b'%PDF-1.4\n%demo\n')
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1200, "height": 900}, device_scale_factor=1.5, locale="ko-KR")
    pg.goto(URL)
    pg.set_input_files("#pdf", PDF)
    pg.click("#btnUpload")
    pg.wait_for_selector(".case")
    pg.locator(".case").nth(1).click()
    pg.fill("#notes", "buffer 4층 유지, 원기둥 벽 3층")
    pg.screenshot(path=f"{OUT}/01_cases.png", clip={"x": 0, "y": 0, "width": 1200, "height": 860}, full_page=True)
    pg.click("#btnSpec")
    pg.wait_for_function("document.getElementById('spec').value.length > 10")
    pg.locator("section").nth(2).screenshot(path=f"{OUT}/02_spec.png")
    pg.click("#btnGen")
    pg.wait_for_selector("#secResult:not([hidden])")
    pg.wait_for_timeout(500)
    pg.locator("#secResult").screenshot(path=f"{OUT}/03_result_openbc.png")
    # 3-D DEM case
    pg.locator(".case").nth(2).click()
    pg.click("#btnSpec")
    pg.wait_for_function("document.getElementById('spec').value.includes('dem_bed_3d')")
    pg.click("#btnGen")
    pg.wait_for_function("document.getElementById('head').textContent.includes('36')")
    pg.wait_for_timeout(500)
    pg.locator("#secResult").screenshot(path=f"{OUT}/04_result_dem3d.png")
    b.close()
os.remove(PDF)
