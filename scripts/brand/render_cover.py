"""Render the Ko-fi cover HTML to PNG at exact cover dimensions.

The banner source lives beside this script so the next state flip is an
edit, not a rebuild from a screenshot -- the previous cover had no source
in the repo, which is why it had to be recreated by eye when Louisiana
went live.

Local only: Playwright is deliberately NOT in requirements.txt and must
never reach NFSN's FreeBSD.

    .venv/Scripts/python.exe scripts/brand/render_cover.py [out.png]
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
SRC = HERE / "kofi_cover.html"
W, H = 1200, 400
SCALE = 2  # 2400x800 so Ko-fi's own downscale stays crisp


def main() -> None:
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else HERE / "kofi_cover.png")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": W, "height": H}, device_scale_factor=SCALE
        )
        page.goto(SRC.as_uri())
        page.wait_for_timeout(600)  # let the webfont settle before capture
        page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": W, "height": H})
        browser.close()
    print(f"wrote {out}  ({W*SCALE}x{H*SCALE})")


if __name__ == "__main__":
    main()
