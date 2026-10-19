"""Journal cards are read-only, account-scoped, and reject stale responses."""

import importlib.util
from pathlib import Path
import unittest
from card_browser import install_card

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
if HAS_PLAYWRIGHT:
    from playwright.async_api import async_playwright
SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "custom_components/dtek_monitor/frontend/account-check-card.js"
)


@unittest.skipUnless(HAS_PLAYWRIGHT, "Requires Playwright Chromium")
class CheckCardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.p = await async_playwright().start()
        self.browser = await self.p.chromium.launch(headless=True)
        self.page = await self.browser.new_page(
            viewport={"width": 360, "height": 800}, timezone_id="America/Los_Angeles"
        )
        await install_card(self.page, SCRIPT)
        await self.page.evaluate("""() => {
          window.calls=[];window.pending=[];
          window.result={schedule_enabled:true,frequency:'reading_window',time:'19:00:00',history:[{at:'2026-10-18T19:00:00+03:00',trigger:'scheduled',status:'ready',readings:[{zone:'GENERAL',value:125,source_at:'2026-10-18T18:58:00+03:00'}]},{at:'2026-10-19T19:00:00+03:00',trigger:'manual',status:'blocked',reason:'source_stale'}]};
          window.hass={language:'en',callWS:msg=>{if(msg.type==='dtek_monitor/account_identity')return Promise.resolve({masked:'••••0001'});calls.push(msg);return new Promise(resolve=>pending.push(resolve));}};
          window.card=document.createElement('dtek-account-check-card');card.setConfig({entity:'sensor.first'});card.hass=hass;document.querySelector('main').append(card);
        }""")

    async def asyncTearDown(self):
        await self.browser.close()
        await self.p.stop()

    async def test_readonly_journal_keeps_kyiv_dates_reasons_and_no_layout_overflow(
        self,
    ):
        await self.page.evaluate("pending[0](result)")
        await self.page.get_by_text("Single zone: 125", exact=True).wait_for()
        self.assertEqual(await self.page.locator("tbody tr").count(), 2)
        self.assertIn("19:00", await self.page.locator("tbody tr").first.inner_text())
        self.assertIn("18:58", await self.page.locator("tbody tr").last.inner_text())
        self.assertEqual(
            await self.page.evaluate("calls.map(c=>c.type)"),
            ["dtek_monitor/account_checks"],
        )
        self.assertFalse(
            await self.page.evaluate("document.documentElement.scrollWidth>innerWidth")
        )
        await self.page.evaluate(
            """() => {for(let i=0;i<20;i++)card.hass={...hass,states:{}};}"""
        )
        self.assertEqual(await self.page.evaluate("calls.length"), 1)
        await self.page.get_by_role(
            "button", name="Refresh journal", exact=True
        ).click()
        self.assertEqual(
            await self.page.evaluate("calls[1].type"), "dtek_monitor/account_checks"
        )

    async def test_switched_account_cannot_show_previous_late_result(self):
        await self.page.evaluate("card.setConfig({entity:'sensor.second'})")
        await self.page.evaluate("pending[1]({...result,history:[]})")
        await self.page.get_by_text("No checks yet.", exact=False).wait_for()
        await self.page.evaluate("pending[0](result)")
        self.assertEqual(await self.page.locator("tbody tr").count(), 0)
        self.assertEqual(
            await self.page.evaluate("calls[1].entity_id"), "sensor.second"
        )

    async def test_unavailable_journal_and_unknown_reason_do_not_display_raw_details(
        self,
    ):
        await self.page.evaluate(
            "pending[0]({...result,reason:'check_store_unavailable',history:[{at:'bad',status:'blocked',reason:'<script>private</script>',trigger:'manual'}]})"
        )
        await self.page.get_by_text(
            "The check could not be completed.", exact=True
        ).wait_for()
        self.assertIn(
            "Checks are stopped", await self.page.locator("ha-card").inner_text()
        )
        self.assertNotIn("private", await self.page.locator("ha-card").inner_text())
        self.assertEqual(
            await self.page.locator("script").count(), 1
        )  # Only the test's ES module.
