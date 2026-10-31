"""Render consistent 1600x900 AGCN Live Voice promotional screenshots.

These are illustrations built from the app UI mockups, not live user data.
"""
from io import BytesIO
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / "screenshots"
OUTPUT = SOURCES / "images"
SCREENS = ("dashboard", "produto", "voz-audio")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for screen in SCREENS:
                page = browser.new_page(
                    viewport={"width": 1600, "height": 900},
                    device_scale_factor=1,
                    color_scheme="light",
                )
                page.goto((SOURCES / f"{screen}.html").resolve().as_uri(), wait_until="load")
                page.evaluate("document.fonts.ready")
                screenshot = page.screenshot(type="png", full_page=False, animations="disabled")
                with Image.open(BytesIO(screenshot)) as image:
                    image.convert("RGB").save(
                        OUTPUT / f"{screen}.webp", format="WEBP", quality=83, method=6
                    )
                page.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
