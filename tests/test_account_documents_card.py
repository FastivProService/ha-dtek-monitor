"""Document downloads do not expose a previous account or treat errors as empty."""
import importlib.util
from pathlib import Path
import unittest
from card_browser import install_card
HAS_PLAYWRIGHT=importlib.util.find_spec('playwright') is not None
if HAS_PLAYWRIGHT:
    from playwright.async_api import async_playwright
SCRIPT=Path(__file__).resolve().parents[1]/'custom_components/dtek_monitor/frontend/account-documents.js'

@unittest.skipUnless(HAS_PLAYWRIGHT,'Requires Playwright')
class DocumentCardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.p=await async_playwright().start();self.browser=await self.p.chromium.launch(headless=True)
        self.page=await self.browser.new_page(viewport={'width':390,'height':850})
        await install_card(self.page,SCRIPT)
        await self.page.evaluate('''() => {
          window.calls=[];window.result={documents:[],checked_at:'2026-10-22T12:00:00+03:00'};
          window.hass={language:'uk',user:{id:'owner'},callWS:async msg=>{if(msg.type==='dtek_monitor/account_identity')return {masked:'••••0001'};calls.push(msg);return result;}};
          window.card=document.createElement('dtek-account-documents');card.setConfig({entity:'sensor.first'});document.querySelector('main').append(card);card.hass=hass;
        }''')
        await self.page.get_by_role('button',name='Оновити документи',exact=True).wait_for()
    async def asyncTearDown(self):
        await self.browser.close();await self.p.stop()
    async def test_empty_and_failure_are_distinct_and_names_are_plain_text(self):
        await self.page.get_by_text('ДТЕК поки не повернув',exact=False).wait_for()
        await self.page.evaluate("() => {hass.callWS=async()=>{throw {code:'unavailable'}};}")
        await self.page.get_by_role('button',name='Оновити документи',exact=True).click()
        await self.page.get_by_role('alert').wait_for()
        self.assertEqual(await self.page.get_by_text('ДТЕК поки не повернув',exact=False).count(),0)
        await self.page.evaluate('''() => {hass.callWS=async msg=>msg.type==='dtek_monitor/account_identity'?{masked:'••••0001'}:{documents:[{id:'a'.repeat(32),name:'<img src=x onerror="window.injected=true">'}],checked_at:'2026-10-22T12:00:00+03:00'};}''')
        await self.page.get_by_role('button',name='Оновити документи',exact=True).click()
        await self.page.get_by_text('<img src=x onerror="window.injected=true">',exact=True).wait_for()
        self.assertEqual(await self.page.locator('img').count(),0)
        self.assertIsNone(await self.page.evaluate('window.injected'))
    async def test_download_is_explicit_and_late_old_account_response_discarded(self):
        await self.page.evaluate('''() => {result={documents:[{id:'a'.repeat(32),name:'Synthetic.pdf'}],checked_at:'2026-10-22T12:00:00+03:00'};}''')
        await self.page.get_by_role('button',name='Оновити документи',exact=True).click()
        button=self.page.get_by_role('button',name='Завантажити PDF: Synthetic.pdf',exact=True)
        await button.wait_for();self.assertFalse(await self.page.evaluate('calls.some(c=>c.document_id)'))
        await self.page.evaluate('''() => {hass.callWS=msg=>msg.document_id?new Promise(resolve=>window.release=resolve):Promise.resolve({documents:[],checked_at:'2026-10-22T12:00:00+03:00'});}''')
        await button.click()
        await self.page.evaluate('''() => {window.downloads=0;URL.createObjectURL=()=>{downloads++;return 'blob:fake';};card.setConfig({entity:'sensor.other'});card.hass=hass;release({mime_type:'application/pdf',name:'private.pdf',data:btoa('%PDF-1.4\\n%%EOF')});}''')
        await self.page.get_by_text('ДТЕК поки не повернув',exact=False).wait_for()
        self.assertEqual(await self.page.evaluate('downloads'),0)
        self.assertNotIn('Synthetic.pdf',await self.page.locator('main').inner_text())
