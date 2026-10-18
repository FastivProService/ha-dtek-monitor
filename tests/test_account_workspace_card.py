"""Account workspace interactions: isolated drafts, lazy views and readable states."""

import importlib.util
from pathlib import Path
import unittest

from card_browser import install_card

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
if HAS_PLAYWRIGHT:
    from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "custom_components/dtek_monitor/frontend/account-workspace-card.js"
FIXTURE = ROOT / "tests/fixtures/account_workspace.js"


@unittest.skipUnless(HAS_PLAYWRIGHT, "Requires Playwright Chromium")
class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.p = await async_playwright().start()
        self.browser = await self.p.chromium.launch(headless=True)
        self.page = await self.browser.new_page(viewport={"width": 390, "height": 844})
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        await install_card(self.page, SCRIPT)
        await self.page.evaluate((FIXTURE.parent / "schedule_grid.js").read_text())
        await self.page.evaluate(FIXTURE.read_text())
        await self.page.evaluate("""() => {
          hass.language='en';window.card=document.createElement('dtek-account-workspace-card');
          card.setConfig({entity:'sensor.account_1'});card.hass=hass;document.querySelector('main').append(card);
        }""")
        await self.page.get_by_role("tab", name="Overview", exact=True).wait_for()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.p.stop()
        self.assertFalse(self.errors)

    async def readings(self):
        await self.page.get_by_role("tab", name="Readings", exact=True).click()
        return self.page.get_by_label("Single zone (kWh)", exact=True)

    async def test_overview_is_local_and_separates_reading_from_consumption(self):
        text = await self.page.locator("#panel-overview").inner_text()
        self.assertIn("12,400", text)
        self.assertIn("85", text)
        self.assertIn("Cumulative meter reading", text)
        self.assertIn("September 2026", text)
        self.assertIn("Today's schedule is published", text)
        self.assertEqual(await self.page.evaluate(
            "[...new Set(calls.map(c=>c.type))].sort()"
        ), ["dtek_monitor/account_identity", "dtek_monitor/accounts"])

    async def test_tabs_and_updates_keep_input_node_focus_and_draft(self):
        field = await self.readings()
        await field.fill("12503")
        await self.page.evaluate("""() => {
          window.input=card._readings.shadowRoot.querySelector('.fields input');input.setSelectionRange(2,4);
          for(let i=0;i<15;i++)card.hass={...hass,states:{...hass.states}};
        }""")
        self.assertEqual(await field.input_value(), "12503")
        self.assertTrue(await self.page.evaluate("input===card._readings.shadowRoot.activeElement"))
        self.assertEqual(await self.page.evaluate("[input.selectionStart,input.selectionEnd]"), [2, 4])
        await self.page.get_by_role("tab", name="Consumption", exact=True).click()
        await self.page.get_by_role("tab", name="Readings", exact=True).click()
        self.assertEqual(await field.input_value(), "12503")
        self.assertTrue(await self.page.evaluate("input===card._readings.shadowRoot.querySelector('.fields input')"))
        self.assertEqual(await self.page.evaluate("calls.filter(c=>c.action==='context').length"), 1)
        self.assertEqual(await self.page.evaluate("calls.filter(c=>c.action==='confirm').length"), 0)

    async def test_overview_shortcuts_keep_keyboard_focus_and_existing_draft(self):
        action = self.page.get_by_role("button", name="Enter readings", exact=True)
        await action.focus()
        await self.page.evaluate("""() => {
          for(let i=0;i<15;i++)card.hass={...hass,states:{...hass.states}};
        }""")
        self.assertTrue(await action.evaluate("e=>e===e.getRootNode().activeElement"))
        await action.press("Enter")
        field = self.page.get_by_label("Single zone (kWh)", exact=True)
        await field.fill("12503")
        await self.page.get_by_role("tab", name="Overview", exact=True).click()
        await self.page.get_by_role("button", name="View schedule", exact=True).click()
        self.assertEqual(await self.page.get_by_role("tab", name="Schedule", exact=True).get_attribute("aria-selected"), "true")
        await self.page.get_by_role("tab", name="Overview", exact=True).click()
        await action.click()
        self.assertEqual(await field.input_value(), "12503")
        self.assertEqual(await self.page.evaluate("calls.filter(c=>c.action==='confirm').length"), 0)

    async def test_account_switch_warns_discards_and_does_not_change_other_card(self):
        await self.page.evaluate("""() => {
          window.other=document.createElement('dtek-account-workspace-card');other.setConfig({entity:'sensor.account_1'});other.hass=hass;document.querySelector('main').append(other);
        }""")
        scope = self.page.locator("dtek-account-workspace-card").first
        await scope.get_by_role("tab", name="Readings", exact=True).click()
        await scope.get_by_label("Single zone (kWh)", exact=True).fill("12503")
        await scope.get_by_label("Personal account", exact=True).select_option("sensor.account_2")
        await scope.get_by_role("button", name="Keep editing", exact=True).click()
        self.assertEqual(await scope.get_by_label("Single zone (kWh)", exact=True).input_value(), "12503")
        await scope.get_by_label("Personal account", exact=True).select_option("sensor.account_2")
        await scope.get_by_role("button", name="Discard and switch", exact=True).click()
        self.assertEqual(await self.page.evaluate("other._entity"), "sensor.account_1")
        await scope.get_by_role("tab", name="Readings", exact=True).click()
        self.assertEqual(await scope.get_by_label("Single zone (kWh)", exact=True).input_value(), "")
        self.assertEqual(await self.page.evaluate("card._readings._context.serial"), "DEMO-2")

    async def test_pending_send_blocks_switch_and_never_retries_on_navigation(self):
        field = await self.readings()
        await field.fill("12500")
        await self.page.get_by_role("button", name="Review readings", exact=True).click()
        await self.page.get_by_role("checkbox").check()
        await self.page.evaluate("""() => {
          const normal=hass.callWS;hass.callWS=m=>m.action==='confirm'?(calls.push(m),new Promise(resolve=>window.finish=resolve)):normal(m);
        }""")
        await self.page.get_by_role("button", name="Confirm and send to DTEK", exact=True).click()
        await self.page.get_by_label("Personal account", exact=True).select_option("sensor.account_2")
        self.assertEqual(await self.page.evaluate("card._entity"), "sensor.account_1")
        await self.page.get_by_text("Wait for the current operation", exact=False).wait_for()
        await self.page.get_by_role("tab", name="Overview", exact=True).click()
        await self.page.evaluate("finish({status:'unknown'})")
        await self.page.get_by_role("tab", name="Readings", exact=True).click()
        await self.page.get_by_text("The result is not confirmed.", exact=False).wait_for()
        self.assertEqual(await self.page.evaluate("calls.filter(c=>c.action==='confirm').length"), 1)
        self.assertEqual(await self.page.get_by_role("button", name="Confirm and send to DTEK", exact=True).count(), 0)

    async def test_history_has_one_location_and_profile_never_requests_it(self):
        await self.page.get_by_role("button", name="Account and meter details", exact=True).click()
        await self.page.get_by_text("Лічильник прикладу", exact=True).wait_for()
        self.assertEqual(await self.page.evaluate("calls.filter(c=>/account_(history|readback)$/.test(c.type)).length"), 0)
        await self.readings()
        await self.page.get_by_text("Reading history", exact=True).click()
        await self.page.locator("#panel-readings table.readings").wait_for()
        self.assertEqual(await self.page.locator("#panel-readings svg").count(), 0)
        await self.page.get_by_role("tab", name="Consumption", exact=True).click()
        await self.page.locator("#panel-consumption svg").wait_for()
        self.assertEqual(await self.page.locator("#panel-consumption table.readings").count(), 0)

    async def test_read_only_user_never_opens_submission_or_energy_commands(self):
        await self.page.evaluate("""() => {
          accounts.forEach(a=>a.can_submit=false);hass={...hass,user:{id:'viewer',is_admin:false}};card.hass=hass;
        }""")
        await self.page.get_by_text("You have viewing access.", exact=False).wait_for()
        await self.page.get_by_role("tab", name="Readings", exact=True).click()
        await self.page.get_by_text("You can view readings and history.", exact=False).wait_for()
        self.assertEqual(await self.page.evaluate("calls.filter(c=>['dtek_monitor/account_submission','dtek_monitor/account_energy'].includes(c.type)).length"), 0)
        self.assertEqual(await self.page.locator("input").count(), 0)

    async def test_unconfigured_multiple_accounts_require_a_choice(self):
        await self.page.evaluate("card.setConfig({show_account_selector:false})")
        self.assertIsNone(await self.page.evaluate("card._account"))
        self.assertEqual(await self.page.get_by_label("Personal account", exact=True).input_value(), "")
        await self.page.get_by_label("Personal account", exact=True).select_option("sensor.account_2")
        await self.page.get_by_role("tab", name="Overview", exact=True).wait_for()
        self.assertEqual(await self.page.evaluate("card._entity"), "sensor.account_2")

    async def test_legacy_resource_url_does_not_duplicate_card_picker(self):
        await self.page.add_script_tag(url="http://dtek-card.test/account-readings-card.js?v=legacy", type="module")
        await self.page.add_script_tag(url="http://dtek-card.test/account-history-card.js?v=legacy", type="module")
        self.assertTrue(await self.page.evaluate("new Set(customCards.map(c=>c.type)).size===customCards.length"))

    async def test_old_loaded_component_requires_reload_before_showing_new_forms(self):
        await self.page.evaluate("""() => {
          Object.defineProperty(customElements.get('dtek-account-readings-card'),'workspaceVersion',{value:0});
          card._applyAccounts();
        }""")
        await self.page.get_by_role("button", name="Reload page", exact=True).wait_for()
        self.assertEqual(await self.page.get_by_role("tab", name="Readings", exact=True).count(), 0)
        self.assertEqual(await self.page.evaluate("calls.filter(c=>c.type==='dtek_monitor/account_submission').length"), 0)

    async def test_mobile_dark_theme_keyboard_and_no_page_overflow(self):
        for width in [320, 390, 1100]:
            await self.page.set_viewport_size({"width": width, "height": 900})
            await self.page.evaluate("document.body.style.cssText='--primary-text-color:#edf2f5;--card-background-color:#20272c;--secondary-background-color:#30383e;--secondary-text-color:#bbc5ce;--divider-color:#52616e' ")
            await self.page.get_by_role("tab", name="Overview", exact=True).click()
            self.assertTrue(await self.page.evaluate("document.documentElement.scrollWidth<=innerWidth"))
            await self.page.get_by_role("tab", name="Overview", exact=True).press("ArrowRight")
            self.assertEqual(await self.page.get_by_role("tab", name="Schedule", exact=True).get_attribute("aria-selected"), "true")
            await self.readings()
            await self.page.get_by_text("Reading history", exact=True).click()
            self.assertTrue(await self.page.evaluate("document.documentElement.scrollWidth<=innerWidth"))

    async def test_empty_monitoring_offers_management_without_forcing_new_login(self):
        await self.page.evaluate("""() => {
          accounts.splice(0);card.setConfig({});card._accounts=null;card._loadAccounts();
        }""")
        link = self.page.get_by_role("link", name="Manage connections and notifications", exact=True)
        await link.wait_for()
        self.assertEqual(await link.get_attribute("href"), "/config/integrations/integration/dtek_monitor")
        self.assertEqual(await self.page.get_by_role("tab", name="Readings", exact=True).count(), 0)

    async def test_schedule_opens_one_operator_grid_with_home_and_personal_highlights(self):
        await self.page.get_by_role("tab", name="Schedule", exact=True).click()
        await self.page.locator("dtek-schedule-grid tbody tr").first.wait_for()
        self.assertEqual(await self.page.locator("dtek-schedule-grid tbody tr").count(), 12)
        self.assertEqual(await self.page.locator("dtek-schedule-grid tr.home").count(), 2)
        self.assertEqual(await self.page.locator("dtek-schedule-grid tr.selected").count(), 1)
        self.assertEqual(await self.page.get_by_role("button", name="All operator groups", exact=True).get_attribute("aria-pressed"), "true")
