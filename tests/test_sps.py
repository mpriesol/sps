import json

import httpx
import pytest

from app.sps import SPSClient, SPSError


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
