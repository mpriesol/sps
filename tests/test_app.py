import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.sps import CourierCredentialsError, OperationUncertainError

PASSWORD = "test-application-password-only"
HEADERS = {"Origin": "https://testserver", "X-Requested-With": "sps-app"}


class FakeSPS:
    def __init__(self):
        self.accepted = []

    async def close(self):
        pass

    async def shipments(self):
        return [{"trackNumber": "JJD00001", "senderName": "Test sender", "tourDate": None, "tourCourier": 7},
                {"trackNumber": "SK0TEST2", "senderName": "Another sender", "tourDate": None, "tourCourier": 7}]

    async def couriers(self, numbers):
        assert numbers
        return [{"id": 7, "name": "Test Courier", "code": "TEST7"}]

    async def accept(self, number, courier_id, password):
        if password != "courier-test":
            raise CourierCredentialsError("Nesprávne heslo kuriéra.")
        self.accepted.append((number, courier_id))


@pytest.fixture
def setup(tmp_path):
    (tmp_path / "index.html").write_text('<html lang="sk">Príjem balíkov</html>')
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text('console.log("test");')
    fake = FakeSPS()
    settings = Settings(public_url="https://testserver/sps", app_password=PASSWORD, data_dir=str(tmp_path / 'data'))
    with TestClient(create_app(settings, fake, tmp_path), base_url="https://testserver") as client:
        yield client, fake


def login(client):
    response = client.post('/sps/api/login', json={"password": PASSWORD}, headers=HEADERS)
    assert response.status_code == 200
    return {**HEADERS, 'X-CSRF-Token': response.json()['csrf']}


def prepare(client, headers):
    assert client.get('/sps/api/shipments').status_code == 200
    response = client.post('/sps/api/couriers', json={"numbers": ["jjd00001", "SK0TEST2"]}, headers=headers)
    assert response.status_code == 200
    assert response.json()['suggested'] == 7


def test_full_receiving_flow_does_not_resend_confirmed_parcels(setup):
    client, fake = setup
    headers = login(client)
    prepare(client, headers)
    for number in ['jjd00001', 'SK0TEST2', 'JJD00001']:
        response = client.post('/sps/api/accept', json={"number": number, "courier_id": "7", "password": "courier-test"}, headers=headers)
        assert response.status_code == 200
        assert response.json()['accepted'] is True
    assert fake.accepted == [('JJD00001', 7), ('SK0TEST2', 7)]
    parcels = client.get('/sps/api/warehouse').json()['parcels']
    assert {p['carrier_status'] for p in parcels} == {'accepted'}


def test_authentication_csrf_and_logout(setup):
    client, _ = setup
    assert client.get('/sps/api/shipments').status_code == 401
    assert client.post('/sps/api/login', json={"password": PASSWORD}).status_code == 403
    headers = login(client)
    cookie = client.cookies.get('sps_receiving_session')
    assert cookie and PASSWORD not in cookie
    assert client.post('/sps/api/couriers', json={"numbers": ['JJD00001']}, headers=HEADERS).status_code == 403
    foreign = {**headers, "Origin": "https://another.example"}
    assert client.post('/sps/api/logout', json={}, headers=foreign).status_code == 403
    assert client.post('/sps/api/logout', json={}, headers=headers).status_code == 200
    assert client.get('/sps/api/session').status_code == 401


def test_only_selected_parcels_and_real_courier_ids_are_sent(setup):
    client, fake = setup
    headers = login(client)
    prepare(client, headers)
    for number, courier in [('UNKNOWN', 7), ('JJD00001', 999)]:
        r = client.post('/sps/api/accept', json={"number": number, "courier_id": courier, "password": 'courier-test'}, headers=headers)
        assert r.status_code in {409, 422}
    assert fake.accepted == []


def test_wrong_password_can_be_corrected_without_hidden_retry(setup):
    client, fake = setup
    headers = login(client)
    prepare(client, headers)
    response = client.post('/sps/api/accept', json={"number": 'JJD00001', "courier_id": 7, "password": 'sensitive-test-secret'}, headers=headers)
    assert response.status_code == 502
    assert 'sensitive-test-secret' not in response.text
    assert fake.accepted == []
    response = client.post('/sps/api/accept', json={"number": 'JJD00001', "courier_id": 7, "password": 'courier-test'}, headers=headers)
    assert response.status_code == 200


def test_validation_does_not_echo_password(setup):
    client, _ = setup
    headers = login(client)
    response = client.post('/sps/api/accept', json={"password": 'do-not-echo-this'}, headers=headers)
    assert response.status_code == 422
    assert 'do-not-echo-this' not in response.text


def test_prefix_static_assets_health_and_cookie(setup):
    client, _ = setup
    assert client.get('/healthz').status_code == 200
    assert client.get('/sps/').status_code == 200
    assert client.get('/sps/assets/app.js').status_code == 200
    assert client.get('/sps').url.path == '/sps/'
    response = client.post('/sps/api/login', json={"password": PASSWORD}, headers=HEADERS)
    cookie = response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=strict' in cookie and 'Path=/sps/' in cookie
    assert response.headers['cache-control'] == 'no-store'
    assert 'microphone=(self)' in response.headers['permissions-policy']


def test_demo_mode_does_not_need_sps_credentials(tmp_path):
    cfg = Settings(public_url='https://testserver', app_password=PASSWORD, demo=True, data_dir=str(tmp_path))
    with TestClient(create_app(cfg), base_url='https://testserver') as client:
        r = client.post('/api/login', json={'password': PASSWORD}, headers=HEADERS)
        headers = {**HEADERS, 'X-CSRF-Token': r.json()['csrf']}
        assert len(client.get('/api/shipments').json()['shipments']) == 10
        client.post('/api/couriers', json={'numbers': ['DEMO00000001']}, headers=headers)
        assert client.post('/api/accept', json={'number': 'DEMO00000001', 'courier_id': 1, 'password': 'demo'}, headers=headers).status_code == 200
        assert len(client.get('/api/shipments').json()['shipments']) == 9


def test_manual_success_remembers_password_for_another_phone_and_restart(tmp_path):
    cfg = Settings(public_url='https://testserver/sps', app_password=PASSWORD, data_dir=str(tmp_path))
    for first_run in (True, False):
        fake = FakeSPS()
        with TestClient(create_app(cfg, fake), base_url='https://testserver') as client:
            headers = login(client)
            prepare(client, headers)
            body = {'number': 'JJD00001', 'courier_id': 7}
            if first_run:
                body['password'] = 'courier-test'
            assert client.post('/sps/api/accept', json=body, headers=headers).json()['accepted']
            # New login, like a second mobile, reuses the same server-side password.
            headers = login(client)
            prepare(client, headers)
            response = client.post('/sps/api/accept', json={'number': 'SK0TEST2', 'courier_id': 7}, headers=headers)
            assert response.status_code == 200
            saved = client.get('/sps/api/courier-passwords')
            assert saved.json()['couriers'] == [{'id': 7, 'code': 'TEST7', 'name': 'Test Courier'}]
            assert 'courier-test' not in saved.text
    assert (tmp_path / 'couriers-demo.json').stat().st_mode & 0o777 == 0o600


def test_first_courier_uses_sps_order_even_when_tour_points_elsewhere(setup):
    client, fake = setup
    async def ordered(numbers):
        return [{'id': 8, 'code': 'TEST8', 'name': 'First'}, {'id': 7, 'code': 'TEST7', 'name': 'Second'}]
    fake.couriers = ordered
    headers = login(client)
    client.get('/sps/api/shipments')
    response = client.post('/sps/api/couriers', json={'numbers': ['JJD00001']}, headers=headers)
    assert response.json()['suggested'] == 8
    assert [c['id'] for c in response.json()['couriers']] == [8, 7]


def test_password_management_missing_bad_corrected_and_removed(setup):
    client, fake = setup
    assert client.get('/sps/api/courier-passwords').status_code == 401
    headers = login(client)
    prepare(client, headers)
    body = {'number': 'JJD00001', 'courier_id': 7}
    assert client.post('/sps/api/accept', json={**body, 'number': 'SK0TEST2'}, headers=headers).json()['needs_credentials']
    save = '/sps/api/courier-passwords/save'
    assert client.post(save, json={'courier_id': 7, 'password': 'bad'}, headers=HEADERS).status_code == 403
    assert client.post(save, json={'courier_id': 999, 'password': 'bad'}, headers=headers).status_code == 422
    assert client.post(save, json={'courier_id': 7, 'password': 'bad'}, headers=headers).status_code == 200
    rejected = client.post('/sps/api/accept', json=body, headers=headers)
    assert rejected.status_code == 502 and rejected.json()['needs_credentials']
    assert fake.accepted == []
    assert client.post('/sps/api/accept', json={**body, 'password': 'courier-test'}, headers=headers).status_code == 200
    assert client.post('/sps/api/accept', json=body, headers=headers).status_code == 200
    assert client.post('/sps/api/courier-passwords/remove', json={'courier_id': 7}, headers=headers).status_code == 200
    assert client.get('/sps/api/courier-passwords').json()['couriers'] == []
    assert client.post('/sps/api/accept', json={**body, 'number': 'SK0TEST2'}, headers=headers).json()['needs_credentials']


def test_failed_manual_password_is_not_remembered(setup):
    client, _ = setup
    headers = login(client)
    prepare(client, headers)
    client.post('/sps/api/accept', json={'number': 'JJD00001', 'courier_id': 7, 'password': 'wrong'}, headers=headers)
    assert client.get('/sps/api/courier-passwords').json()['couriers'] == []


def test_saving_password_does_not_accept_any_parcel(setup):
    client, fake = setup
    headers = login(client)
    prepare(client, headers)
    client.post('/sps/api/courier-passwords/save', json={'courier_id': 7, 'password': 'courier-test'}, headers=headers)
    assert fake.accepted == []


def test_last_successful_courier_is_selected_across_logins_and_can_warn(setup):
    client, fake = setup
    async def ordered(numbers):
        return [{'id': 8, 'code': 'TEST8', 'name': 'First'}, {'id': 7, 'code': 'TEST7', 'name': 'Second'}]
    fake.couriers = ordered
    headers = login(client)
    client.get('/sps/api/shipments')
    body = {'numbers': ['JJD00001', 'SK0TEST2']}
    first = client.post('/sps/api/couriers', json=body, headers=headers).json()
    assert first['suggested'] == 8 and first['warning_enabled'] is True
    # Choosing or saving a password is not successful use.
    client.post('/sps/api/courier-passwords/save', json={'courier_id': 7, 'password': 'courier-test'}, headers=headers)
    assert client.post('/sps/api/couriers', json=body, headers=headers).json()['suggested'] == 8
    client.post('/sps/api/accept', json={'number': 'JJD00001', 'courier_id': 7}, headers=headers)
    headers = login(client)
    client.get('/sps/api/shipments')
    selected = client.post('/sps/api/couriers', json=body, headers=headers).json()
    assert selected['suggested'] == 7 and selected['first_id'] == 8
    assert selected['last_courier_id'] == '7' and selected['requires_selection'] is False
    client.post('/sps/api/settings', json={'courier_warning': False}, headers=headers)
    assert client.post('/sps/api/couriers', json=body, headers=headers).json()['warning_enabled'] is False


def test_missing_last_courier_requires_manual_selection(setup):
    client, fake = setup
    headers = login(client)
    prepare(client, headers)
    client.post('/sps/api/accept', json={'number': 'JJD00001', 'courier_id': 7, 'password': 'courier-test'}, headers=headers)
    async def replacement(numbers):
        return [{'id': 9, 'code': 'TEST9', 'name': 'Replacement'}]
    fake.couriers = replacement
    response = client.post('/sps/api/couriers', json={'numbers': ['SK0TEST2']}, headers=headers).json()
    assert response['suggested'] is None and response['requires_selection'] is True
    assert response['first_id'] == 9 and response['last_courier_id'] == '7'


def test_failed_receipt_does_not_replace_last_courier(setup):
    client, fake = setup
    async def ordered(numbers):
        return [{'id': 8, 'code': 'TEST8', 'name': 'First'}, {'id': 7, 'code': 'TEST7', 'name': 'Second'}]
    fake.couriers = ordered
    headers = login(client)
    client.get('/sps/api/shipments')
    numbers = {'numbers': ['JJD00001', 'SK0TEST2']}
    client.post('/sps/api/couriers', json=numbers, headers=headers)
    assert client.post('/sps/api/accept', json={'number': 'JJD00001', 'courier_id': 7, 'password': 'wrong'}, headers=headers).status_code == 502
    result = client.post('/sps/api/couriers', json=numbers, headers=headers).json()
    assert result['suggested'] == 8 and result['last_courier_id'] is None


def test_uncertain_receipt_keeps_location_and_blocks_blind_retry(setup):
    client, fake = setup
    headers = login(client)
    prepare(client, headers)
    location = client.post('/sps/api/locations/create', json={'rack_name': 'A', 'shelf_count': 1}, headers=headers).json()['locations'][0]
    parcel = client.post('/sps/api/parcels/register', json={'number': 'JJD00001'}, headers=headers).json()['parcel']
    client.post('/sps/api/parcels/move', json={'number': parcel['number'], 'location_id': location['id'],
                'expected_version': parcel['version'], 'operation_id': 'move-0001'}, headers=headers)
    async def uncertain(*args):
        fake.accepted.append(args[:2])
        raise OperationUncertainError('Výsledok nie je známy.')
    fake.accept = uncertain
    body = {'number': 'JJD00001', 'courier_id': 7, 'password': 'courier-test', 'operation_id': 'accept-0001'}
    response = client.post('/sps/api/accept', json=body, headers=headers)
    assert response.status_code == 502 and response.json()['outcome'] == 'uncertain'
    again = client.post('/sps/api/accept', json={**body, 'operation_id': 'accept-0002'}, headers=headers)
    assert again.status_code == 409 and len(fake.accepted) == 1
    p = client.get('/sps/api/warehouse').json()['parcels'][0]
    assert p['location_id'] == location['id'] and p['carrier_status'] == 'accept_uncertain'


def test_app_password_and_csrf_protect_all_local_writes(setup):
    client, _ = setup
    assert client.get('/sps/api/warehouse').status_code == 401
    headers = login(client)
    assert client.post('/sps/api/locations/create', json={'rack_name': 'A'}, headers=HEADERS).status_code == 403
    assert client.post('/sps/api/settings', json={'last_courier_id': '999'}, headers=headers).status_code == 422
    session = client.get('/sps/api/session').json()
    assert session['account'] == 'demo' and session['actor']
