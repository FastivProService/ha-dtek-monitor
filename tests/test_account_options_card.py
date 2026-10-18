"""Scoped settings dialogs retain edits and never submit on navigation/updates."""

import json
import unittest
import test_account_workspace_card as workspace


@unittest.skipUnless(workspace.HAS_PLAYWRIGHT, "Requires Playwright Chromium")
class OptionsCardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workspace.WorkspaceTests.asyncSetUp(self)
        translations = json.loads(
            (
                workspace.ROOT / "custom_components/dtek_monitor/translations/en.json"
            ).read_text()
        )
        await self.page.evaluate("(t)=>window.optionTranslations=t", translations)
        await self.page.evaluate(
            (workspace.FIXTURE.parent / "account_options.js").read_text()
        )

    async def asyncTearDown(self):
        await workspace.WorkspaceTests.asyncTearDown(self)

    async def open(self, name="Reading reminders"):
        await self.page.get_by_role("tab", name="Settings", exact=True).click()
        await self.page.get_by_role("button", name=name, exact=True).click()
        dialog = self.page.locator("dtek-account-options dialog")
        await dialog.get_by_role("button", name="Save", exact=True).wait_for()
        return dialog

    async def test_shortcuts_use_selected_account_without_opening_readings_or_saving(
        self,
    ):
        await self.page.get_by_label("Personal account", exact=True).select_option(
            "sensor.account_2"
        )
        for name, action in [
            ("Building and outage group", "group"),
            ("Reading reminders", "reminders"),
            ("Meter notifications", "notices"),
            ("Add or unlink an account", "accounts"),
            ("Choose accounts to monitor", "monitoring"),
        ]:
            dialog = await self.open(name)
            self.assertIn("••••0002", await dialog.inner_text())
            self.assertEqual(
                await self.page.evaluate(
                    "calls.filter(c=>c.type==='dtek_monitor/account_options').at(-1)"
                ),
                {
                    "type": "dtek_monitor/account_options",
                    "entity_id": "sensor.account_2",
                    "action": action,
                },
            )
            await dialog.get_by_role("button", name="Cancel", exact=True).click()
        self.assertEqual(await self.page.evaluate("optionPosts"), 0)
        self.assertIsNone(await self.page.evaluate("card._readings"))
        self.assertEqual(
            await self.page.evaluate("apiCalls.filter(c=>c.method==='DELETE').length"),
            5,
        )

    async def test_form_focus_values_and_dirty_cancel_survive_ha_updates(self):
        dialog = await self.open()
        field = dialog.get_by_label("Time (Europe/Kyiv)", exact=True)
        await field.fill("20:15:00")
        await field.focus()
        await self.page.evaluate(
            "for(let i=0;i<20;i++)card.hass={...hass,states:{...hass.states}}"
        )
        self.assertEqual(await field.input_value(), "20:15:00")
        self.assertTrue(await field.evaluate("e=>e===e.getRootNode().activeElement"))
        await dialog.get_by_role("button", name="Cancel", exact=True).click()
        await dialog.get_by_role("button", name="Keep editing", exact=True).click()
        self.assertEqual(await field.input_value(), "20:15:00")
        await field.press("Escape")
        await dialog.get_by_role("button", name="Discard and close", exact=True).click()
        self.assertEqual(await self.page.evaluate("optionPosts"), 0)
        self.assertFalse(await dialog.is_visible())

    async def test_explicit_save_refreshes_accounts_without_changing_selected_tab(self):
        dialog = await self.open()
        await dialog.get_by_label("Time (Europe/Kyiv)", exact=True).fill("20:15:00")
        await dialog.get_by_role("button", name="Save", exact=True).click()
        await dialog.get_by_text("Settings saved.", exact=True).last.wait_for()
        self.assertEqual(await self.page.evaluate("optionPosts"), 1)
        sent = await self.page.evaluate("apiCalls.find(c=>c.method==='POST').data")
        self.assertEqual(
            sent, {"enabled": False, "time": "20:15:00", "include_estimate": True}
        )
        await dialog.get_by_role("button", name="Close", exact=True).click()
        self.assertEqual(await self.page.evaluate("card._entity"), "sensor.account_1")
        self.assertEqual(
            await self.page.get_by_role(
                "tab", name="Settings", exact=True
            ).get_attribute("aria-selected"),
            "true",
        )
        self.assertEqual(
            await self.page.evaluate("apiCalls.filter(c=>c.method==='DELETE').length"),
            0,
        )

    async def test_validation_error_retains_values_and_never_resubmits_on_update(self):
        await self.page.evaluate(
            "() => {const previous=hass.callApi;hass.callApi=async(m,p,d)=>m==='POST'?(apiCalls.push({method:m,data:d}),{...optionsStep(),errors:{base:'invalid_reminder_settings'}}):previous(m,p,d);}"
        )
        dialog = await self.open()
        field = dialog.get_by_label("Time (Europe/Kyiv)", exact=True)
        await field.fill("invalid")
        await dialog.get_by_role("button", name="Save", exact=True).click()
        self.assertEqual(await field.input_value(), "invalid")
        await self.page.evaluate("card.hass={...hass}")
        self.assertEqual(
            await self.page.evaluate("apiCalls.filter(c=>c.method==='POST').length"), 1
        )

    async def test_uncertain_save_disables_retry_and_retains_input(self):
        await self.page.evaluate(
            "() => {const previous=hass.callApi;hass.callApi=async(m,p,d)=>{if(m==='POST'){apiCalls.push({method:m,data:d});throw new Error('offline');}return previous(m,p,d);};}"
        )
        dialog = await self.open()
        await dialog.get_by_label("Time (Europe/Kyiv)", exact=True).fill("20:15:00")
        await dialog.get_by_role("button", name="Save", exact=True).click()
        await dialog.get_by_text("Changes may have been saved.", exact=False).wait_for()
        self.assertTrue(
            await dialog.get_by_role("button", name="Save", exact=True).is_disabled()
        )
        self.assertEqual(
            await dialog.get_by_label("Time (Europe/Kyiv)", exact=True).input_value(),
            "20:15:00",
        )
        self.assertEqual(
            await self.page.evaluate("apiCalls.filter(c=>c.method==='POST').length"), 1
        )

    async def test_unsaved_readings_require_deliberate_discard_before_options(self):
        field = await workspace.WorkspaceTests.readings(self)
        await field.fill("12503")
        await self.page.get_by_role("tab", name="Settings", exact=True).click()
        await self.page.get_by_role(
            "button", name="Reading reminders", exact=True
        ).click()
        await self.page.get_by_role("button", name="Keep editing", exact=True).click()
        self.assertEqual(await field.input_value(), "12503")
        self.assertEqual(
            await self.page.evaluate(
                "calls.filter(c=>c.type==='dtek_monitor/account_options').length"
            ),
            0,
        )
        await self.page.get_by_role(
            "button", name="Reading reminders", exact=True
        ).click()
        await self.page.get_by_role(
            "button", name="Discard and open settings", exact=True
        ).click()
        await (
            self.page.locator("dtek-account-options dialog")
            .get_by_role("button", name="Save", exact=True)
            .wait_for()
        )
        self.assertIsNone(await self.page.evaluate("card._readings"))

    async def test_pending_open_blocks_switch_and_late_reply_is_deleted_after_disconnect(
        self,
    ):
        await self.page.evaluate(
            "() => {const previous=hass.callWS;hass.callWS=m=>m.type==='dtek_monitor/account_options'?(calls.push(m),new Promise(resolve=>window.finishOpen=resolve)):previous(m);}"
        )
        await self.page.get_by_role("tab", name="Settings", exact=True).click()
        await self.page.get_by_role(
            "button", name="Reading reminders", exact=True
        ).click()
        await self.page.wait_for_function("!!window.finishOpen")
        await self.page.evaluate("card._choose('sensor.account_2')")
        self.assertEqual(await self.page.evaluate("card._entity"), "sensor.account_1")
        self.assertTrue(
            await self.page.locator("dtek-account-options dialog")
            .get_by_role("button", name="Cancel", exact=True)
            .is_disabled()
        )
        await self.page.evaluate(
            "card.remove();finishOpen({flow_id:'late-flow',entry_id:'connection-1'})"
        )
        await self.page.wait_for_function(
            "apiCalls.some(c=>c.method==='DELETE'&&c.path.endsWith('/late-flow'))"
        )
        self.assertEqual(await self.page.evaluate("optionPosts"), 0)

    async def test_viewer_has_no_mutating_settings_actions(self):
        await self.page.evaluate(
            "accounts.forEach(a=>a.can_submit=false);hass={...hass,user:{id:'viewer',is_admin:false}};card.hass=hass"
        )
        await self.page.get_by_role("tab", name="Settings", exact=True).click()
        self.assertEqual(
            await self.page.get_by_role(
                "button", name="Reading reminders", exact=True
            ).count(),
            0,
        )
        self.assertEqual(
            await self.page.evaluate(
                "calls.filter(c=>c.type==='dtek_monitor/account_options').length"
            ),
            0,
        )

    async def test_mobile_settings_dialog_stays_inside_viewport(self):
        for width in [320, 390]:
            await self.page.set_viewport_size({"width": width, "height": 844})
            dialog = await self.open()
            self.assertTrue(
                await dialog.evaluate(
                    "e=>e.getBoundingClientRect().width<=innerWidth&&e.scrollWidth<=e.clientWidth"
                )
            )
            await dialog.get_by_role("button", name="Cancel", exact=True).click()
