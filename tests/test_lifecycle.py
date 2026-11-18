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
        self.dropoffs = []
        self.refreshes = []
        self.receipts = []
        self.pickups = []
        self.expired_reads = 0
        self.delivery_error = None
        self.return_error = None
        self.accept_error = None
        self.receipt_error = None
        self.dropoff_error = None
        self.dropoff_applies_before_error = False
        self.refresh_error = None
        self.pickup_error = None
        self.terminal_allowed = True
        self.courier_choices = None
        self.delivery_started = threading.Event()
        self.delivery_release = threading.Event()
        self.block_delivery = False
        self.dropoff_started = threading.Event()
        self.dropoff_release = threading.Event()
        self.block_dropoff = False

    async def lookup_pickup(self, pin):
        self.pickups.append(pin)
        if self.pickup_error:
            raise self.pickup_error
        parcel = await super().lookup_pickup(pin)
        if parcel:
            parcel["paymentTerminalAllowed"] = self.terminal_allowed
        return parcel

    async def refresh_cod(self, number):
        self.refreshes.append(number)
        if self.refresh_error:
            raise self.refresh_error
        return await super().refresh_cod(number)

    async def dropoff(self, number):
        self.dropoffs.append(number)
        self.dropoff_started.set()
        if self.block_dropoff:
            for _ in range(1000):
                if self.dropoff_release.is_set():
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("Test did not release the blocked demo dropoff")
        if self.dropoff_applies_before_error:
            await super().dropoff(number)
        if self.dropoff_error:
            raise self.dropoff_error
        if not self.dropoff_applies_before_error:
            return await super().dropoff(number)

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


def dropoff(client, headers, number="DEMODROPOFF01", **overrides):
    return client.post(PREFIX + "dropoff/confirm", json={
        "number": number, "operation_id": uuid.uuid4().hex, **overrides,
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
    result = lookup(client, headers, "222223")
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


def test_manual_entry_and_pickup_scan_defaults_persist_as_account_settings(workflow):
    client, _, warehouse, headers = workflow
    defaults = client.get(PREFIX + "warehouse").json()["settings"]
    assert defaults["manual_entry"] is False and defaults["pickup_scan_required"] is False
    response = client.post(PREFIX + "settings", json={"manual_entry": True, "pickup_scan_required": True}, headers=headers)
    assert response.status_code == 200
    restarted = type(warehouse)(warehouse.path)
    assert restarted.get_settings()["manual_entry"] is True
    assert restarted.get_settings()["pickup_scan_required"] is True


def test_optional_pickup_scan_omission_still_requires_pin_and_explicit_cash(workflow):
    client, demo, _, headers = workflow
    found = lookup(client, headers)
    assert found["scan_required"] is False
    body = {"lookup_id": found["lookup_id"], "operation_id": uuid.uuid4().hex}
    assert client.post(PREFIX + "pickup/confirm", json=body, headers=headers).status_code == 422
    body["cod_confirmed"] = True
    response = client.post(PREFIX + "pickup/confirm", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert demo.deliveries == [("111111", True, False)]


def test_server_scan_setting_is_enforced_even_when_changed_after_lookup(workflow):
    client, demo, _, headers = workflow
    found = lookup(client, headers)
    client.post(PREFIX + "settings", json={"pickup_scan_required": True}, headers=headers)
    body = {"lookup_id": found["lookup_id"], "operation_id": uuid.uuid4().hex, "cod_confirmed": True}
    assert client.post(PREFIX + "pickup/confirm", json=body, headers=headers).status_code == 409
    body["scanned_number"] = "01"
    assert client.post(PREFIX + "pickup/confirm", json=body, headers=headers).status_code == 409
    assert demo.deliveries == []
    body["scanned_number"] = found["parcel"]["trackNumber"]
    assert client.post(PREFIX + "pickup/confirm", json=body, headers=headers).status_code == 200


def test_explicit_mobile_refresh_obtains_authoritative_zero_without_terminal_flag(workflow):
    client, demo, warehouse, headers = workflow
    location = place(warehouse, "DEMOMOBILE01")
    found = lookup(client, headers, "222222")
    assert found["parcel"]["cod"] == 18.90
    refreshed = client.post(PREFIX + "pickup/refresh", json={"lookup_id": found["lookup_id"]}, headers=headers)
    assert refreshed.status_code == 200, refreshed.text
    result = refreshed.json()
    assert result["lookup_id"] == found["lookup_id"] and result["amount_changed"] is True
    assert result["parcel"]["cod"] == 0 and result["local"]["carrier_data"]["cod"] == 0
    assert result["local"]["location_id"] == location["id"]
    assert "222222" not in str(result) and "pin" not in result
    assert demo.refreshes == ["DEMOMOBILE01"] and demo.deliveries == []
    assert confirm(client, headers, result, paid_with_terminal=True).status_code == 422
    delivered = client.post(PREFIX + "pickup/confirm", json={"lookup_id": found["lookup_id"], "operation_id": uuid.uuid4().hex}, headers=headers)
    assert delivered.status_code == 200, delivered.text
    assert demo.deliveries == [("222222", False, False)] and demo.receipts == []
    assert warehouse.get_parcel("DEMOMOBILE01")["location_id"] is None
    assert client.post(PREFIX + "pickup/refresh", json={"lookup_id": found["lookup_id"]}, headers=headers).status_code == 409
    assert demo.refreshes == ["DEMOMOBILE01"]


def test_refresh_does_not_treat_not_paid_or_unchanged_amount_as_zero(workflow):
    client, demo, _, headers = workflow
    found = lookup(client, headers)
    response = client.post(PREFIX + "pickup/refresh", json={"lookup_id": found["lookup_id"]}, headers=headers)
    assert response.status_code == 200
    result = response.json()
    assert result["amount_changed"] is False and result["parcel"]["cod"] == 12.50
    assert confirm(client, headers, result).status_code == 422
    assert demo.deliveries == []


@pytest.mark.parametrize("failure_stage", ["check", "details", "identity", "stock"])
def test_failed_refresh_preserves_original_baseline_until_explicit_success(workflow, failure_stage, monkeypatch):
    client, demo, _, headers = workflow
    found = lookup(client, headers, "222222")
    if failure_stage == "check":
        demo.refresh_error = SPSError("Kontrola platby nedostupná")
        demo.parcels["DEMOMOBILE01"]["cod"] = 0
    elif failure_stage == "details":
        demo.pickup_error = SPSError("Neplatná odpoveď lookupu")
    elif failure_stage == "identity":
        demo.pins["222222"] = "DEMOEXPIRED01"
    else:
        original = demo.stored_shipments
        async def missing(number=""):
            return []
        monkeypatch.setattr(demo, "stored_shipments", missing)
    response = client.post(PREFIX + "pickup/refresh", json={"lookup_id": found["lookup_id"]}, headers=headers)
    assert response.status_code in {409, 502}, response.text
    demo.refresh_error = None
    demo.pickup_error = None
    demo.pins["222222"] = "DEMOMOBILE01"
    if failure_stage == "stock":
        monkeypatch.setattr(demo, "stored_shipments", original)
    assert demo.parcels["DEMOMOBILE01"]["cod"] == 0
    # The old lookup must still reject the newly changed amount after failure.
    assert confirm(client, headers, found).status_code == 409
    assert demo.deliveries == []
    refreshed = client.post(PREFIX + "pickup/refresh", json={"lookup_id": found["lookup_id"]}, headers=headers)
    assert refreshed.status_code == 200 and refreshed.json()["amount_changed"] is True
    assert confirm(client, headers, refreshed.json()).status_code == 200


def test_pickup_refresh_keeps_session_pin_private_and_rejects_another_login(workflow):
    client, demo, _, headers = workflow
    found = lookup(client, headers, "222222")
    other = login(client)
    response = client.post(PREFIX + "pickup/refresh", json={"lookup_id": found["lookup_id"]}, headers=other)
    assert response.status_code == 409 and demo.refreshes == []


def test_dropoff_scan_is_local_until_explicit_confirmation_then_handover_uses_sps_queue(workflow):
    client, demo, warehouse, headers = workflow
    number = "DEMODROPOFF01"
    registered = client.post(PREFIX + "parcels/register", json={"number": number, "scanned": True}, headers=headers)
    parcel = registered.json()["parcel"]
    assert parcel["scanned_at"] and parcel["received_at"] is None and parcel["dropoff_confirmed"] is False
    location = place(warehouse, number)
    assert demo.dropoffs == []
    assert number not in [p["trackNumber"] for p in client.get(PREFIX + "returns").json()["parcels"]]
    response = dropoff(client, headers, scanned=True)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["accepted"] is True and result["local_saved"] is True
    local = result["parcel"]
    assert local["dropoff_confirmed"] is True and local["received_at"]
    assert local["carrier_data"]["handoverKind"] == "outgoing"
    assert local["carrier_status"] == "accepted" and local["location_id"] == location["id"]
    outgoing = client.get(PREFIX + "returns").json()["outgoing"]
    assert number in [p["trackNumber"] for p in outgoing]
    assert demo.dropoffs == [number]
    prepare_return(client, headers, number)
    assert return_parcel(client, headers, number).status_code == 200
    assert warehouse.get_parcel(number)["location_id"] is None
    assert warehouse.get_parcel(number)["dropoff_confirmed"] is True
    assert dropoff(client, headers, number).status_code == 409


def test_successful_dropoff_same_or_new_id_never_resends_or_leaves_pending_journal(workflow):
    client, demo, warehouse, headers = workflow
    op = uuid.uuid4().hex
    first = dropoff(client, headers, operation_id=op)
    same = dropoff(client, headers, operation_id=op)
    new = dropoff(client, headers)
    assert all(r.status_code == 200 for r in (first, same, new))
    assert same.json()["replayed"] is True and new.json()["already_accepted"] is True
    assert demo.dropoffs == ["DEMODROPOFF01"]
    restarted = type(warehouse)(warehouse.path)
    assert restarted.get_parcel("DEMODROPOFF01")["dropoff_confirmed"] is True
    assert restarted.get_parcel("DEMODROPOFF01")["pending_operation"] is None
    assert dropoff(client, headers, "DEMODROPOFF02", operation_id=op).status_code == 409
    assert demo.dropoffs == ["DEMODROPOFF01"]


def test_dropoff_explicit_rejection_preserves_location_and_requires_new_attempt_id(workflow):
    client, demo, warehouse, headers = workflow
    warehouse.register_parcel("DEMODROPOFF01", scanned=True)
    location = place(warehouse, "DEMODROPOFF01")
    demo.dropoff_error = SPSError("SPS číslo neprijalo")
    op = uuid.uuid4().hex
    rejected = dropoff(client, headers, operation_id=op)
    assert rejected.status_code == 502 and rejected.json()["outcome"] == "rejected"
    local = warehouse.get_parcel("DEMODROPOFF01")
    assert local["carrier_status"] == "unconfirmed" and local["location_id"] == location["id"]
    assert local["received_at"] is None and local["dropoff_confirmed"] is False
    assert local["pending_operation"] is None
    demo.dropoff_error = None
    assert dropoff(client, headers, operation_id=op).status_code == 409
    assert dropoff(client, headers).status_code == 200
    assert len(demo.dropoffs) == 2


@pytest.mark.parametrize("actually_applied", [False, True])
def test_uncertain_dropoff_durable_guard_cannot_be_cleared_by_import_or_automatic_retry(workflow, actually_applied):
    client, demo, warehouse, headers = workflow
    warehouse.register_parcel("DEMODROPOFF01", scanned=True)
    location = place(warehouse, "DEMODROPOFF01")
    demo.dropoff_applies_before_error = actually_applied
    demo.dropoff_error = OperationUncertainError("Spojenie prerušilo odpoveď")
    op = uuid.uuid4().hex
    response = dropoff(client, headers, operation_id=op)
    assert response.status_code == 502 and response.json()["outcome"] == "uncertain"
    client.get(PREFIX + "warehouse/sync")
    client.get(PREFIX + "returns")
    local = type(warehouse)(warehouse.path).get_parcel("DEMODROPOFF01")
    assert local["carrier_status"] == "dropoff_uncertain"
    assert local["pending_operation"]["operation_id"] == op and local["pending_operation"]["kind"] == "dropoff"
    assert local["dropoff_confirmed"] is False and local["received_at"] is None
    assert local["location_id"] == location["id"]
    assert dropoff(client, headers, operation_id=op).status_code == 409
    assert dropoff(client, headers).status_code == 409
    assert demo.dropoffs == ["DEMODROPOFF01"]
    resolution = {"number": "DEMODROPOFF01", "kind": "dropoff", "outcome": "confirmed" if actually_applied else "failed",
                  "verified_in_sps": False, "note": "Výsledok bol osobne overený v portáli SPS."}
    assert client.post(PREFIX + "operations/resolve", json=resolution, headers=headers).status_code == 422
    resolution["verified_in_sps"] = True
    resolved = client.post(PREFIX + "operations/resolve", json=resolution, headers=headers)
    assert resolved.status_code == 200, resolved.text
    local = resolved.json()["parcel"]
    assert local["location_id"] == location["id"] and local["received_at"] is None
    assert local["dropoff_confirmed"] is actually_applied
    demo.dropoff_error = None
    demo.dropoff_applies_before_error = False
    assert dropoff(client, headers).status_code == 200
    assert len(demo.dropoffs) == (1 if actually_applied else 2)


def test_existing_sps_intake_cannot_be_reclassified_by_another_customer_dropoff(workflow):
    client, demo, warehouse, headers = workflow
    assert dropoff(client, headers, "DEMOSTORED01").status_code == 409
    # Also protect a remote parcel not yet imported by this application.
    demo.parcels["DEMODROPOFF99"] = demo._demo_parcel("DEMODROPOFF99", outgoing=True)
    assert dropoff(client, headers, "DEMODROPOFF99").status_code == 409
    local = warehouse.get_parcel("DEMODROPOFF99")
    assert local["carrier_status"] == "accepted" and local["received_at"] is None
    assert local["dropoff_confirmed"] is False and demo.dropoffs == []


def test_dropoff_full_code_intent_guard_rejects_suffix_or_location(workflow):
    client, demo, _, headers = workflow
    for number in ("01", "   01   ", "LOC-ABC123", "ABC/123"):
        assert dropoff(client, headers, number).status_code == 422
    assert demo.dropoffs == []


def test_dropoff_preflight_race_checks_local_state_atomically_before_remote_write(workflow, monkeypatch):
    client, demo, warehouse, headers = workflow
    number = "DEMODROPOFF01"
    warehouse.register_parcel(number)
    location = place(warehouse, number)
    async def stale_read(number=""):
        warehouse.mark_carrier(number, "accepted", details={"source": "concurrent receipt"})
        return []
    monkeypatch.setattr(demo, "stored_shipments", stale_read)
    response = dropoff(client, headers)
    assert response.status_code == 409
    assert demo.dropoffs == []
    local = warehouse.get_parcel(number)
    assert local["carrier_status"] == "accepted" and local["location_id"] == location["id"]
    assert local["pending_operation"] is None and local["dropoff_confirmed"] is False


def test_pending_customer_intake_blocks_duplicates_and_manual_resolution_while_running(workflow):
    client, demo, warehouse, headers = workflow
    demo.block_dropoff = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dropoff, client, headers)
        try:
            assert demo.dropoff_started.wait(timeout=3)
            assert dropoff(client, headers).status_code == 409
            response = client.post(PREFIX + "operations/resolve", json={
                "number": "DEMODROPOFF01", "kind": "dropoff", "outcome": "confirmed",
                "note": "Počas bežiaceho zápisu nemožno rozhodnúť výsledok.", "verified_in_sps": True,
            }, headers=headers)
            assert response.status_code == 409
        finally:
            demo.dropoff_release.set()
        assert future.result(timeout=5).status_code == 200
    assert demo.dropoffs == ["DEMODROPOFF01"]
    assert warehouse.get_parcel("DEMODROPOFF01")["pending_operation"] is None


def test_confirmed_local_dropoff_classifies_only_parcels_present_in_sps_queue(workflow):
    client, demo, warehouse, headers = workflow
    assert dropoff(client, headers).status_code == 200
    # Compact SPS metadata may omit direction; our confirmed intake is proof.
    demo.parcels["DEMODROPOFF01"]["handoverKind"] = "unknown"
    demo.parcels["DEMODROPOFF01"]["expiredDate"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    queue = client.get(PREFIX + "returns").json()
    assert "DEMODROPOFF01" in [p["trackNumber"] for p in queue["outgoing"]]
    assert warehouse.get_parcel("DEMODROPOFF01")["carrier_data"]["handoverKind"] == "outgoing"
    demo.parcels.pop("DEMODROPOFF01")
    assert "DEMODROPOFF01" not in [p["trackNumber"] for p in client.get(PREFIX + "returns").json()["parcels"]]


def test_received_and_scanned_dates_use_real_events_not_import_creation(workflow):
    client, _, warehouse, headers = workflow
    imported = warehouse.get_parcel("DEMOSTORED01")
    assert imported["created_at"] and imported["expiry_synced_at"]
    assert imported["received_at"] is None and imported["scanned_at"] is None
    local = client.post(PREFIX + "parcels/register", json={"number": "DEMO00000001"}, headers=headers).json()["parcel"]
    assert local["received_at"] is None and local["scanned_at"] is None
    scanned = client.post(PREFIX + "parcels/register", json={"number": "DEMO00000001", "scanned": True}, headers=headers).json()["parcel"]
    assert scanned["scanned_at"] and scanned["received_at"] is None
    client.post(PREFIX + "parcels/register", json={"number": "DEMO00000001", "scanned": True}, headers=headers)
    assert len([e for e in warehouse.history("DEMO00000001") if e["type"] == "scanned"]) == 1
    prepare_receipt(client, headers)
    assert accept(client, headers).status_code == 200
    assert warehouse.get_parcel("DEMO00000001")["received_at"]
