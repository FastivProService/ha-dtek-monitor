"""Read-back stays explicit, scoped and does not reproduce the cabinet history."""

import importlib.util
from pathlib import Path
import unittest
from card_browser import install_card

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
if HAS_PLAYWRIGHT:
    from playwright.async_api import async_playwright

SCRIPT = Path(__file__).resolve().parents[1] / "custom_components/dtek_monitor/frontend/account-readback.js"


@unittest.skipUnless(HAS_PLAYWRIGHT, "Requires Playwright Chromium")
class ReadbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.p = await async_playwright().start()
        self.browser = await self.p.chromium.launch(headless=True)
        self.page = await self.browser.new_page()
        await install_card(self.page, SCRIPT)
        await self.page.evaluate("""() => {
          window.calls=[];window.data={status:'no_attempt',latest:[{value:'PRIVATE-DUPLICATE'}],fetched_at:'2026-10-18T12:00:00+03:00'};
          window.hass={language:'en',user:{id:'one'},callWS:async m=>{calls.push(m);return data;}};
          window.card=document.createElement('dtek-account-readback');card.configure(hass,'sensor.first');document.body.append(card);
        }""")

    async def asyncTearDown(self):
        await self.browser.close()
        await self.p.stop()

    async def test_explicit_read_only_check_without_duplicate_history(self):
        self.assertEqual(await self.page.evaluate("calls.length"), 0)
        await self.page.get_by_role("button").click()
        await self.page.get_by_text("There is no submission recorded", exact=False).wait_for()
        self.assertNotIn("PRIVATE-DUPLICATE", await self.page.locator("dtek-account-readback section").inner_text())
        self.assertEqual(await self.page.evaluate("calls.map(c=>c.type)"), ["dtek_monitor/account_readback"])

    async def test_unknown_and_unmatched_attempt_does_not_offer_retry(self):
        await self.page.evaluate("data={status:'not_found',attempt:{status:'unknown',readings:[{zone:'GENERAL',value:321}]},matches:[],fetched_at:'2026-10-18T12:00:00+03:00'}")
        await self.page.get_by_role("button").click()
        await self.page.get_by_text("No matching record yet.", exact=False).wait_for()
        self.assertIn("Single zone: 321 kWh", await self.page.locator("dtek-account-readback section").inner_text())
        self.assertEqual(await self.page.get_by_role("button").count(), 1)
        self.assertEqual(await self.page.evaluate("calls.map(c=>c.type)"), ["dtek_monitor/account_readback"])

    async def test_late_response_does_not_follow_new_account_or_user(self):
        await self.page.evaluate("() => { hass.callWS=m=>new Promise(resolve=>window.finish=resolve); }")
        await self.page.get_by_role("button").click()
        await self.page.evaluate("card.configure({...hass,user:{id:'two'}},'sensor.second');finish({status:'matched',attempt:{readings:[{zone:'GENERAL',value:99999}]}})")
        self.assertNotIn("99,999", await self.page.locator("dtek-account-readback section").inner_text())
        self.assertIsNone(await self.page.evaluate("card._data"))
