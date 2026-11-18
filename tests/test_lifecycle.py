"""Integration gates for explicit SPS handover and independent shelf state.

These tests use the local demo adapter exclusively. No live carrier request or
real parcel mutation is made.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
import uuid

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.main import create_app
from app.sps import DemoSPS, OperationUncertainError, SPSError


PASSWORD = "lifecycle-test-application-password"
HEADERS = {"Origin": "https://testserver", "X-Requested-With": "sps-app"}
PREFIX = "/sps/api/"


class ObservedDemo(DemoSPS):
    def __init__(self):
        super().__init__()
        self.deliveries = []
        self.returns = []
        self.acceptances = []
        self.receipts = []
        self.pickups = []
        self.expired_reads = 0
        self.delivery_error = None
        self.return_error = None
        self.accept_error = None
        self.receipt_error = None
        self.terminal_allowed = True
        self.courier_choices = None
        self.delivery_started = threading.Event()
        self.delivery_release = threading.Event()
        self.block_delivery = False

    async def lookup_pickup(self, pin):
        self.pickups.append(pin)
        parcel = await super().lookup_pickup(pin)
        if parcel:
            parcel["paymentTerminalAllowed"] = self.terminal_allowed
        return parcel

    async def deliver(self, pin, cod_confirmed=False, paid_with_terminal=False):
        self.deliveries.append((pin, cod_confirmed, paid_with_terminal))
        self.delivery_started.set()
        if self.block_delivery:
            for _ in range(1000):
                if self.delivery_release.is_set():
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("Test did not release the blocked demo delivery")
        if self.delivery_error:
            raise self.delivery_error
        return await super().deliver(pin, cod_confirmed, paid_with_terminal)

    async def return_parcel(self, number, courier_id, courier_password):
        self.returns.append((number, courier_id))
        if self.return_error:
            raise self.return_error
        return await super().return_parcel(number, courier_id, courier_password)

    async def expired_shipments(self):
        self.expired_reads += 1
        return await super().expired_shipments()

    async def accept(self, number, courier_id, courier_password):
        self.acceptances.append((number, courier_id))
        if self.accept_error:
            raise self.accept_error
        return await super().accept(number, courier_id, courier_password)

    async def receipt(self, number, update_number=True):
        self.receipts.append((number, update_number))
        if self.receipt_error:
            raise self.receipt_error
        return await super().receipt(number, update_number)

    async def couriers(self, numbers):
        return self.courier_choices if self.courier_choices is not None else await super().couriers(numbers)


def login(client):
    response = client.post(PREFIX + "login", json={"password": PASSWORD}, headers=HEADERS)
    assert response.status_code == 200, response.text
    return {**HEADERS, "X-CSRF-Token": response.json()["csrf"]}


@pytest.fixture
def workflow(tmp_path):
    demo = ObservedDemo()
    settings = Settings(public_url="https://testserver/sps", app_password=PASSWORD, data_dir=str(tmp_path / "data"))
    app = create_app(settings, demo, tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        headers = login(client)
        assert client.get(PREFIX + "warehouse/sync").status_code == 200
        yield client, demo, app.state.warehouse, headers


def lookup(client, headers, pin="111111"):
    response = client.post(PREFIX + "pickup/lookup", json={"pin": pin}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, headers, result, **overrides):
    body = {"lookup_id": result["lookup_id"], "scanned_number": result["parcel"]["trackNumber"],
            "operation_id": uuid.uuid4().hex, **overrides}
    return client.post(PREFIX + "pickup/confirm", json=body, headers=headers)


def place(warehouse, number, rack="A"):
    location = warehouse.create_locations(rack)[0]
    parcel = warehouse.get_parcel(number)
    warehouse.move_parcel(number, location["id"], parcel["version"], uuid.uuid4().hex, "Test obsluhy")
    return location


def prepare_return(client, headers, number="DEMOEXPIRED01"):
    response = client.post(PREFIX + "returns/couriers", json={"numbers": [number]}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def return_parcel(client, headers, number="DEMOEXPIRED01", **overrides):
    return client.post(PREFIX + "returns/confirm", json={
        "number": number, "courier_id": 1, "password": "demo", "operation_id": uuid.uuid4().hex, **overrides,
    }, headers=headers)


def prepare_receipt(client, headers, number="DEMO00000001"):
    assert client.get(PREFIX + "shipments").status_code == 200
    response = client.post(PREFIX + "couriers", json={"numbers": [number]}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def accept(client, headers, number="DEMO00000001", **overrides):
    return client.post(PREFIX + "accept", json={
        "number": number, "courier_id": 1, "password": "demo", "operation_id": uuid.uuid4().hex, **overrides,
    }, headers=headers)


def test_pin_lookup_is_read_only_and_shows_location_and_visible_suffix(workflow):
    client, demo, warehouse, headers = workflow
    location = place(warehouse, "DEMOSTORED01")
    result = lookup(client, headers)
    assert result["local"]["location_id"] == location["id"]
    assert result["local"]["suffix"] == "01"
    assert result["parcel"]["cod"] == 12.50
    assert demo.deliveries == []
    assert demo.returns == []
    assert demo.acceptances == []
    assert "111111" not in str(result)
    assert "111111" not in str(warehouse.history("DEMOSTORED01"))


def test_wrong_full_parcel_scan_cannot_release_even_with_correct_suffix(workflow):
    client, demo, warehouse, headers = workflow
    result = lookup(client, headers)
    response = confirm(client, headers, result, scanned_number="DEMOEXPIRED01", cod_confirmed=True)
    assert response.status_code == 409
    assert demo.deliveries == []
    assert warehouse.get_parcel("DEMOSTORED01")["carrier_status"] == "accepted"


def test_pin_lookup_token_is_bound_to_login_session(workflow):
    client, demo, _, headers = workflow
    result = lookup(client, headers)
    other_headers = login(client)
    response = confirm(client, other_headers, result, cod_confirmed=True)
    assert response.status_code == 409
    assert demo.deliveries == []


@pytest.mark.parametrize("change", ["amount", "pin", "missing"])
def test_fresh_carrier_lookup_rejects_changed_amount_or_identity(workflow, change):
    client, demo, _, headers = workflow
    result = lookup(client, headers)
    if change == "amount":
        demo.parcels["DEMOSTORED01"]["cod"] = 18.00
    elif change == "pin":
        demo.pins["111111"] = "DEMOEXPIRED01"
    else:
        demo.pins.pop("111111")
    response = confirm(client, headers, result, cod_confirmed=True)
    assert response.status_code == 409
    assert demo.deliveries == []


@pytest.mark.parametrize("flags", [{}, {"cod_confirmed": True, "paid_with_terminal": True}])
def test_cod_needs_exactly_one_explicit_payment_confirmation(workflow, flags):
    client, demo, _, headers = workflow
    response = confirm(client, headers, lookup(client, headers), **flags)
    assert response.status_code == 422
    assert demo.deliveries == []


@pytest.mark.parametrize("terminal", [False, True])
def test_confirmed_delivery_clears_only_current_location_preserving_history(workflow, terminal):
    client, demo, warehouse, headers = workflow
    location = place(warehouse, "DEMOSTORED01")
    result = lookup(client, headers)
    response = confirm(client, headers, result, cod_confirmed=not terminal, paid_with_terminal=terminal)
    assert response.status_code == 200, response.text
    assert response.json()["delivered"] is True
    assert response.json()["receipt_url"].endswith("DEMOSTORED01")
    parcel = warehouse.get_parcel("DEMOSTORED01")
    assert parcel["carrier_status"] == "delivered"
    assert parcel["location_id"] is None
    events = warehouse.history("DEMOSTORED01")
    assert any(e["type"] == "move" and e["to_location_id"] == location["id"] for e in events)
    assert any(e["type"] == "carrier_delivered" and e["from_location_id"] == location["id"] and e["details"]["payment"] == ("terminal" if terminal else "cash") for e in events)
    assert demo.deliveries == [("111111", not terminal, terminal)]
    assert demo.receipts == [("DEMOSTORED01", True)]
    pdf = client.get(PREFIX + "pickup/receipts/DEMOSTORED01")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    assert pdf.headers["cache-control"] == "no-store"


def test_account_without_terminal_cannot_select_terminal(workflow):
    client, demo, _, headers = workflow
    demo.terminal_allowed = False
    result = lookup(client, headers)
    assert result["parcel"]["paymentTerminalAllowed"] is False
    response = confirm(client, headers, result, paid_with_terminal=True)
    assert response.status_code == 422
    assert demo.deliveries == []


def test_zero_cod_delivery_requires_no_payment_and_creates_no_receipt(workflow):
    client, demo, warehouse, headers = workflow
    result = lookup(client, headers, "222222")
    response = confirm(client, headers, result)
    assert response.status_code == 200
    assert warehouse.get_parcel("DEMOEXPIRED01")["carrier_status"] == "delivered"
    assert demo.receipts == []


def test_browser_repeat_after_delivery_does_not_repeat_write_or_receipt_number(workflow):
    client, demo, _, headers = workflow
    result = lookup(client, headers)
    op = uuid.uuid4().hex
    first = confirm(client, headers, result, operation_id=op, cod_confirmed=True)
    same = confirm(client, headers, result, operation_id=op, cod_confirmed=True)
    new = confirm(client, headers, result, cod_confirmed=True)
    assert all(r.status_code == 200 for r in (first, same, new))
    assert same.json()["already_delivered"] is True
    assert len(demo.deliveries) == 1
    assert demo.receipts == [("DEMOSTORED01", True)]


@pytest.mark.parametrize("error", [SPSError("Doklad nedostupný"), OperationUncertainError("Číslo dokladu nejasné")])
def test_receipt_failure_preserves_delivery_and_retry_is_reprint_only(workflow, error):
    client, demo, warehouse, headers = workflow
    place(warehouse, "DEMOSTORED01")
    demo.receipt_error = error
    result = lookup(client, headers)
    response = confirm(client, headers, result, cod_confirmed=True)
    assert response.status_code == 200, response.text
    assert response.json()["delivered"] is True
    assert response.json()["receipt_pending"] is True
    assert warehouse.get_parcel("DEMOSTORED01")["location_id"] is None
    assert warehouse.get_parcel("DEMOSTORED01")["carrier_status"] == "delivered"
    demo.receipt_error = None
    retry = client.post(PREFIX + "pickup/receipt", json={"number": "DEMOSTORED01"}, headers=headers)
    assert retry.status_code == 200, retry.text
    again = client.post(PREFIX + "pickup/receipt", json={"number": "DEMOSTORED01"}, headers=headers)
    assert again.status_code == 200
    assert len(demo.deliveries) == 1
    assert demo.receipts == [("DEMOSTORED01", True), ("DEMOSTORED01", False)]


def test_cancelled_receipt_download_cannot_cause_a_second_delivery(workflow):
    client, demo, warehouse, headers = workflow
    demo.receipt_error = asyncio.CancelledError()
    # Cancellation can prevent the caller from receiving a response. Simulate
    # that boundary while allowing subsequent requests to inspect persisted state.
    with TestClient(client.app, base_url="https://testserver", raise_server_exceptions=False) as interrupted:
        interrupted_headers = login(interrupted)
        result = lookup(interrupted, interrupted_headers)
        response = confirm(interrupted, interrupted_headers, result, cod_confirmed=True)
        assert response.status_code == 500
    assert warehouse.get_parcel("DEMOSTORED01")["carrier_status"] == "delivered"
    assert warehouse.get_parcel("DEMOSTORED01")["location_id"] is None
    demo.receipt_error = None
    retry = client.post(PREFIX + "pickup/receipt", json={"number": "DEMOSTORED01"}, headers=headers)
    assert retry.status_code == 200, retry.text
    assert len(demo.deliveries) == 1
    assert demo.receipts == [("DEMOSTORED01", True), ("DEMOSTORED01", False)]


def test_receipt_routes_require_auth_csrf_and_a_completed_delivery(workflow):
    client, demo, _, headers = workflow
    assert client.post(PREFIX + "pickup/receipt", json={"number": "DEMOSTORED01"}, headers=headers).status_code == 409
    assert client.post(PREFIX + "pickup/receipt", json={"number": "DEMOSTORED01"}, headers=HEADERS).status_code == 403
    assert client.get(PREFIX + "pickup/receipts/DEMOSTORED01").status_code == 404
    result = lookup(client, headers)
    assert confirm(client, headers, result, cod_confirmed=True).status_code == 200
    assert client.post(PREFIX + "logout", json={}, headers=headers).status_code == 200
    assert client.get(PREFIX + "pickup/receipts/DEMOSTORED01").status_code == 401
    assert client.post(PREFIX + "pickup/receipt", json={"number": "DEMOSTORED01"}, headers=headers).status_code == 401
    assert len(demo.receipts) == 1


@pytest.mark.parametrize("staging", [False, True])
def test_direct_or_staged_return_requires_actual_carrier_confirmation(workflow, staging):
    client, demo, warehouse, headers = workflow
    location = place(warehouse, "DEMOEXPIRED01")
    if staging:
        assert client.post(PREFIX + "settings", json={"return_staging": True}, headers=headers).status_code == 200
        local = warehouse.get_parcel("DEMOEXPIRED01")
        response = client.post(PREFIX + "returns/stage", json={"parcels": [{"number": local["number"], "version": local["version"]}]}, headers=headers)
        assert response.status_code == 200
        assert warehouse.get_parcel("DEMOEXPIRED01")["staged"] is True
        assert warehouse.get_parcel("DEMOEXPIRED01")["location_id"] == location["id"]
    assert demo.returns == []
    prepare_return(client, headers)
    assert demo.returns == []
    response = return_parcel(client, headers)
    assert response.status_code == 200, response.text
    assert response.json()["returned"] is True
    parcel = warehouse.get_parcel("DEMOEXPIRED01")
    assert parcel["carrier_status"] == "returned" and parcel["location_id"] is None and parcel["staged"] is False
    assert any(e["type"] == "carrier_returned" and e["from_location_id"] == location["id"] for e in warehouse.history(parcel["number"]))
    assert return_parcel(client, headers).status_code == 200
    assert demo.returns == [("DEMOEXPIRED01", 1)]


def test_return_list_displays_carrier_block_and_never_submits_5555(workflow):
    client, demo, _, headers = workflow
    response = client.get(PREFIX + "returns")
    assert response.status_code == 200
    rows = {p["trackNumber"]: p for p in response.json()["parcels"]}
    assert rows["DEMOBLOCKED01"]["returnEligible"] is False
    blocked = client.post(PREFIX + "returns/couriers", json={"numbers": ["DEMOBLOCKED01"]}, headers=headers)
    assert blocked.status_code == 409
    assert return_parcel(client, headers, "DEMOBLOCKED01").status_code == 409
    assert demo.returns == []


@pytest.mark.parametrize("change", ["extension", "blocked", "removed"])
def test_return_rechecks_current_carrier_eligibility_after_courier_selection(workflow, change):
    client, demo, _, headers = workflow
    prepare_return(client, headers)
    reads = demo.expired_reads
    if change == "extension":
        demo.parcels["DEMOEXPIRED01"]["expiredDate"] = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    elif change == "blocked":
        demo.parcels["DEMOEXPIRED01"]["returnEligible"] = False
    else:
        demo.parcels.pop("DEMOEXPIRED01")
    assert return_parcel(client, headers).status_code == 409
    assert demo.expired_reads > reads
    assert demo.returns == []


@pytest.mark.parametrize("kind", ["deliver", "return", "accept"])
def test_uncertain_carrier_write_keeps_location_and_blocks_new_attempts(workflow, kind):
    client, demo, warehouse, headers = workflow
    if kind == "accept":
        number = "DEMO00000001"
        warehouse.register_parcel(number)
        location = place(warehouse, number)
        prepare_receipt(client, headers, number)
        demo.accept_error = OperationUncertainError("Výsledok príjmu nie je známy")
        action = lambda: accept(client, headers, number)
        writes = demo.acceptances
        expected = "accept_uncertain"
    elif kind == "deliver":
        number = "DEMOSTORED01"
        location = place(warehouse, number)
        result = lookup(client, headers)
        demo.delivery_error = OperationUncertainError("Výsledok výdaja nie je známy")
        action = lambda: confirm(client, headers, result, cod_confirmed=True)
        writes = demo.deliveries
        expected = "delivery_uncertain"
    else:
        number = "DEMOEXPIRED01"
        location = place(warehouse, number)
        prepare_return(client, headers)
        demo.return_error = OperationUncertainError("Výsledok vratky nie je známy")
        action = lambda: return_parcel(client, headers)
        writes = demo.returns
        expected = "return_uncertain"
    first = action()
    assert first.status_code == 502, first.text
    assert first.json()["outcome"] == "uncertain"
    assert warehouse.get_parcel(number)["carrier_status"] == expected
    assert warehouse.get_parcel(number)["location_id"] == location["id"]
    assert action().status_code == 409
    assert len(writes) == 1


def test_resolution_requires_operator_verification_and_keeps_audit_note(workflow):
    client, demo, warehouse, headers = workflow
    location = place(warehouse, "DEMOSTORED01")
    result = lookup(client, headers)
    demo.delivery_error = OperationUncertainError("Nejasný výsledok")
    assert confirm(client, headers, result, cod_confirmed=True).status_code == 502
    resolution = {"number": "DEMOSTORED01", "kind": "deliver", "outcome": "failed", "note": "Overené v portáli SPS, zásielka čaká na výdaj.", "verified_in_sps": False}
    assert client.post(PREFIX + "operations/resolve", json=resolution, headers=headers).status_code == 422
    resolution["verified_in_sps"] = True
    response = client.post(PREFIX + "operations/resolve", json=resolution, headers=headers)
    assert response.status_code == 200, response.text
    parcel = warehouse.get_parcel("DEMOSTORED01")
    assert parcel["carrier_status"] == "accepted" and parcel["location_id"] == location["id"]
    assert any(e["type"] == "carrier_verified" and e["details"]["note"] == resolution["note"] for e in warehouse.history("DEMOSTORED01"))
    demo.delivery_error = None
    assert confirm(client, headers, result, cod_confirmed=True).status_code == 200
    assert len(demo.deliveries) == 2


def test_confirmed_manual_resolution_clears_location_without_resending_carrier(workflow):
    client, demo, warehouse, headers = workflow
    place(warehouse, "DEMOEXPIRED01")
    prepare_return(client, headers)
    demo.return_error = OperationUncertainError("Nejasný výsledok")
    assert return_parcel(client, headers).status_code == 502
    response = client.post(PREFIX + "operations/resolve", json={
        "number": "DEMOEXPIRED01", "kind": "return", "outcome": "confirmed",
        "note": "SPS potvrdilo odovzdanie zásielky kuriérovi.", "verified_in_sps": True,
    }, headers=headers)
    assert response.status_code == 200, response.text
    assert warehouse.get_parcel("DEMOEXPIRED01")["carrier_status"] == "returned"
    assert warehouse.get_parcel("DEMOEXPIRED01")["location_id"] is None
    assert demo.returns == [("DEMOEXPIRED01", 1)]


def test_operator_cannot_resolve_a_carrier_request_that_is_still_running(workflow):
    client, demo, warehouse, headers = workflow
    result = lookup(client, headers)
    demo.block_delivery = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(confirm, client, headers, result, cod_confirmed=True)
        try:
            assert demo.delivery_started.wait(timeout=3), "Demo delivery did not start"
            response = client.post(PREFIX + "operations/resolve", json={
                "number": "DEMOSTORED01", "kind": "deliver", "outcome": "failed",
                "note": "Toto sa nesmie potvrdiť počas prebiehajúcej požiadavky.", "verified_in_sps": True,
            }, headers=headers)
            assert response.status_code == 409, response.text
        finally:
            demo.delivery_release.set()
        assert future.result(timeout=5).status_code == 200
    assert warehouse.get_parcel("DEMOSTORED01")["carrier_status"] == "delivered"


def test_courier_preference_updates_only_on_success_and_missing_last_requires_choice(workflow):
    client, demo, warehouse, headers = workflow
    initial = prepare_receipt(client, headers)
    assert initial["suggested"] == initial["first_id"] == 1
    assert accept(client, headers, courier_id=2, password="wrong").status_code == 502
    assert warehouse.get_settings()["last_courier_id"] is None
    assert accept(client, headers, courier_id=2).status_code == 200
    assert warehouse.get_settings()["last_courier_id"] == "2"
    next_selection = prepare_receipt(client, headers, "DEMO00000002")
    assert next_selection["suggested"] == 2 and next_selection["first_id"] == 1
    assert next_selection["warning_enabled"] is True
    assert client.post(PREFIX + "settings", json={"courier_warning": False}, headers=headers).status_code == 200
    assert prepare_receipt(client, headers, "DEMO00000002")["warning_enabled"] is False
    demo.courier_choices = [{"id": 1, "name": "Iný kuriér", "code": "DEMO"}]
    missing = prepare_receipt(client, headers, "DEMO00000002")
    assert missing["suggested"] is None and missing["requires_selection"] is True
    assert warehouse.get_settings()["last_courier_id"] == "2"
    assert accept(client, headers, "DEMO00000002", courier_id=2).status_code == 422


def test_courier_queue_separates_customer_dropoffs_from_expired_returns(workflow):
    client, demo, warehouse, _ = workflow
    response = client.get(PREFIX + "returns")
    assert response.status_code == 200
    data = response.json()
    assert {p['trackNumber'] for p in data['outgoing']} == {'DEMOOUTGOING01', 'DEMOOUTGOING02', 'DEMOOUTBLOCKED03'}
    assert {p['trackNumber'] for p in data['returns']} == {'DEMOEXPIRED01', 'DEMOBLOCKED01'}
    assert data['unclassified'] == []
    outgoing = warehouse.get_parcel('DEMOOUTGOING01')
    assert outgoing['carrier_status'] == 'accepted'
    assert outgoing['expires_at'] is None
    assert outgoing['carrier_data']['handoverKind'] == 'outgoing'
    assert demo.returns == [] and demo.acceptances == []


def test_manual_mixed_selection_can_handover_outgoing_and_expired_without_registration_or_scan_gate(workflow):
    client, demo, warehouse, headers = workflow
    for number in ['DEMOOUTGOING01', 'DEMOEXPIRED01']:
        place(warehouse, number, rack=number)
    prepared = client.post(PREFIX + 'returns/couriers', json={'numbers': ['DEMOOUTGOING01', 'DEMOEXPIRED01']}, headers=headers)
    assert prepared.status_code == 200
    assert demo.acceptances == [] and demo.returns == []
    for number, kind in [('DEMOOUTGOING01', 'outgoing'), ('DEMOEXPIRED01', 'return')]:
        response = return_parcel(client, headers, number)
        assert response.status_code == 200, response.text
        assert response.json()['handover_kind'] == kind
        parcel = warehouse.get_parcel(number)
        assert parcel['carrier_status'] == 'returned' and parcel['location_id'] is None
        event = next(e for e in warehouse.history(number) if e['type'] == 'carrier_returned')
        assert event['details']['handover_kind'] == kind
    assert demo.returns == [('DEMOOUTGOING01', 1), ('DEMOEXPIRED01', 1)]
    assert demo.acceptances == []
    assert return_parcel(client, headers, 'DEMOOUTGOING01').json()['already_returned'] is True
    assert len(demo.returns) == 2


@pytest.mark.parametrize('number', ['DEMOOUTBLOCKED03', 'CUSTOMER_NOT_REGISTERED'])
def test_blocked_or_only_locally_registered_outgoing_cannot_become_eligible(workflow, number):
    client, demo, warehouse, headers = workflow
    warehouse.register_parcel(number)
    place(warehouse, number)
    response = client.post(PREFIX + 'returns/couriers', json={'numbers': [number]}, headers=headers)
    assert response.status_code == 409
    assert return_parcel(client, headers, number).status_code == 409
    assert demo.returns == []
    assert warehouse.get_parcel(number)['location_id'] is not None


def test_outgoing_eligibility_is_rechecked_and_uncertain_handover_preserves_location(workflow):
    client, demo, warehouse, headers = workflow
    place(warehouse, 'DEMOOUTGOING01')
    prepare_return(client, headers, 'DEMOOUTGOING01')
    demo.parcels['DEMOOUTGOING01']['returnEligible'] = False
    assert return_parcel(client, headers, 'DEMOOUTGOING01').status_code == 409
    assert demo.returns == []
    demo.parcels['DEMOOUTGOING01']['returnEligible'] = True
    demo.return_error = OperationUncertainError('Nejasné odovzdanie')
    first = return_parcel(client, headers, 'DEMOOUTGOING01')
    assert first.status_code == 502 and first.json()['outcome'] == 'uncertain'
    assert warehouse.get_parcel('DEMOOUTGOING01')['carrier_status'] == 'return_uncertain'
    assert warehouse.get_parcel('DEMOOUTGOING01')['location_id'] is not None
    assert return_parcel(client, headers, 'DEMOOUTGOING01').status_code == 409
    assert demo.returns == [('DEMOOUTGOING01', 1)]


def test_unknown_carrier_type_is_visible_as_unknown_without_altering_eligibility(workflow):
    client, demo, _, headers = workflow
    demo.parcels['DEMOEXPIRED01']['handoverKind'] = 'unknown'
    response = client.get(PREFIX + 'returns')
    assert response.status_code == 200
    assert [p['trackNumber'] for p in response.json()['unclassified']] == ['DEMOEXPIRED01']
    prepare_return(client, headers, 'DEMOEXPIRED01')
    assert return_parcel(client, headers, 'DEMOEXPIRED01').status_code == 200
