"""Schedule UI distinguishes empty publication from unavailable or failed data."""

import importlib.util
from pathlib import Path
import unittest
from card_browser import install_card

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
if HAS_PLAYWRIGHT:
    from playwright.async_api import async_playwright
SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "custom_components/dtek_monitor/frontend/schedule-card.js"
)


@unittest.skipUnless(HAS_PLAYWRIGHT, "Requires Playwright Chromium")
class ScheduleCardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.p = await async_playwright().start()
        self.browser = await self.p.chromium.launch(headless=True)
        self.page = await self.browser.new_page(viewport={"width": 390, "height": 844})
        await install_card(self.page, SCRIPT)
        await self.page.evaluate("""() => {
          window.created=[];
          window.loadCardHelpers=async()=>({createCardElement:config=>{
            created.push(config); const element=document.createElement('div');element.className='test-calendar';element.textContent='Calendar';return element;
          }});
          window.hass={language:'en',states:{'sensor.status':{state:'missing',attributes:{status_date:'2026-10-18'}},'calendar.building':{state:'unavailable'}}};
          window.card=document.createElement('dtek-schedule-card');
          card.setConfig({status_entity:'sensor.status',entities:['calendar.building'],title:'Building — all groups',view:'list'});
          document.querySelector('main').append(card);card.hass=hass;
        }""")

    async def asyncTearDown(self):
        await self.browser.close()
        await self.p.stop()

    async def update(self, status, calendar="unavailable"):
        await self.page.evaluate(
            """([status,calendar])=>{
          hass.states['sensor.status']={state:status,attributes:{status_date:'2026-10-18'}};
          hass.states['calendar.building']={state:calendar};card.hass={...hass};
        }""",
            [status, calendar],
        )

    async def test_missing_unpublished_and_error_are_distinct_from_empty(self):
        for status, text in [
            ("missing", "DTEK responded"),
            ("unpublished", "paused"),
            ("error", "Could not fetch"),
            ("forecast", "Only a forecast"),
        ]:
            await self.update(status)
            self.assertIn(text, await self.page.locator(".status").inner_text())
            self.assertEqual(await self.page.locator(".test-calendar").count(), 0)
            self.assertEqual(
                await self.page.locator(".status.error").count(),
                1 if status == "error" else 0,
            )
        await self.update("empty", "off")
        await self.page.locator(".test-calendar").wait_for()
        self.assertIn(
            "No planned outages today", await self.page.locator(".status").inner_text()
        )
        self.assertEqual(await self.page.evaluate("created.length"), 1)

    async def test_late_calendar_helper_cannot_resurrect_failed_schedule(self):
        await self.page.evaluate(
            "() => {window.loadCardHelpers=()=>new Promise(resolve=>window.finish=resolve);}"
        )
        await self.update("current", "off")
        await self.update("error")
        await self.page.evaluate(
            'finish({createCardElement:()=>document.createElement("div")})'
        )
        self.assertEqual(await self.page.locator(".calendar").inner_text(), "")

    async def test_cabinet_selection_source_and_loss_are_explicit(self):
        await self.update("cabinet_selected", "off")
        await self.page.evaluate("hass.states['sensor.status'].attributes.selection_source='cabinet';card.hass={...hass}")
        await self.page.locator(".test-calendar").wait_for()
        self.assertIn("Group identified from your DTEK personal account", await self.page.locator(".status").inner_text())
        await self.update("cabinet_unavailable", "off")
        self.assertIn("could not verify", await self.page.locator(".status").inner_text())
        self.assertEqual(await self.page.locator(".test-calendar").count(), 0)
        await self.update("cabinet_group_mismatch", "off")
        self.assertIn("does not match", await self.page.locator(".status").inner_text())

    async def test_updates_keep_calendar_instance_and_separate_personal_selection(self):
        await self.update("current", "off")
        await self.page.locator(".test-calendar").wait_for()
        for _ in range(3):
            await self.update("current", "off")
        self.assertEqual(await self.page.evaluate("created.length"), 1)
        await self.update("not_selected", "off")
        self.assertIn("Choose yours", await self.page.locator(".status").inner_text())
        self.assertEqual(await self.page.locator(".test-calendar").count(), 0)
        await self.update("schedule_empty", "off")
        await self.page.locator(".test-calendar").wait_for()
        self.assertIn(
            "No planned outages today", await self.page.locator(".status").inner_text()
        )

    async def test_ukrainian_error_and_mobile_long_title_do_not_overflow(self):
        await self.page.evaluate(
            'hass.language="uk";card.setConfig({...card._config,title:"<img src=x onerror=alert(1)>".repeat(3)});card.hass=hass'
        )
        await self.update("missing")
        self.assertIn(
            "ДТЕК відповідає", await self.page.locator(".status").inner_text()
        )
        self.assertEqual(await self.page.locator("img").count(), 0)
        self.assertLessEqual(
            await self.page.evaluate("document.documentElement.scrollWidth"), 390
        )

    async def test_frontend_load_failure_is_not_mislabeled_as_dtek_failure(self):
        await self.page.evaluate(
            '() => {window.loadCardHelpers=async()=>{throw new Error("network") };}'
        )
        await self.update("current", "off")
        await self.page.get_by_text(
            "Could not open the dashboard calendar. Reload the page."
        ).wait_for()
        self.assertIn(
            "Today's schedule", await self.page.locator(".status").first.inner_text()
        )
