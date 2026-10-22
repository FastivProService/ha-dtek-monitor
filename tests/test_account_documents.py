"""Technical documents: explicit empty, bounded files, ownership and restart deduplication."""
import base64
from copy import deepcopy
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch
import test_ha_runtime as runtime
from component_loader import load_component_module

if runtime.HAS_HA:
    mod=load_component_module('account_documents')
    api=load_component_module('account_documents_api')
    providers=load_component_module('account_providers')


def payload(identity='act-one',name='Synthetic act.pdf'):
    return {'status':'success','data':{'itemsByType':{'5':[{'actId':identity,'fileName':name}]}}}


class MemoryStore:
    def __init__(self):self.data=None
    async def async_load(self):return deepcopy(self.data)
    async def async_save(self,data):self.data=deepcopy(data)


@unittest.skipUnless(runtime.HAS_HA,'Requires Home Assistant')
class DocumentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entry=NS(entry_id='test-entry',data={'provider_id':'dtek_oem','accounts':{'100001':'Synthetic address'}},options={'account_meter_notifications':{'100001':{'documents':True}}})
        self.coordinator=NS(client=NS(_accounts={'100001'}))
        self.hass=NS(config=NS(language='uk'))
        with patch.object(mod,'Store'):
            self.manager=mod.AccountDocuments(self.hass,self.entry,self.coordinator)
        self.manager.store=MemoryStore()
        self.manager.client=NS(list_documents=AsyncMock(return_value=mod.parse_documents(payload(),'100001')),download=AsyncMock(return_value={'data':'synthetic'}),close=AsyncMock())
        self.addAsyncCleanup(self.manager.close)
        self.create=patch.object(mod.pn,'async_create').start()
        patch.object(mod,'clear_api_issue').start();patch.object(mod,'report_api_issue').start()
        self.addCleanup(patch.stopall)

    def test_explicit_empty_unknown_shape_ids_and_file_boundaries(self):
        self.assertEqual(mod.parse_documents({'status':'success','data':'Empty result'},'1'),[])
        first=mod.parse_documents(payload(),'1');second=mod.parse_documents(payload(),'2')
        self.assertNotEqual(first[0]['id'],second[0]['id'])
        for bad in ({},[],{'status':'success','data':None},{'status':'success','data':[]},payload(name='bad\nname')):
            with self.assertRaises(mod.AccountError):mod.parse_documents(bad,'1')
        pdf=base64.b64encode(b'%PDF-1.4\nSynthetic\n%%EOF').decode()
        doc=mod.parse_document_file({'status':'success','data':{'file':pdf,'docClass':'pdf'}},'test')
        self.assertEqual(doc['name'],'test.pdf')
        for data in ({'file':pdf,'docClass':'html'},{'file':base64.b64encode(b'<script/>').decode(),'docClass':'pdf'},{'file':'x'*(mod.MAX_RESPONSE_BYTES+1),'docClass':'pdf'}):
            with self.assertRaises(mod.AccountError):mod.parse_document_file({'status':'success','data':data},'test')

    async def test_first_baseline_no_flood_new_act_once_after_restart_and_reordering(self):
        first=await self.manager.get('100001');self.create.assert_not_called()
        self.assertNotIn('_act_id',json.dumps(first))
        self.manager.cache.clear()
        self.manager.client.list_documents.return_value += mod.parse_documents(payload('act-two'),'100001')
        await self.manager.get('100001');self.create.assert_called_once()
        self.manager.cache.clear();self.manager.records=None
        self.manager.client.list_documents.return_value.reverse()
        await self.manager.get('100001');self.create.assert_called_once()
        self.assertNotIn('Synthetic act',json.dumps(self.manager.store.data))

    async def test_empty_then_new_document_notifies_and_failure_is_not_empty(self):
        self.manager.client.list_documents.return_value=[]
        await self.manager.get('100001');self.create.assert_not_called()
        self.manager.cache.clear();self.manager.client.list_documents.return_value=mod.parse_documents(payload(),'100001')
        await self.manager.get('100001');self.create.assert_called_once()
        saved=deepcopy(self.manager.store.data)
        self.manager.cache.clear();self.manager.client.list_documents.side_effect=mod.AccountError('offline')
        with self.assertRaises(mod.AccountError):await self.manager.get('100001')
        self.assertEqual(self.manager.store.data,saved)

    async def test_download_requires_id_from_selected_account_and_rechecks_after_fetch(self):
        data=await self.manager.get('100001');identity=data['documents'][0]['id']
        with self.assertRaises(mod.SubmissionError):await self.manager.download('100001','foreign-id')
        self.manager.client.download.assert_not_awaited()
        async def revoke(*args):self.entry.data['accounts']={};return {'data':'private'}
        self.manager.client.download.side_effect=revoke
        with self.assertRaises(mod.SubmissionError):await self.manager.download('100001',identity)

    async def test_persistence_failure_and_corrupt_state_cannot_send_notifications(self):
        self.manager.store.async_save=AsyncMock(side_effect=OSError())
        with self.assertRaises(mod.SubmissionError):await self.manager.get('100001')
        self.create.assert_not_called()
        self.manager.records=None;self.manager.store.data={'x':{'seen':'bad'}}
        with self.assertRaises(mod.SubmissionError):await self.manager.get('100001')
        self.manager.store.data={'x':{'seen':[], 'pending':'bad'}}
        with self.assertRaises(mod.SubmissionError):await self.manager.get('100001')

    async def test_api_authorizes_both_before_and_after_file_fetch(self):
        self.entry.runtime_data=NS(documents=self.manager)
        connection=NS(send_result=Mock(),send_error=Mock());msg={'id':1,'entity_id':'sensor.example'}
        with patch.object(api,'resolve_readable_account',side_effect=[(self.entry,'100001',None),api.AccountAccessError('unauthorized')]):
            await api.websocket_documents.__wrapped__(self.hass,connection,msg)
        connection.send_result.assert_not_called();self.assertEqual(connection.send_error.call_args.args[1],'unauthorized')

    async def test_read_only_routes_stream_limits_and_expired_auth(self):
        modern=NS(spec=providers.get_account_provider_spec(),_accounts={'100001'})
        response=NS(status_code=200,aclose=AsyncMock())
        async def chunks():yield json.dumps({'status':'success','data':'Empty result'}).encode()
        response.aiter_content=chunks
        transport=NS(request=AsyncMock(return_value=response),close=AsyncMock())
        client=mod.DocumentClient(modern,transport);client._tokens['100001']='synthetic-token'
        result=await client.request_document('100001','/tech_acts/get_acts_list')
        self.assertEqual(result['data'],'Empty result')
        self.assertTrue(transport.request.call_args.kwargs['stream'])
        with self.assertRaises(ValueError):await client.request_document('100001','/write')
        async def too_big():yield b'x'*(mod.MAX_RESPONSE_BYTES+1)
        response.aiter_content=too_big
        with self.assertRaisesRegex(mod.SubmissionError,'document_too_large'):await client.request_document('100001','/tech_acts/get_act','act')
        self.assertEqual(response.aclose.await_count,2)
        modern._accounts.clear()
        with self.assertRaises(mod.SubmissionError):await client.request_document('100001','/tech_acts/get_act','act')
        await client.close()
