"""Browser regression tests for history rendering and asynchronous year selection."""

import importlib.util
import unittest
from pathlib import Path
from card_browser import install_card

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
if HAS_PLAYWRIGHT:
    from playwright.async_api import async_playwright

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "custom_components/dtek_monitor/frontend/account-history-card.js"
)


@unittest.skipUnless(HAS_PLAYWRIGHT, "Requires Playwright Chromium")
class HistoryCardTests(unittest.IsolatedAsyncioTestCase):
    async def test_photo_marker_and_controller_comment_are_safe_text(self):
        await self.page.evaluate("""() => {
          const old=result;result=year=>{const data=old(year);
            data.readings[1].has_photo=true;
            data.readings[1].controller_note='<img src=x onerror="window.injected=true">';return data;};
        }""")
        await self.load("uk")
        await self.page.get_by_text("📷 ДТЕК має фото", exact=True).wait_for()
        await self.page.get_by_text('Коментар контролера: <img src=x onerror="window.injected=true">', exact=True).wait_for()
        self.assertEqual(await self.page.locator("img").count(), 0)
        self.assertIsNone(await self.page.evaluate("window.injected"))

    async def test_consumption_failure_keeps_readings_without_a_false_empty_chart(self):
        await self.page.evaluate("""() => {
          const old=result;result=year=>({...old(year),months:[],unavailable:['meter_consumption'],comparison_unavailable:true});
        }""")
        await self.load()
        self.assertEqual(await self.page.locator('svg[aria-label="Monthly consumption, kWh"]').count(), 0)
        await self.page.get_by_text("Monthly consumption is temporarily unavailable. Reading history remains available.", exact=True).wait_for()
        await self.page.get_by_text("Comparison is unavailable because consumption could not be loaded for one of the years.", exact=True).wait_for()
        self.assertGreater(await self.page.locator("table.readings tbody tr").count(), 0)

    async def test_api_change_directs_to_repairs_without_exposing_raw_error(self):
        await self.load("uk")
        await self.page.evaluate("() => {hass.callWS=async()=>{throw {code:'api_changed',message:'PRIVATE_TOKEN'}};}")
        await self.page.get_by_role("button", name="Оновити історію", exact=True).click()
        await self.page.locator("p.error[role=alert]").wait_for()
        message = await self.page.locator("p.error[role=alert]").inner_text()
        self.assertIn("Виправлення", message)
        self.assertNotIn("PRIVATE_TOKEN", message)

    async def test_modern_tariff_codes_have_readable_history_and_consumption_labels(self):
        await self.page.evaluate("""() => {
          const old=result;result=year=>{const data=old(year);
            data.readings.forEach((row,i)=>row.zone=i%2?'DAY_2_ZONE':'NIGHT_2_ZONE');
            data.months[0].zones={DAY_2_ZONE:'15',NIGHT_2_ZONE:'3'};return data;};
        }""")
        await self.load("uk")
        self.assertGreater(await self.page.get_by_text("День", exact=True).count(), 0)
        self.assertGreater(await self.page.get_by_text("Ніч", exact=True).count(), 0)
        await self.page.locator("summary").click()
        await self.page.get_by_text("День: 15; Ніч: 3", exact=True).wait_for()
        self.assertNotIn("_2_ZONE", await self.page.locator("main").inner_text())

    async def asyncSetUp(self):
        self.p = await async_playwright().start()
        self.browser = await self.p.chromium.launch(headless=True)
        self.page = await self.browser.new_page(
            viewport={"width": 430, "height": 900}, timezone_id="America/Los_Angeles"
        )
        await install_card(self.page, SCRIPT)
        await self.page.evaluate("""() => {
          window.result = (year) => ({year, fetched_at:`${year}-10-18T13:00:00+03:00`, months:Array.from({length:12},(_,i)=>({period:`${year}-${String(i+1).padStart(2,'0')}`,status:i<2?'closed':i===9?'open':'missing',value:i===0?'-12':i===1?'0':null,zones:i===0?{DAY:'-15',NIGHT:'3'}:{}})),readings:Array.from({length:12},(_,i)=>({date_time:`${year}-09-30T23:59:00+03:00`,value:String(100+i),zone:'GENERAL',energy:'ACTIVE',source:i===0?'<img src=x onerror="window.injected=true">':'Test',status:i===0?'inactive':'active'}))});
          window.requests=[];window.pending=[];
          window.card=document.createElement('dtek-account-history-card');
          card.setConfig({entity:'sensor.example',year:2026});
          document.querySelector('main').append(card);
        }""")

    async def asyncTearDown(self):
        await self.browser.close()
        await self.p.stop()

    async def load(self, language="en"):
        await self.page.evaluate(
            """language => { window.hass={language,callWS:async msg=>{if(msg.type==='dtek_monitor/account_identity')return {masked:'••••0001'};requests.push(msg);return result(msg.year);}};card.hass=hass; }""",
            language,
        )
        (
            await self.page.get_by_text("Meter readings · 11", exact=True).wait_for()
            if language == "en"
            else await self.page.get_by_text(
                "Покази лічильника · 11", exact=True
            ).wait_for()
        )

    async def test_negative_zero_gaps_zone_table_and_html_are_rendered_safely(self):
        await self.load()
        self.assertEqual(await self.page.locator("rect.bar").count(), 2)
        self.assertEqual(await self.page.locator("rect.negative").count(), 1)
        self.assertEqual(await self.page.get_by_text("Inactive", exact=True).count(), 0)
        await self.page.get_by_label("Show all DTEK records", exact=True).check()
        self.assertEqual(await self.page.locator("img").count(), 0)
        self.assertIsNone(await self.page.evaluate("window.injected"))
        self.assertEqual(await self.page.get_by_text("Inactive", exact=True).count(), 1)
        await self.page.locator("summary").click()
        self.assertEqual(
            await self.page.get_by_text("Day: -15; Night: 3", exact=True).count(), 1
        )
        await self.page.get_by_role("button", name="Next", exact=True).click()
        self.assertEqual(
            await self.page.get_by_text("Page 2 / 2", exact=True).count(), 1
        )
        self.assertEqual(await self.page.evaluate("requests.length"), 1)

    async def test_comparison_is_on_demand_and_year_changes_reset_it(self):
        await self.load()
        await self.page.evaluate(
            """() => {const base=hass.callWS;hass.callWS=async msg=>{const data=await base(msg);if(msg.compare)data.comparison={year:msg.year,previous_year:msg.year-1,fetched_at:{current:data.fetched_at,previous:'2026-10-18T11:00:00+03:00'},months:[{month:1,current:'-12',previous:'10',difference:'-22',comparable:true}],periods:[{period:'year',months:[1],complete:false,current:'-12',previous:'10',difference:'-22'}]};return data;};card.hass=hass;}"""
        )
        self.assertFalse(await self.page.evaluate("requests.some(m=>m.compare)"))
        await self.page.get_by_role(
            "button", name="Compare with previous year", exact=True
        ).click()
        await self.page.get_by_text(
            "Consumption for matching periods · 2026 / 2025", exact=True
        ).wait_for()
        self.assertEqual(
            await self.page.get_by_text("Partial: 01", exact=True).count(), 1
        )
        await self.page.get_by_label("Year", exact=True).select_option("2025")
        await self.page.get_by_text("Monthly consumption · 2025", exact=True).wait_for()
        self.assertEqual(
            await self.page.get_by_text(
                "Consumption for matching periods", exact=False
            ).count(),
            0,
        )
        self.assertFalse(await self.page.evaluate("requests.at(-1).compare"))

    async def test_filter_preserves_warnings_values_and_user_choice_during_refresh(self):
        await self.load("uk")
        await self.page.evaluate("""() => {
          card._data.readings[1].status='blocked';card._data.readings[2].status='unknown';
          card._data.readings[2].value=card._data.readings[1].value;card._render();
        }""")
        self.assertEqual(await self.page.locator('table.readings tr.blocked').count(), 1)
        self.assertEqual(await self.page.locator('table.readings tr.unknown').count(), 1)
        self.assertEqual(await self.page.locator('table.readings tr.inactive').count(), 0)
        await self.page.get_by_label("Показати всі записи ДТЕК", exact=True).check()
        await self.page.get_by_role("button", name="Далі", exact=True).click()
        for _ in range(3):
            await self.page.evaluate("card.hass={...hass,states:{changed:Math.random()}}")
        self.assertEqual(await self.page.get_by_text("Сторінка 2 / 2", exact=True).count(), 1)
        self.assertTrue(await self.page.get_by_label("Показати всі записи ДТЕК", exact=True).is_checked())
        await self.page.evaluate("""() => {const base=hass.callWS;hass.callWS=msg=>msg.type==='dtek_monitor/account_history'?new Promise(resolve=>pending.push({msg,resolve})):base(msg);card.hass=hass;} """)
        await self.page.get_by_role("button", name="Оновити історію", exact=True).click()
        await self.page.get_by_label("Показати всі записи ДТЕК", exact=True).uncheck()
        await self.page.evaluate("pending[0].resolve(result(2026))")
        await self.page.get_by_role("button", name="Оновити історію", exact=True).wait_for()
        self.assertFalse(await self.page.get_by_label("Показати всі записи ДТЕК", exact=True).is_checked())
        self.assertEqual(await self.page.locator('table.readings tr.inactive').count(), 0)
        self.assertEqual(await self.page.get_by_text("Сторінка 1 / 2", exact=True).count(), 1)
        self.assertEqual(await self.page.evaluate("requests.length"), 1)  # Toggle never fetches.

    async def test_all_inactive_is_explained_and_available_without_refetch(self):
        await self.load()
        await self.page.evaluate("card._data.readings.forEach(r=>r.status='inactive');card._render()")
        self.assertEqual(await self.page.get_by_text("Only inactive records are available.", exact=False).count(), 1)
        self.assertEqual(await self.page.get_by_text("No readings returned for this year.", exact=True).count(), 0)
        await self.page.get_by_label("Show all DTEK records", exact=True).check()
        await self.page.get_by_role("button", name="Next", exact=True).click()
        self.assertEqual(await self.page.locator('table.readings tbody tr').count(), 2)
        self.assertEqual(await self.page.evaluate("requests.length"), 1)
        await self.page.get_by_label("Show all DTEK records", exact=True).uncheck()
        self.assertEqual(await self.page.locator('table.readings').count(), 0)

    async def test_late_response_cannot_overwrite_user_year_choice(self):
        await self.page.evaluate(
            """() => {card.hass={language:'en',callWS:msg=>msg.type==='dtek_monitor/account_identity'?Promise.resolve({masked:'••••0001'}):new Promise(resolve=>{requests.push(msg);pending.push({msg,resolve});})};}"""
        )
        await self.page.get_by_label("Year", exact=True).select_option("2025")
        await self.page.evaluate("pending[1].resolve(result(2025))")
        await self.page.get_by_text("Monthly consumption · 2025", exact=True).wait_for()
        await self.page.evaluate("pending[0].resolve(result(2026))")
        self.assertEqual(
            await self.page.get_by_label("Year", exact=True).input_value(), "2025"
        )
        self.assertEqual(
            await self.page.get_by_text(
                "Monthly consumption · 2026", exact=True
            ).count(),
            0,
        )

    async def test_hass_state_updates_keep_selection_and_do_not_refetch(self):
        await self.load("uk")
        await self.page.get_by_label("Рік", exact=True).select_option("2025")
        await self.page.get_by_text("Місячне споживання · 2025", exact=True).wait_for()
        for _ in range(5):
            await self.page.evaluate(
                "card.hass={...hass,states:{changed:Math.random()}}"
            )
        self.assertEqual(
            await self.page.get_by_label("Рік", exact=True).input_value(), "2025"
        )
        self.assertEqual(await self.page.evaluate("requests.length"), 2)

    async def test_eye_does_not_reset_year_or_pagination(self):
        await self.load()
        await self.page.get_by_label("Year", exact=True).select_option("2025")
        await self.page.get_by_text("Monthly consumption · 2025", exact=True).wait_for()
        await self.page.get_by_role("button", name="Next", exact=True).click()
        await self.page.evaluate("""() => {
          const old=hass.callWS;
          hass.callWS=msg=>msg.type==='dtek_monitor/account_identity'
            ? Promise.resolve({masked:'••••0001',number:'1234500001'}) : old(msg);
        }""")
        await self.page.get_by_role(
            "button", name="Show account number", exact=True
        ).click()
        await self.page.get_by_text("Account number: 1234500001", exact=True).wait_for()
        await self.page.get_by_role(
            "button", name="Hide account number", exact=True
        ).click()
        self.assertEqual(
            await self.page.get_by_label("Year", exact=True).input_value(), "2025"
        )
        self.assertEqual(
            await self.page.get_by_text("Page 2 / 2", exact=True).count(), 1
        )
        self.assertEqual(await self.page.evaluate("requests.length"), 2)

    async def test_failed_force_refresh_marks_existing_data_and_denial_clears_it(self):
        await self.load()
        await self.page.evaluate(
            '() => {hass.callWS=async()=>{throw {code:"history_unavailable",message:"private upstream text"}};}'
        )
        await self.page.get_by_role(
            "button", name="Refresh history", exact=True
        ).click()
        await self.page.locator("p.error[role=alert]").wait_for()
        self.assertEqual(
            await self.page.get_by_text(
                "Monthly consumption · 2026", exact=True
            ).count(),
            1,
        )
        self.assertEqual(
            await self.page.get_by_text(
                "Refresh failed. The previously fetched result remains displayed with its original date.",
                exact=True,
            ).count(),
            1,
        )
        self.assertEqual(
            await self.page.get_by_text("private upstream text", exact=True).count(), 0
        )
        await self.page.evaluate(
            '() => {hass.callWS=async()=>{throw {code:"unauthorized"}};}'
        )
        await self.page.get_by_role(
            "button", name="Refresh history", exact=True
        ).click()
        self.assertEqual(
            await self.page.get_by_text(
                "Monthly consumption · 2026", exact=True
            ).count(),
            0,
        )

    async def test_disconnect_discards_pending_result_and_reconnect_loads_again(self):
        await self.page.evaluate(
            """() => {card.hass={language:'en',callWS:msg=>msg.type==='dtek_monitor/account_identity'?Promise.resolve({masked:'••••0001'}):new Promise(resolve=>pending.push({msg,resolve}))};card.remove();pending[0].resolve(result(2026));document.querySelector('main').append(card);}"""
        )
        self.assertEqual(await self.page.evaluate("pending.length"), 2)
        await self.page.evaluate("pending[1].resolve(result(2026))")
        await self.page.get_by_text("Meter readings · 11", exact=True).wait_for()
