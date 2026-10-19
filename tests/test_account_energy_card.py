"""Energy binding UI: local-only calibration, deliberate insertion, stable edits."""

import unittest
import test_account_readings_card as readings


@unittest.skipUnless(readings.HAS_PLAYWRIGHT, "Requires Playwright Chromium")
class EnergyCardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await readings.ReadingsCardTests.asyncSetUp(self)
        await self.page.evaluate("""() => {
          window.binding={mode:'offset',serial:'TEST',digits:6,revision:'reference-1',max_age:900,max_power_kw:'30',history:[],zones:{GENERAL:{entity_id:'sensor.grid',anchor_meter:'1000',anchor_source:'100'}}};
          window.energy={binding,ready:true,proposal:'source-token',readings:{GENERAL:{value:'1025.4',send_value:'1025',source:{value:'125.4',at:'2026-10-18T12:00:00+03:00'}}}};
          window.deferEnergy=false;window.energyPending=[];
          const old=hass.callWS;
          hass.states={'sensor.grid':{state:'125.4',attributes:{device_class:'energy',state_class:'total_increasing',unit_of_measurement:'kWh',friendly_name:'Apartment input'}}};
          hass.callWS=async msg=>{
            if(msg.type!=='dtek_monitor/account_energy')return old(msg);
            calls.push(msg);
            if(msg.action==='get')return deferEnergy ? new Promise(resolve=>energyPending.push(resolve)) : structuredClone(energy);
            if(msg.action==='preview')return {confirmation:'local-reference',binding:msg.config?{...binding,...msg.config}:binding,change:{changes:{GENERAL:{source:'125.4',source_at:'2026-10-18T12:00:00+03:00',actual:msg.references?.GENERAL??null,correction:'4.6'}}}};
            if(msg.action==='save')return {saved:true};
            if(msg.action==='remove'){energy={binding:null};return {removed:true};}
          };
        }""")

    async def asyncTearDown(self):
        await readings.ReadingsCardTests.asyncTearDown(self)

    async def load(self):
        await readings.ReadingsCardTests.load(self)
        await self.page.get_by_role(
            "button", name="Load / refresh energy source", exact=True
        ).click()
        await self.page.get_by_role(
            "button", name="Use proposed readings in the form", exact=True
        ).wait_for()

    async def test_proposal_is_inserted_only_by_button_and_user_edit_clears_provenance(
        self,
    ):
        await self.load()
        field = self.page.get_by_label("Single zone (kWh)", exact=True)
        await field.fill("1030")
        await self.page.get_by_role(
            "button", name="Load / refresh energy source", exact=True
        ).click()
        self.assertEqual(await field.input_value(), "1030")
        await self.page.get_by_role(
            "button", name="Use proposed readings in the form", exact=True
        ).click()
        self.assertEqual(await field.input_value(), "1025")
        await self.page.get_by_role(
            "button", name="Review readings", exact=True
        ).click()
        self.assertEqual(
            await self.page.evaluate(
                "calls.find(x=>x.action==='prepare').source_token"
            ),
            "source-token",
        )
        await self.page.get_by_role(
            "button", name="Back to editing", exact=True
        ).click()
        await field.fill("1030")
        await self.page.get_by_role(
            "button", name="Review readings", exact=True
        ).click()
        self.assertFalse(
            await self.page.evaluate(
                "Object.hasOwn(calls.filter(x=>x.action==='prepare').at(-1),'source_token')"
            )
        )
        self.assertEqual(
            await self.page.evaluate("calls.filter(x=>x.action==='confirm').length"), 0
        )

    async def test_corrected_reading_can_save_reference_without_sending_to_dtek(self):
        await self.load()
        await self.page.get_by_label("Single zone (kWh)", exact=True).fill("1030")
        await self.page.get_by_role(
            "button", name="Use entered readings for local calibration", exact=True
        ).click()
        await self.page.get_by_text("Correction: 4.6 kWh", exact=True).wait_for()
        save = self.page.get_by_role("button", name="Save local reference", exact=True)
        self.assertTrue(await save.is_disabled())
        await self.page.locator("dtek-energy-editor").get_by_role("checkbox").check()
        await save.click()
        await self.page.get_by_text(
            "Local reference saved. Future estimates include this correction; submission is a separate action.",
            exact=True,
        ).wait_for()
        self.assertEqual(
            await self.page.get_by_label("Single zone (kWh)", exact=True).input_value(),
            "1030",
        )
        operations = await self.page.evaluate("calls.map(x=>[x.type,x.action])")
        self.assertIn(["dtek_monitor/account_energy", "save"], operations)
        self.assertNotIn(["dtek_monitor/account_submission", "confirm"], operations)
        self.assertEqual(
            await self.page.evaluate(
                "calls.find(x=>x.type==='dtek_monitor/account_energy'&&x.action==='preview').references.GENERAL"
            ),
            "1030",
        )

    async def test_binding_fields_survive_hass_updates_and_review_is_required(self):
        await self.page.evaluate("energy={binding:null}")
        await readings.ReadingsCardTests.load(self)
        await self.page.get_by_role(
            "button", name="Load / refresh energy source", exact=True
        ).click()
        await self.page.get_by_role(
            "button", name="Link an energy sensor", exact=True
        ).click()
        source = self.page.get_by_label(
            "Single zone · Cumulative energy sensor", exact=True
        )
        actual = self.page.get_by_label(
            "Single zone · Actual official meter reading (kWh)", exact=True
        )
        await source.fill("sensor.grid")
        await actual.fill("1000.5")
        await self.page.get_by_label(
            "Source availability check", exact=True
        ).select_option("availability")
        await self.page.get_by_text(
            "For sources that report only when the counter changes.", exact=False
        ).wait_for()
        for _ in range(5):
            await self.page.evaluate(
                "card.hass={...hass,states:{...hass.states,random:{state:Math.random()}}}"
            )
        self.assertEqual(await source.input_value(), "sensor.grid")
        self.assertEqual(await actual.input_value(), "1000.5")
        self.assertEqual(
            await self.page.get_by_label(
                "Source availability check", exact=True
            ).input_value(),
            "availability",
        )
        await self.page.get_by_role("button", name="Review binding", exact=True).click()
        self.assertEqual(
            await self.page.evaluate(
                "calls.filter(x=>x.type==='dtek_monitor/account_energy'&&x.action==='preview').at(-1).config.freshness"
            ),
            "availability",
        )
        self.assertTrue(
            await self.page.get_by_role(
                "button", name="Save local reference", exact=True
            ).is_disabled()
        )
        self.assertEqual(
            await self.page.evaluate(
                "calls.filter(x=>x.action==='save'||x.action==='confirm').length"
            ),
            0,
        )

    async def test_calibration_remains_available_when_dtek_context_is_offline(self):
        await self.page.evaluate(
            """() => {const old=hass.callWS;hass.callWS=msg=>msg.type==='dtek_monitor/account_submission'?Promise.reject({code:'unavailable'}):old(msg);} """
        )
        await self.page.get_by_role(
            "button", name="Load current meter", exact=True
        ).click()
        await self.page.get_by_role(
            "button", name="Load / refresh energy source", exact=True
        ).click()
        await self.page.get_by_label(
            "Single zone · Actual official meter reading (kWh)", exact=True
        ).fill("1030")
        await self.page.get_by_role(
            "button", name="Review new reference pair", exact=True
        ).click()
        await self.page.get_by_text("Correction: 4.6 kWh", exact=True).wait_for()
        self.assertEqual(
            await self.page.evaluate("calls.filter(x=>x.action==='confirm').length"), 0
        )

    async def test_expired_or_reset_source_cannot_be_reused_silently(self):
        await self.load()
        await self.page.evaluate(
            "energy={...energy,ready:false,readings:{GENERAL:{error:'source_reset'}}}"
        )
        await self.page.get_by_role(
            "button", name="Load / refresh energy source", exact=True
        ).click()
        self.assertEqual(
            await self.page.get_by_role(
                "button", name="Use proposed readings in the form", exact=True
            ).count(),
            0,
        )
        await self.page.get_by_text(
            "The counter decreased. Verify the meter and create a new reference pair.",
            exact=True,
        ).wait_for()

    async def test_late_energy_result_does_not_cross_accounts(self):
        await self.page.evaluate("deferEnergy=true")
        await self.page.get_by_role(
            "button", name="Load / refresh energy source", exact=True
        ).click()
        await self.page.evaluate(
            "card.setConfig({entity:'sensor.other'});energyPending[0](energy)"
        )
        self.assertEqual(
            await self.page.get_by_text("sensor.grid", exact=True).count(), 0
        )

    async def test_ukrainian_layout_and_error_text_are_safe(self):
        await self.page.evaluate(
            "card.setConfig({entity:'sensor.example'});card.hass={...hass,language:'uk'};energy.readings.GENERAL={error:'source_stale'};energy.ready=false;binding.zones.GENERAL.entity_id='<img src=x onerror=alert(1)>'"
        )
        await self.page.get_by_role(
            "button", name="Завантажити / оновити джерело", exact=True
        ).click()
        await self.page.get_by_text(
            "Дані джерела застаріли. Оновіть пристрій перед використанням.", exact=True
        ).wait_for()
        self.assertEqual(await self.page.locator("img").count(), 0)
        self.assertTrue(
            await self.page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        )
