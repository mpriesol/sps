import json

import httpx
import pytest

from app.sps import DemoSPS, OperationUncertainError, SPSClient, SPSError


@pytest.mark.asyncio
async def test_portal_contract_cookie_session_and_courier_body():
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith('/login'):
            assert json.loads(request.content) == {'username': 'test-user', 'password': 'test-account-secret'}
            return httpx.Response(200, json={'parcelshop': {'id': 12}}, headers={'set-cookie': 'SESSION=test-only; Path=/psappserver; Secure; HttpOnly'})
        assert 'SESSION=test-only' in request.headers.get('cookie', '')
        if request.url.path.endswith('/shipment/tour'):
            return httpx.Response(200, json=[{'trackNumber': '0012345', 'senderName': 'Test', 'tourCourier': 7}])
        if request.url.path.endswith('/shipment/couriers'):
            assert json.loads(request.content) == {'trackNumbers': ['0012345']}
            return httpx.Response(200, json=[{'id': 7, 'code': 'TEST7', 'name': 'Courier'}])
        if request.url.path.endswith('/shipment/accept'):
            assert json.loads(request.content) == {'trackNumber': '0012345', 'courierId': 7, 'password': 'courier-secret'}
            return httpx.Response(200)
        raise AssertionError(request.url)
    sps = SPSClient('test-user', 'test-account-secret', httpx.MockTransport(handler))
    assert (await sps.shipments())[0]['trackNumber'] == '0012345'
    assert (await sps.couriers(['0012345']))[0]['id'] == 7
    await sps.accept('0012345', 7, 'courier-secret')
    await sps.close()
    assert [c[1].rsplit('/', 1)[-1] for c in calls] == ['login', 'tour', 'couriers', 'accept']


@pytest.mark.asyncio
async def test_account_login_failure_never_posts_parcel():
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(401)
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    with pytest.raises(SPSError):
        await sps.accept('TEST1', 1, 'test')
    assert len(paths) == 1 and paths[0].endswith('/login')
    await sps.close()


@pytest.mark.asyncio
async def test_expired_account_session_refreshes_on_next_read():
    logins = 0
    reads = 0
    def handler(request):
        nonlocal logins, reads
        if request.url.path.endswith('/login'):
            logins += 1
            return httpx.Response(200, json={'parcelshop': 12})
        reads += 1
        return httpx.Response(401) if reads == 1 else httpx.Response(200, json=[])
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    assert await sps.shipments() == []
    assert logins == 2 and reads == 2
    await sps.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [302, 404, 500])
async def test_error_response_is_not_reported_as_accepted(status):
    def handler(request):
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': 12})
        return httpx.Response(status)
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    with pytest.raises(SPSError):
        await sps.accept('TEST1', 1, 'secret')
    await sps.close()


@pytest.mark.asyncio
async def test_pin_lookup_is_read_only_encoded_and_does_not_return_sensitive_fields():
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': 12})
        assert request.method == 'GET'
        assert request.url.path.endswith('/getShipmentDetails')
        assert request.url.params['smsKey'] == '00+123&456'
        assert request.url.params['logInfo'] == 'false'
        return httpx.Response(200, json={
            'trackNumber': '0012345', 'cod': 0, 'senderName': 'Sender',
            'recipientName': 'Recipient', 'recipientEmail': 'private@example.test',
            'recipientPhone': 'private', 'smsKey': '00+123&456',
        })
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    parcel = await sps.lookup_pickup('00+123&456')
    assert parcel['trackNumber'] == '0012345'
    assert parcel['cod'] == 0
    assert not {'recipientEmail', 'recipientPhone', 'smsKey'} & parcel.keys()
    assert calls == [('POST', '/psappserver/rest/login'), ('GET', '/psappserver/rest/getShipmentDetails')]
    await sps.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [[], {'trackNumber': 'TEST1'}, {'trackNumber': 'TEST1', 'cod': -1}, {'trackNumber': 'TEST1', 'cod': True}])
async def test_changed_pickup_contract_cannot_hide_unknown_cod(payload):
    def handler(request):
        return httpx.Response(200, json={'parcelshop': 12} if request.url.path.endswith('/login') else payload)
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    with pytest.raises(SPSError):
        await sps.lookup_pickup('000001')
    await sps.close()


@pytest.mark.asyncio
async def test_missing_pickup_is_none_not_success():
    def handler(request):
        return httpx.Response(200, content=b'{"parcelshop":12}' if request.url.path.endswith('/login') else b'null')
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    assert await sps.lookup_pickup('000001') is None
    await sps.close()


@pytest.mark.asyncio
async def test_stored_and_expired_contract_preserves_carrier_dates_and_return_gate():
    def handler(request):
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': 12})
        if request.url.path.endswith('/getStoredShipments'):
            assert request.url.params['trackNumber'] == '0012345'
            return httpx.Response(200, json=[{'trackNumber': '0012345', 'expiredDate': '2026-12-01T23:59:59', 'status': {'code': 441, 'description': 'Stored'}}])
        if request.url.path.endswith('/events'):
            return httpx.Response(403)
        assert request.url.path.endswith('/shipment/expired')
        return httpx.Response(200, json=[
            {'trackNumber': '0012345', 'status': {'code': 441}, 'entryCourier': 7},
            {'trackNumber': '0098765', 'status': {'code': 5555}},
        ])
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    stored = await sps.stored_shipments('0012345')
    assert stored[0]['expiredDate'] == '2026-12-01T23:59:59'
    assert stored[0]['cod'] is None
    returns = await sps.expired_shipments()
    assert returns[0]['returnEligible'] is True
    assert returns[0]['entryCourier'] == 7
    assert returns[1]['returnEligible'] is False
    assert returns[0]['handoverKind'] == 'unknown'
    await sps.close()


@pytest.mark.asyncio
async def test_expired_shipment_without_status_is_not_assumed_returnable():
    def handler(request):
        return httpx.Response(200, json={'parcelshop': 12} if request.url.path.endswith('/login') else [{'trackNumber': 'TEST1'}])
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    with pytest.raises(SPSError):
        await sps.expired_shipments()
    await sps.close()


@pytest.mark.asyncio
async def test_delivery_and_return_use_verified_put_contracts_only_on_explicit_call():
    calls = []
    def handler(request):
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': 12})
        calls.append((request.method, request.url.path.rsplit('/', 1)[-1], json.loads(request.content)))
        return httpx.Response(204)
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    await sps.deliver('000001', cod_confirmed=True)
    await sps.return_parcel('0012345', 7, 'courier-secret')
    assert calls == [
        ('PUT', 'shipmentDelivery', {'smsKey': '000001', 'state': 40, 'codConfirmed': True, 'paidWithPaymentTerminal': False}),
        ('PUT', 'return', {'trackNumber': '0012345', 'courierId': 7, 'password': 'courier-secret'}),
    ]
    await sps.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['accept', 'deliver', 'return'])
async def test_write_timeout_is_uncertain_and_never_retried(operation):
    writes = []
    def handler(request):
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': 12})
        writes.append(request)
        raise httpx.ReadTimeout('test timeout', request=request)
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    with pytest.raises(OperationUncertainError, match='Pred opakovaním overte'):
        if operation == 'accept':
            await sps.accept('TEST1', 1, 'test')
        elif operation == 'deliver':
            await sps.deliver('000001')
        else:
            await sps.return_parcel('TEST1', 1, 'test')
    assert len(writes) == 1
    await sps.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('status,content,content_type', [
    (500, b'', 'application/json'),
    (200, b'<html>Login</html>', 'text/html'),
    (200, b'unexpected result', 'text/plain'),
    (200, b'{"success":false}', 'application/json'),
    (202, b'', 'application/json'),
])
async def test_ambiguous_write_response_requires_reconciliation(status, content, content_type):
    def handler(request):
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': 12})
        return httpx.Response(status, content=content, headers={'content-type': content_type})
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    with pytest.raises(OperationUncertainError):
        await sps.accept('TEST1', 1, 'test')
    await sps.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [401, 404, 409, 461, 462, 463, 464, 465])
async def test_known_delivery_rejection_is_safe_error_and_not_retried(status):
    writes = 0
    def handler(request):
        nonlocal writes
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': 12})
        writes += 1
        return httpx.Response(status)
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    with pytest.raises(SPSError) as caught:
        await sps.deliver('000001')
    assert not isinstance(caught.value, OperationUncertainError)
    assert writes == 1
    await sps.close()


@pytest.mark.asyncio
async def test_demo_entire_lifecycle_and_cod_gate_are_local():
    sps = DemoSPS()
    assert len(await sps.couriers(['DEMO00000001'])) == 2
    await sps.accept('DEMO00000001', 2, 'demo')
    assert (await sps.lookup_pickup('000001'))['trackNumber'] == 'DEMO00000001'
    assert (await sps.stored_shipments('DEMO00000001'))[0]['expiredDate']
    await sps.deliver('000001')
    assert await sps.lookup_pickup('000001') is None
    assert await sps.stored_shipments('DEMO00000001') == []
    with pytest.raises(SPSError, match='Dobierka'):
        await sps.deliver('111111')
    assert await sps.lookup_pickup('111111') is not None
    await sps.deliver('111111', cod_confirmed=True)
    assert (await sps.receipt('DEMOSTORED01')).startswith(b'%PDF-1.4')
    expired = await sps.expired_shipments()
    assert {p['trackNumber']: p['returnEligible'] for p in expired} == {
        'DEMOEXPIRED01': True, 'DEMOBLOCKED01': False,
        'DEMOOUTGOING01': True, 'DEMOOUTGOING02': True, 'DEMOOUTBLOCKED03': False,
    }
    with pytest.raises(SPSError):
        await sps.return_parcel('DEMOBLOCKED01', 1, 'demo')
    await sps.return_parcel('DEMOEXPIRED01', 1, 'demo')
    assert {p['trackNumber'] for p in await sps.expired_shipments()} == {'DEMOBLOCKED01', 'DEMOOUTGOING01', 'DEMOOUTGOING02', 'DEMOOUTBLOCKED03'}
    await sps.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('allowed', [False, True])
async def test_terminal_flag_uses_real_account_permission(allowed):
    mutations = []
    def handler(request):
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': {'id': 12, 'paymentTerminal': allowed}})
        if request.url.path.endswith('/getShipmentDetails'):
            return httpx.Response(200, json={'trackNumber': 'TEST1', 'cod': 12.50})
        mutations.append(json.loads(request.content))
        return httpx.Response(200)
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    assert (await sps.lookup_pickup('000001'))['paymentTerminalAllowed'] is allowed
    if allowed:
        await sps.deliver('000001', paid_with_terminal=True)
        assert mutations == [{'smsKey': '000001', 'state': 40, 'codConfirmed': False, 'paidWithPaymentTerminal': True}]
    else:
        with pytest.raises(SPSError, match='povolený platobný terminál'):
            await sps.deliver('000001', paid_with_terminal=True)
        assert mutations == []
    await sps.close()


@pytest.mark.asyncio
async def test_receipt_generation_and_reprint_use_verified_contract_without_delivery():
    calls = []
    def handler(request):
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': 12})
        calls.append(request.url.path.rsplit('/', 1)[-1])
        if request.url.path.endswith('/hash'):
            assert request.method == 'POST'
            assert json.loads(request.content) == {'text': '0012345'}
            return httpx.Response(200, json={'hash': 'test-hash'})
        assert request.method == 'GET'
        assert request.url.path.endswith('/getreceiptdocuments')
        assert request.url.params['hash'] == 'test-hash'
        assert request.url.params['trackNumber'] == '0012345'
        assert request.url.params['updateReceiptNumber'] == ('true' if len(calls) == 2 else 'false')
        return httpx.Response(200, content=b'%PDF-1.4\n%%EOF', headers={'content-type': 'application/pdf'})
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    assert (await sps.receipt('0012345')).startswith(b'%PDF-')
    assert (await sps.receipt('0012345', update_number=False)).startswith(b'%PDF-')
    assert calls == ['hash', 'getreceiptdocuments', 'hash', 'getreceiptdocuments']
    await sps.close()


@pytest.mark.asyncio
async def test_receipt_numbering_timeout_is_not_retried_or_reported_as_delivery_failure():
    downloads = 0
    def handler(request):
        nonlocal downloads
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': 12})
        if request.url.path.endswith('/hash'):
            return httpx.Response(200, json={'hash': 'test-hash'})
        downloads += 1
        raise httpx.ReadTimeout('test timeout', request=request)
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    with pytest.raises(OperationUncertainError, match='Výdaj je dokončený'):
        await sps.receipt('0012345')
    assert downloads == 1
    await sps.close()


@pytest.mark.asyncio
async def test_courier_queue_classifies_only_authoritative_flags_and_uses_one_handover_endpoint():
    writes = []
    def handler(request):
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': 12})
        if request.url.path.endswith('/shipment/expired'):
            return httpx.Response(200, json=[
                {'trackNumber': 'OUT01', 'expiredDate': None, 'statusDate': '2026-11-18T10:00:00', 'status': {'code': 71, 'droppoff': True, 'shouldReturnToCourier': True}},
                {'trackNumber': 'EXP02', 'status': {'code': 441, 'droppoff': False, 'shouldReturnToCourier': True}},
                {'trackNumber': 'UNKNOWN03', 'expiredDate': '2000-01-01', 'status': {'code': 999, 'droppoff': False, 'shouldReturnToCourier': False}},
                {'trackNumber': 'OUTBLOCK04', 'status': {'code': 5555, 'droppoff': True, 'shouldReturnToCourier': True}},
            ])
        assert request.method == 'PUT' and request.url.path.endswith('/shipment/return')
        writes.append(json.loads(request.content))
        return httpx.Response(204)
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    queue = await sps.expired_shipments()
    assert [p['handoverKind'] for p in queue] == ['outgoing', 'return', 'unknown', 'outgoing']
    assert queue[0]['statusDate'] == '2026-11-18T10:00:00'
    assert queue[0]['expiredDate'] is None
    assert queue[3]['returnEligible'] is False
    await sps.return_parcel('OUT01', 7, 'secret')
    assert writes == [{'trackNumber': 'OUT01', 'courierId': 7, 'password': 'secret'}]
    await sps.close()


@pytest.mark.asyncio
async def test_status_catalog_fills_partial_metadata_without_overwriting_explicit_flags():
    catalog_reads = 0
    def handler(request):
        nonlocal catalog_reads
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': 12})
        if request.url.path.endswith('/events'):
            catalog_reads += 1
            return httpx.Response(200, json=[
                {'code': 71, 'droppoff': True, 'shouldReturnToCourier': True},
                {'code': 441, 'droppoff': False, 'shouldReturnToCourier': True},
                {'code': 91, 'droppoff': True, 'shouldReturnToCourier': True},
                {'code': 91, 'droppoff': False, 'shouldReturnToCourier': False},
            ])
        assert request.url.path.endswith('/shipment/expired')
        return httpx.Response(200, json=[
            {'trackNumber': 'OUT01', 'status': {'code': 71, 'droppoff': None}},
            {'trackNumber': 'RET02', 'status': {'code': 441, 'droppoff': False}},
            {'trackNumber': 'LATEST03', 'status': {'code': 71, 'droppoff': False, 'shouldReturnToCourier': False}},
            {'trackNumber': 'CONFLICT04', 'status': {'code': 91}},
        ])
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    for _ in range(2):
        queue = await sps.expired_shipments()
        assert [p['handoverKind'] for p in queue] == ['outgoing', 'return', 'unknown', 'unknown']
        assert all(p['returnEligible'] for p in queue)
    assert catalog_reads == 1
    await sps.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('catalog_status', [403, 500])
async def test_unavailable_status_catalog_never_blocks_handover_or_invents_type(catalog_status):
    def handler(request):
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': 12})
        if request.url.path.endswith('/events'):
            return httpx.Response(catalog_status)
        return httpx.Response(200, json=[{'trackNumber': 'UNKNOWN1', 'expiredDate': '2000-01-01', 'status': {'code': 71}}])
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    queue = await sps.expired_shipments()
    assert queue[0]['returnEligible'] is True and queue[0]['handoverKind'] == 'unknown'
    await sps.close()


@pytest.mark.asyncio
async def test_demo_outgoing_does_not_need_customer_registration_to_be_handed_over():
    sps = DemoSPS()
    queue = await sps.expired_shipments()
    outgoing = {p['trackNumber']: p for p in queue if p['handoverKind'] == 'outgoing'}
    assert outgoing['DEMOOUTGOING01']['expiredDate'] is None
    assert outgoing['DEMOOUTGOING02']['returnEligible'] is True
    assert outgoing['DEMOOUTBLOCKED03']['returnEligible'] is False
    await sps.return_parcel('DEMOOUTGOING01', 1, 'demo')
    assert await sps.stored_shipments('DEMOOUTGOING01') == []
    with pytest.raises(SPSError):
        await sps.return_parcel('DEMOOUTBLOCKED03', 1, 'demo')
    with pytest.raises(SPSError):
        await sps.return_parcel('CUSTOMER_NOT_REGISTERED', 1, 'demo')
    await sps.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('accepted', ['cargonetAccepted', 'cdexAccepted'])
async def test_customer_dropoff_uses_verified_body_and_explicit_success(accepted):
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': {'id': 12, 'supportdropoff': True}})
        assert request.method == 'POST' and request.url.path.endswith('/insertShipmentForDropoff')
        assert json.loads(request.content) == {'trackNumber': '00123456789'}
        return httpx.Response(200, json={accepted: True})
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    await sps.dropoff('00123456789')
    assert calls == [('POST', '/psappserver/rest/login'), ('POST', '/psappserver/rest/insertShipmentForDropoff')]
    await sps.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [
    {'error': 'DROPOFF_DISABLED'}, {'error': 'INVALID_TRACKNUMBER_LENGTH'},
    {'cargonetAccepted': False, 'cdexAccepted': False},
])
async def test_explicit_dropoff_rejection_is_not_uncertain_or_accepted(response):
    def handler(request):
        return httpx.Response(200, json={'parcelshop': {'id': 12, 'supportdropoff': True}}
                              if request.url.path.endswith('/login') else response)
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    with pytest.raises(SPSError) as caught:
        await sps.dropoff('00123456789')
    assert not isinstance(caught.value, OperationUncertainError)
    await sps.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [None, {}, [], {'success': True}, {'cargonetAccepted': 1},
                                      {'cargonetAccepted': 'true'}, {'cargonetAccepted': True, 'error': 'DROPOFF_DISABLED'}])
async def test_ambiguous_dropoff_json_never_manufactures_acceptance(response):
    def handler(request):
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': {'id': 12, 'supportdropoff': True}})
        return httpx.Response(200, content=json.dumps(response).encode(), headers={'content-type': 'application/json'})
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    with pytest.raises(OperationUncertainError):
        await sps.dropoff('00123456789')
    await sps.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['timeout', 'empty', 'server', 'html'])
async def test_dropoff_transport_or_nonconfirmation_is_uncertain_and_not_retried(failure):
    writes = []
    def handler(request):
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': {'id': 12, 'supportdropoff': True}})
        writes.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('test-only timeout', request=request)
        if failure == 'server':
            return httpx.Response(503)
        if failure == 'html':
            return httpx.Response(200, text='<html>Login</html>', headers={'content-type': 'text/html'})
        return httpx.Response(204)
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    with pytest.raises(OperationUncertainError):
        await sps.dropoff('00123456789')
    assert len(writes) == 1
    await sps.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('permission', [False, None, 'true', 1])
async def test_customer_dropoff_permission_is_checked_before_any_write(permission):
    def handler(request):
        assert request.url.path.endswith('/login')
        return httpx.Response(200, json={'parcelshop': {'id': 12, 'supportdropoff': permission}})
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    with pytest.raises(SPSError) as caught:
        await sps.dropoff('00123456789')
    assert not isinstance(caught.value, OperationUncertainError)
    await sps.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [200, 204])
async def test_payment_refresh_checks_carrier_and_amount_comes_only_from_details(status):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': {'id': 12}})
        assert request.method == 'GET'
        if request.url.path.endswith('/shipment/refreshCod'):
            assert dict(request.url.params) == {'trackNumber': '00123456789'}
            return httpx.Response(status)
        assert request.url.path.endswith('/getShipmentDetails')
        assert dict(request.url.params) == {'smsKey': '000111', 'logInfo': 'false'}
        return httpx.Response(200, json={'trackNumber': '00123456789', 'cod': 12.50})
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    assert await sps.refresh_cod('00123456789') is None
    assert (await sps.lookup_pickup('000111'))['cod'] == 12.50
    assert len(paths) == 3
    await sps.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [302, 403, 500])
async def test_failed_payment_refresh_cannot_claim_zero_or_send_payment(status):
    def handler(request):
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'parcelshop': {'id': 12}})
        assert request.method == 'GET' and request.url.path.endswith('/shipment/refreshCod')
        return httpx.Response(status)
    sps = SPSClient('test', 'test', httpx.MockTransport(handler))
    with pytest.raises(SPSError):
        await sps.refresh_cod('00123456789')
    await sps.close()
