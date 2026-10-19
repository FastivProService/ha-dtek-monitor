"""Serve local ES modules to Playwright without external assets or a real server."""

from urllib.parse import urlsplit


async def install_card(page, script):
    async def serve(route):
        name = urlsplit(route.request.url).path.rsplit("/", 1)[-1]
        if name.endswith(".js") and (script.parent / name).is_file():
            await route.fulfill(
                path=str(script.parent / name), content_type="text/javascript"
            )
        else:
            await route.fulfill(body="<main></main>", content_type="text/html")

    await page.route("http://dtek-card.test/**", serve)
    await page.goto("http://dtek-card.test/")
    await page.add_script_tag(url="http://dtek-card.test/" + script.name, type="module")
