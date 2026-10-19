"""Card-local account editors preserve edits and never select an account silently."""

import importlib.util
from pathlib import Path
import unittest
from card_browser import install_card

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
if HAS_PLAYWRIGHT:
    from playwright.async_api import async_playwright
SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "custom_components/dtek_monitor/frontend/account-readings-card.js"
)


@unittest.skipUnless(HAS_PLAYWRIGHT, "Requires Playwright Chromium")
class AccountEditorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.p = await async_playwright().start()
        self.browser = await self.p.chromium.launch(headless=True)
        self.page = await self.browser.new_page(viewport={"width": 360, "height": 780})
        await install_card(self.page, SCRIPT)
        await self.page.evaluate("""() => {
          window.accounts=[{entity_id:'sensor.first',masked:'••••0001',address:'Apartment One',entities:['sensor.first','sensor.reading']},{entity_id:'sensor.second',masked:'••••0002',address:'Apartment Two',entities:['sensor.second']}];
          window.events=[];window.calls=[];window.pending=[];
          window.hass={language:'en',user:{id:'user'},callWS:msg=>{calls.push(msg);return new Promise(resolve=>pending.push(resolve));}};
          window.editor=customElements.get('dtek-account-readings-card').getConfigElement();
          editor.addEventListener('config-changed',ev=>events.push(ev.detail.config));
          editor.setConfig({type:'custom:dtek-account-readings-card',entity:'sensor.first',title:'Before'});
          editor.hass=hass;document.querySelector('main').append(editor);
        }""")

    async def asyncTearDown(self):
        await self.browser.close()
        await self.p.stop()

    async def test_late_account_list_and_ha_updates_preserve_typed_title_and_selection(
        self,
    ):
        title = self.page.get_by_label("Title (optional)", exact=True)
        await title.fill("My apartment")
        await self.page.evaluate(
            """() => {editor.hass={...hass,states:{}};pending[0](accounts);}"""
        )
        await self.page.get_by_role(
            "option", name="••••0002 — Apartment Two", exact=True
        ).wait_for(state="attached")
        await self.page.get_by_label("Personal account", exact=True).select_option(
            "sensor.second"
        )
        await self.page.evaluate(
            """() => {for(let i=0;i<10;i++)editor.hass={...hass,states:{}};}"""
        )
        self.assertEqual(await title.input_value(), "My apartment")
        self.assertEqual(
            await self.page.get_by_label("Personal account", exact=True).input_value(),
            "sensor.second",
        )
        self.assertEqual(await self.page.evaluate("calls.length"), 1)
        self.assertEqual(
            await self.page.evaluate("events.at(-1).title"), "My apartment"
        )
        self.assertFalse(
            await self.page.evaluate("document.documentElement.scrollWidth>innerWidth")
        )

    async def test_manual_entity_alias_is_preserved_and_no_auto_selection(self):
        await self.page.evaluate(
            """() => {editor.setConfig({type:'custom:dtek-account-readings-card',entity:'sensor.reading'});pending[0](accounts);}"""
        )
        await self.page.get_by_role(
            "option", name="••••0001 — Apartment One", exact=True
        ).wait_for(state="attached")
        self.assertEqual(
            await self.page.get_by_label("Personal account", exact=True).input_value(),
            "sensor.reading",
        )
        self.assertEqual(await self.page.evaluate("events.length"), 0)
        await self.page.evaluate(
            "editor.setConfig({type:'custom:dtek-account-readings-card'})"
        )
        self.assertEqual(
            await self.page.get_by_label("Personal account", exact=True).input_value(),
            "",
        )
        self.assertEqual(await self.page.evaluate("events.length"), 0)

    async def test_two_cards_are_independent_and_missing_selection_never_switches(self):
        await self.page.evaluate(
            """() => {pending[0](accounts);window.other=document.createElement('dtek-account-card-editor');other.setConfig({type:'custom:dtek-account-history-card',entity:'sensor.second'});other.hass={...hass,callWS:async()=>accounts};document.querySelector('main').append(other);}"""
        )
        await self.page.get_by_label(
            "Personal account", exact=True
        ).first.select_option("sensor.second")
        await self.page.get_by_label("Personal account", exact=True).last.select_option(
            "sensor.first"
        )
        self.assertEqual(
            await self.page.evaluate("editor._config.entity"), "sensor.second"
        )
        self.assertEqual(
            await self.page.evaluate("other._config.entity"), "sensor.first"
        )
        await self.page.get_by_role(
            "button", name="Refresh account list", exact=True
        ).first.click()
        await self.page.evaluate("pending[1]([accounts[0]])")
        await self.page.get_by_role(
            "option", name="Current entity is unavailable: sensor.second", exact=True
        ).wait_for(state="attached")
        self.assertEqual(
            await self.page.get_by_label(
                "Personal account", exact=True
            ).first.input_value(),
            "sensor.second",
        )

    async def test_reconnect_ignores_old_response_and_escapes_labels(self):
        await self.page.evaluate(
            """() => {editor.remove();document.querySelector('main').append(editor);pending[1]([{...accounts[1],address:'<img src=x onerror="window.injected=true">'}]);}"""
        )
        await (
            self.page.get_by_role("option")
            .filter(has_text="<img")
            .wait_for(state="attached")
        )
        await self.page.evaluate("pending[0](accounts)")
        self.assertEqual(await self.page.locator("img").count(), 0)
        self.assertEqual(
            await self.page.get_by_role("option").filter(has_text="<img").count(), 1
        )
        self.assertIsNone(await self.page.evaluate("window.injected"))
