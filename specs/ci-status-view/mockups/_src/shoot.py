import asyncio, os
from playwright.async_api import async_playwright
D = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(f"{D}/png", exist_ok=True)
PAGES = ["session-card","sidebar-panel","runner-timeline","chat-card-live","compact-badge","index"]
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width":1440,"height":900}, device_scale_factor=2)
        for n in PAGES:
            pg = await ctx.new_page()
            await pg.goto(f"file://{D}/{n}.html"); await pg.evaluate("document.fonts.ready")
            await pg.wait_for_timeout(200)
            out = f"{D}/png/{n}.png"
            if n == "compact-badge":
                await pg.locator("#shot").screenshot(path=out)
            else:
                await pg.screenshot(path=out)
            # overflow check
            ov = await pg.evaluate("document.documentElement.scrollHeight")
            print(n, "scrollHeight", ov)
        await b.close()
asyncio.run(main())
