import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.sps import SPSError

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
            raise SPSError("Nesprávne heslo kuriéra.")
        self.accepted.append((number, courier_id))


@pytest.fixture
def setup(tmp_path):
    (tmp_path / "index.html").write_text('<html lang="sk">Príjem balíkov</html>')
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text('console.log("test");')
    fake = FakeSPS()
    settings = Settings(public_url="https://testserver/sps", app_password=PASSWORD)
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


def test_full_receiving_flow_and_explicit_repeat(setup):
    client, fake = setup
    headers = login(client)
    prepare(client, headers)
    for number in ['jjd00001', 'SK0TEST2', 'JJD00001']:
        response = client.post('/sps/api/accept', json={"number": number, "courier_id": "7", "password": "courier-test"}, headers=headers)
        assert response.status_code == 200
        assert response.json()['accepted'] is True
    assert fake.accepted == [('JJD00001', 7), ('SK0TEST2', 7), ('JJD00001', 7)]


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


def test_demo_mode_does_not_need_sps_credentials():
    cfg = Settings(public_url='https://testserver', app_password=PASSWORD, demo=True)
    with TestClient(create_app(cfg), base_url='https://testserver') as client:
        r = client.post('/api/login', json={'password': PASSWORD}, headers=HEADERS)
        headers = {**HEADERS, 'X-CSRF-Token': r.json()['csrf']}
        assert len(client.get('/api/shipments').json()['shipments']) == 10
        client.post('/api/couriers', json={'numbers': ['DEMO00000001']}, headers=headers)
        assert client.post('/api/accept', json={'number': 'DEMO00000001', 'courier_id': 1, 'password': 'demo'}, headers=headers).status_code == 200
        assert len(client.get('/api/shipments').json()['shipments']) == 9
