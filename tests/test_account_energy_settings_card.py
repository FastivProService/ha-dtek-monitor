"""Separate settings: local calibration, draft isolation and source invalidation."""

import unittest
import test_account_workspace_card as workspace


@unittest.skipUnless(workspace.HAS_PLAYWRIGHT, "Requires Playwright Chromium")
class EnergySettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workspace.WorkspaceTests.asyncSetUp(self)
        await self.page.evaluate("""() => {
          window.energyBinding={serial:'DEMO-1',mode:'offset',freshness:'reported',revision:'binding-1',history:[],max_age:900,max_power_kw:'15',zones:{GENERAL:{entity_id:'sensor.grid',anchor_meter:'12400',anchor_source:'2000'}}};
          window.sourceData=()=>({binding:structuredClone(energyBinding),ready:!!energyBinding,proposal:energyBinding?.revision,readings:{GENERAL:{value:'12500.5',send_value:'12500',source:{value:'2100.5',at:'2026-10-18T12:00:00+03:00'}}}});
          window.holdSave=false;
          const previous=hass.callWS;
          hass.callWS=async msg=>{
            if(msg.type!=='dtek_monitor/account_energy')return previous(msg);
            calls.push(structuredClone(msg));
            if(msg.action==='get')return sourceData();
            if(msg.action==='preview')return {confirmation:'reference-review',binding:energyBinding,change:{changes:{GENERAL:{source:'2100.5',source_at:'2026-10-18T12:00:00+03:00',actual:msg.references.GENERAL,correction:'4.5'}}}};
            if(msg.action==='save'){if(holdSave)await new Promise(resolve=>window.finishSave=resolve);energyBinding.revision='binding-2';return {saved:true};}
            if(msg.action==='remove'){energyBinding=null;return {removed:true};}
          };
        }""")

    async def asyncTearDown(self):
        await workspace.WorkspaceTests.asyncTearDown(self)

    async def settings(self):
        await self.page.get_by_role("tab", name="Settings", exact=True).click()
        summary = self.page.get_by_text("Home energy sensor", exact=True)
        if not await summary.evaluate("e=>e.parentElement.open"):
            await summary.click()
        editor = self.page.locator("dtek-energy-settings dtek-energy-editor")
        await editor.get_by_role(
            "button", name="Replace binding", exact=True
        ).wait_for()
        return editor

    async def calibration(self):
        editor = await self.settings()
        await editor.get_by_text(
            "Calibrate against the official meter", exact=True
        ).click()
        field = editor.get_by_label(
            "Single zone · Actual official meter reading (kWh)", exact=True
        )
        await field.fill("12505")
        return editor, field

    async def save_review(self, editor):
        await editor.get_by_role(
            "button", name="Review new reference pair", exact=True
        ).click()
        await editor.get_by_role("checkbox").check()
        await editor.get_by_role(
            "button", name="Save local reference", exact=True
        ).click()

    async def test_local_calibration_stays_in_settings_without_opening_submission(self):
        editor, field = await self.calibration()
        self.assertIsNone(await self.page.evaluate("card._readings"))
        self.assertEqual(
            await editor.get_by_role(
                "button", name="Use proposed readings in the form"
            ).count(),
            0,
        )
        await self.save_review(editor)
        await editor.get_by_text("Local reference saved.", exact=False).wait_for()
        self.assertEqual(
            await self.page.get_by_role(
                "tab", name="Settings", exact=True
            ).get_attribute("aria-selected"),
            "true",
        )
        self.assertEqual(
            await self.page.evaluate(
                "calls.filter(x=>x.type==='dtek_monitor/account_submission'&&x.action!=='context').length"
            ),
            0,
        )

    async def test_settings_draft_focus_tabs_and_account_discard(self):
        editor, field = await self.calibration()
        await field.focus()
        await self.page.evaluate(
            """() => {for(let i=0;i<20;i++)card.hass={...hass,states:{...hass.states}};}"""
        )
        self.assertTrue(await field.evaluate("e=>e===e.getRootNode().activeElement"))
        await self.page.get_by_role("tab", name="Overview", exact=True).click()
        await self.page.get_by_role("tab", name="Settings", exact=True).click()
        self.assertEqual(await field.input_value(), "12505")
        await self.page.get_by_label("Personal account", exact=True).select_option(
            "sensor.account_2"
        )
        await self.page.get_by_role("button", name="Keep editing", exact=True).click()
        self.assertEqual(await field.input_value(), "12505")
        await self.page.get_by_label("Personal account", exact=True).select_option(
            "sensor.account_2"
        )
        await self.page.get_by_role(
            "button", name="Discard and switch", exact=True
        ).click()
        self.assertEqual(await self.page.locator("dtek-energy-settings").count(), 0)
        self.assertEqual(await self.page.evaluate("card._entity"), "sensor.account_2")

    async def test_advanced_controls_keep_values_across_mode_and_ha_updates(self):
        editor = await self.settings()
        await editor.get_by_role("button", name="Replace binding", exact=True).click()
        power = editor.get_by_label("Maximum plausible input power (kW)", exact=True)
        self.assertFalse(await power.is_visible())
        await editor.get_by_text("Advanced settings", exact=True).click()
        await power.fill("9")
        await editor.get_by_label(
            "Source availability check", exact=True
        ).select_option("availability")
        await editor.get_by_label("Reading mode", exact=True).select_option("direct")
        self.assertEqual(await power.input_value(), "9")
        self.assertEqual(
            await editor.get_by_label(
                "Source availability check", exact=True
            ).input_value(),
            "availability",
        )
        self.assertEqual(
            await editor.get_by_label(
                "Single zone · Actual official meter reading (kWh)", exact=True
            ).count(),
            0,
        )
        await self.page.evaluate("card.hass={...hass,states:{...hass.states}}")
        self.assertEqual(await power.input_value(), "9")

    async def test_binding_change_invalidates_proposal_but_preserves_entered_values(
        self,
    ):
        field = await workspace.WorkspaceTests.readings(self)
        await self.page.get_by_role("button", name="Use suggestion", exact=True).click()
        self.assertEqual(await field.input_value(), "12500")
        await self.page.get_by_role(
            "button", name="Sensor settings", exact=True
        ).click()
        editor = self.page.locator("dtek-energy-settings dtek-energy-editor")
        await editor.get_by_text(
            "Calibrate against the official meter", exact=True
        ).click()
        await editor.get_by_label(
            "Single zone · Actual official meter reading (kWh)", exact=True
        ).fill("12505")
        await self.save_review(editor)
        await editor.get_by_text("Local reference saved.", exact=False).wait_for()
        await self.page.get_by_role("tab", name="Readings", exact=True).click()
        self.assertEqual(await field.input_value(), "12500")
        self.assertTrue(
            await self.page.get_by_role(
                "button", name="Review readings", exact=True
            ).is_disabled()
        )
        await self.page.get_by_role(
            "button", name="Refresh suggestion", exact=True
        ).click()
        await self.page.get_by_role("button", name="Use suggestion", exact=True).click()
        self.assertFalse(
            await self.page.get_by_role(
                "button", name="Review readings", exact=True
            ).is_disabled()
        )
        self.assertEqual(
            await self.page.evaluate("card._readings._sourceToken"), "binding-2"
        )

    async def test_pending_save_blocks_account_switch_and_reading_review(self):
        field = await workspace.WorkspaceTests.readings(self)
        await field.fill("12505")
        editor, _ = await self.calibration()
        await self.page.evaluate("holdSave=true")
        await self.save_review(editor)
        await self.page.get_by_label("Personal account", exact=True).select_option(
            "sensor.account_2"
        )
        self.assertEqual(await self.page.evaluate("card._entity"), "sensor.account_1")
        await self.page.get_by_role("tab", name="Readings", exact=True).click()
        self.assertTrue(
            await self.page.get_by_role(
                "button", name="Review readings", exact=True
            ).is_disabled()
        )
        await self.page.evaluate("finishSave()")
        await self.page.get_by_role(
            "button", name="Review readings", exact=True
        ).wait_for()
        await self.page.wait_for_function("!card._energySettings.busy")
        self.assertEqual(await field.input_value(), "12505")
        self.assertFalse(
            await self.page.get_by_role(
                "button", name="Review readings", exact=True
            ).is_disabled()
        )

    async def test_existing_reference_calibrates_when_dtek_is_offline(self):
        await self.page.evaluate(
            """() => {const old=hass.callWS;hass.callWS=m=>{if(m.type==='dtek_monitor/account_submission')throw {code:'unavailable'};return old(m);};} """
        )
        editor, _ = await self.calibration()
        await self.page.get_by_text(
            "Could not load the DTEK meter.", exact=False
        ).wait_for()
        await self.save_review(editor)
        await editor.get_by_text("Local reference saved.", exact=False).wait_for()

    async def test_first_binding_collects_each_tariff_zone_and_reviews_before_save(
        self,
    ):
        await self.page.evaluate("""() => {
          energyBinding=null;const old=hass.callWS;
          hass.callWS=async m=>{const r=await old(m);if(m.type==='dtek_monitor/account_submission'&&m.action==='context')r.zones=[{zone:'DAY'},{zone:'NIGHT'}];return r;};
        }""")
        await self.page.get_by_role("tab", name="Settings", exact=True).click()
        await self.page.get_by_text("Home energy sensor", exact=True).click()
        editor = self.page.locator("dtek-energy-settings dtek-energy-editor")
        await editor.get_by_role(
            "button", name="Link an energy sensor", exact=True
        ).click()
        for zone in ("Day", "Night"):
            await editor.get_by_label(
                zone + " · Cumulative energy sensor", exact=True
            ).fill("sensor.grid")
            field = editor.get_by_label(
                zone + " · Actual official meter reading (kWh)", exact=True
            )
            self.assertEqual(await field.input_value(), "")
            await field.fill("1000" if zone == "Day" else "250")
        self.assertFalse(
            await editor.get_by_label(
                "Maximum data age (minutes)", exact=True
            ).is_visible()
        )
        self.assertEqual(
            await self.page.evaluate(
                "calls.filter(x=>['save','confirm','preview'].includes(x.action)).length"
            ),
            0,
        )
        await editor.get_by_role("button", name="Cancel", exact=True).click()
        self.assertFalse(await self.page.evaluate("card._energySettings.hasDraft"))

    async def test_submission_in_progress_locks_calibration(self):
        editor, _ = await self.calibration()
        await self.page.get_by_role("tab", name="Readings", exact=True).click()
        await self.page.get_by_label("Single zone (kWh)", exact=True).fill("12505")
        await self.page.get_by_role(
            "button", name="Review readings", exact=True
        ).click()
        await self.page.get_by_role("checkbox").check()
        await self.page.evaluate(
            """() => {const old=hass.callWS;hass.callWS=m=>m.type==='dtek_monitor/account_submission'&&m.action==='confirm'?new Promise(resolve=>window.finishSubmission=resolve):old(m);} """
        )
        await self.page.get_by_role(
            "button", name="Confirm and send to DTEK", exact=True
        ).click()
        await self.page.get_by_role("tab", name="Settings", exact=True).click()
        self.assertTrue(
            await editor.get_by_role(
                "button", name="Review new reference pair", exact=True
            ).is_disabled()
        )
        await self.page.evaluate("finishSubmission({status:'accepted'})")
        await self.page.wait_for_function("!card._readings.busy")
        self.assertFalse(
            await editor.get_by_role(
                "button", name="Review new reference pair", exact=True
            ).is_disabled()
        )

    async def test_late_meter_response_cannot_restore_old_account_settings(self):
        await self.page.evaluate(
            """() => {const old=hass.callWS;hass.callWS=m=>m.type==='dtek_monitor/account_submission'?new Promise(resolve=>window.oldMeter=resolve):old(m);} """
        )
        await self.page.get_by_role("tab", name="Settings", exact=True).click()
        await self.page.get_by_text("Home energy sensor", exact=True).click()
        await self.page.wait_for_function('typeof oldMeter==="function"')
        await self.page.evaluate(
            "card.setConfig({entity:'sensor.account_2'});oldMeter({serial:'OLD-METER',zones:[]})"
        )
        self.assertEqual(await self.page.evaluate("card._entity"), "sensor.account_2")
        self.assertEqual(await self.page.locator("dtek-energy-settings").count(), 0)
        self.assertNotIn("OLD-METER", await self.page.locator("body").inner_text())
