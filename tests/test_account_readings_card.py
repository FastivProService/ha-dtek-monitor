"""The submission UI requires review; unrelated updates never overwrite edits."""

import importlib.util
from pathlib import Path
import unittest

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
if HAS_PLAYWRIGHT:
    from playwright.async_api import async_playwright

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "custom_components/dtek_monitor/frontend/account-readings-card.js"
)


@unittest.skipUnless(HAS_PLAYWRIGHT, "Requires Playwright Chromium")
class ReadingsCardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.p = await async_playwright().start()
        self.browser = await self.p.chromium.launch(headless=True)
        self.page = await self.browser.new_page(viewport={"width": 390, "height": 844})
        await self.page.set_content("<main></main>")
        await self.page.add_script_tag(path=str(SCRIPT))
        await self.page.evaluate("""() => {
          window.ctx={account:'••••0001',address:'Example address',serial:'TEST',date:'2026-10-18',digits:6,revision:'revision',blocked:false,zones:[{zone:'GENERAL',previous:'100',date:'2026-09-30'}]};
          window.calls=[];window.pending=[];window.deferConfirm=false;
          window.hass={language:'en',callWS:async msg=>{
            calls.push(msg);
            if(msg.action==='context')return structuredClone(ctx);
            if(msg.action==='prepare')return {confirmation:'draft',readings:[{zone:'GENERAL',value:123,previous:'100',increase:'23'}]};
            if(msg.action==='confirm')return deferConfirm ? new Promise((resolve,reject)=>pending.push({resolve,reject})) : {status:'accepted'};
            return {status:'acknowledged'};
          }};
          window.card=document.createElement('dtek-account-readings-card');card.setConfig({entity:'sensor.example'});
          document.querySelector('main').append(card);card.hass=hass;
        }""")

    async def asyncTearDown(self):
        await self.browser.close()
        await self.p.stop()

    async def load(self):
        await self.page.get_by_role(
            "button", name="Load current meter", exact=True
        ).click()
        await self.page.get_by_label("Single zone (kWh)", exact=True).wait_for()

    async def prepare(self):
        await self.load()
        await self.page.get_by_label("Single zone (kWh)", exact=True).fill("123")
        await self.page.get_by_role(
            "button", name="Review readings", exact=True
        ).click()
        await self.page.get_by_role("checkbox").wait_for()

    async def test_opening_and_hass_updates_never_send_or_erase_input(self):
        self.assertEqual(await self.page.evaluate("calls.length"), 0)
        await self.load()
        await self.page.get_by_label("Single zone (kWh)", exact=True).fill("123")
        for _ in range(5):
            await self.page.evaluate(
                "card.hass={...hass,states:{random:Math.random()}}"
            )
        self.assertEqual(
            await self.page.get_by_label("Single zone (kWh)", exact=True).input_value(),
            "123",
        )
        self.assertEqual(
            await self.page.evaluate("calls.map(x=>x.action)"), ["context"]
        )

    async def test_review_and_checkbox_required_and_double_click_sends_once(self):
        await self.prepare()
        button = self.page.get_by_role(
            "button", name="Confirm and send to DTEK", exact=True
        )
        self.assertTrue(await button.is_disabled())
        self.assertEqual(
            await self.page.evaluate("calls.map(x=>x.action)"), ["context", "prepare"]
        )
        await self.page.get_by_role("checkbox").check()
        await self.page.evaluate("deferConfirm=true")
        await button.click()
        await self.page.evaluate("card.act('confirm')")
        self.assertEqual(
            await self.page.evaluate("calls.filter(x=>x.action==='confirm').length"), 1
        )
        await self.page.evaluate("pending[0].resolve({status:'accepted'})")
        await self.page.get_by_text(
            "DTEK explicitly confirmed receipt for every tariff zone. This does not mean the billing calculation is final.",
            exact=True,
        ).wait_for()

    async def test_back_to_editing_retains_values_and_requires_new_review(self):
        await self.prepare()
        await self.page.get_by_role(
            "button", name="Back to editing", exact=True
        ).click()
        self.assertEqual(
            await self.page.get_by_label("Single zone (kWh)", exact=True).input_value(),
            "123",
        )
        self.assertEqual(
            await self.page.get_by_role(
                "button", name="Confirm and send to DTEK", exact=True
            ).count(),
            0,
        )
        self.assertEqual(
            await self.page.evaluate("calls.filter(x=>x.action==='confirm').length"), 0
        )

    async def test_lost_response_never_retries_and_requires_status_check(self):
        await self.prepare()
        await self.page.get_by_role("checkbox").check()
        await self.page.evaluate("deferConfirm=true")
        await self.page.get_by_role(
            "button", name="Confirm and send to DTEK", exact=True
        ).click()
        await self.page.evaluate(
            "pending[0].reject(new Error('private upstream text'))"
        )
        await self.page.get_by_role(
            "button", name="Check submission status", exact=True
        ).wait_for()
        self.assertEqual(
            await self.page.get_by_label("Single zone (kWh)", exact=True).count(), 0
        )
        self.assertEqual(
            await self.page.get_by_text("private upstream text", exact=True).count(), 0
        )
        await self.page.evaluate(
            "ctx={...ctx,blocked:true,last_attempt:{id:'attempt',status:'unknown'}}"
        )
        await self.page.get_by_role(
            "button", name="Check submission status", exact=True
        ).click()
        unlock = self.page.get_by_role(
            "button", name="Unlock new entry after checking", exact=True
        )
        self.assertTrue(await unlock.is_disabled())
        await self.page.get_by_role("checkbox").check()
        await unlock.click()
        self.assertEqual(
            await self.page.evaluate("calls.filter(x=>x.action==='confirm').length"), 1
        )
        self.assertEqual(await self.page.evaluate("calls.at(-1).action"), "acknowledge")

    async def test_late_reply_for_changed_account_is_discarded(self):
        await self.page.evaluate(
            "() => {hass.callWS=msg=>new Promise(resolve=>pending.push({resolve}));}"
        )
        await self.page.get_by_role(
            "button", name="Load current meter", exact=True
        ).click()
        await self.page.evaluate(
            "card.setConfig({entity:'sensor.other'});pending[0].resolve(ctx)"
        )
        self.assertEqual(
            await self.page.get_by_text(
                "••••0001 · Example address", exact=True
            ).count(),
            0,
        )

    async def test_ukrainian_mobile_and_untrusted_address_render_as_text(self):
        await self.page.evaluate(
            "card._hass=null;card.hass={...hass,language:'uk'};ctx.address='<img src=x onerror=alert(1)>'"
        )
        await self.page.get_by_role(
            "button", name="Завантажити дані лічильника", exact=True
        ).click()
        await self.page.get_by_label("Одна зона (kWh)", exact=True).wait_for()
        self.assertEqual(await self.page.locator("img").count(), 0)
        self.assertTrue(
            await self.page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        )
