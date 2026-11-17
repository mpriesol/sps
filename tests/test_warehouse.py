"""Invariants that matter when several people scan the same physical stock."""
import base64
import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from app.warehouse import Warehouse, WarehouseError


@pytest.fixture
def store(tmp_path):
    return Warehouse(tmp_path / "warehouse-test.sqlite3")


def locations(store):
    return store.create_locations("A", 2)


def placed(store, number, location):
    parcel = store.register_parcel(number)
    return store.move_parcel(number, location["id"], parcel["version"], "initial-" + number)["parcel"]


def test_locations_are_persistent_and_rename_preserves_printed_barcode(store):
    rows = store.create_locations("Regál A", 2, 2)
    assert [row["name"] for row in rows] == ["Regál A1-1", "Regál A1-2", "Regál A2-1", "Regál A2-2"]
    assert len({row["barcode"] for row in rows}) == 4
    first = rows[0]
    renamed = store.update_location(first["id"], "Horná polica vľavo", "Pri okne", first["version"])
    assert renamed["barcode"] == first["barcode"]
    reloaded = Warehouse(store.path).snapshot()
    assert next(row for row in reloaded["locations"] if row["id"] == first["id"])["name"] == "Horná polica vľavo"
    placed(store, "000001AZ", first)
    with pytest.raises(WarehouseError, match="Najskôr ich presuňte"):
        store.delete_location(first["id"])
    assert store.get_parcel("000001AZ")["suffix"] == "AZ"


def test_many_parcels_can_share_location_and_acceptance_does_not_erase_it(store):
    shelf = locations(store)[0]
    first = placed(store, "000001AZ", shelf)
    placed(store, "000002AZ", shelf)
    assert store.snapshot()["locations"][0]["count"] == 2
    assert first["carrier_status"] == "unconfirmed"
    uncertain = store.mark_carrier(first["number"], "accept_uncertain")
    assert uncertain["location_id"] == shelf["id"]
    accepted = store.mark_carrier(first["number"], "accepted")
    assert accepted["location_id"] == shelf["id"]
    pending_delivery = store.mark_carrier(first["number"], "delivery_uncertain")
    assert pending_delivery["location_id"] == shelf["id"]
    delivered = store.mark_carrier(first["number"], "delivered")
    assert delivered["location_id"] is None
    assert store.register_parcel(first["number"])["carrier_status"] == "delivered"
    with pytest.raises(WarehouseError, match="už bol vydaný"):
        store.move_parcel(first["number"], shelf["id"], delivered["version"], "cannot-resurrect")


def test_two_devices_cannot_silently_overwrite_a_move(store):
    first, second = locations(store)
    parcel = store.register_parcel("000001AZ")
    def move(location, operation):
        try:
            return store.move_parcel(parcel["number"], location["id"], parcel["version"], operation)
        except WarehouseError as exc:
            return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(move, first, "device-one"), pool.submit(move, second, "device-two")]
        outcomes = [task.result() for task in tasks]
    assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
    assert outcomes.count(409) == 1
    assert len([event for event in store.history(parcel["number"]) if event["type"] == "move"]) == 1


def test_network_retry_of_same_move_is_idempotent_even_after_a_later_move(store):
    first, second = locations(store)
    parcel = store.register_parcel("000001AZ")
    original = store.move_parcel(parcel["number"], first["id"], parcel["version"], "first-scan")
    later = store.move_parcel(parcel["number"], second["id"], original["parcel"]["version"], "second-scan")
    assert store.move_parcel(parcel["number"], first["id"], parcel["version"], "first-scan") == original
    assert store.get_parcel(parcel["number"])["location_id"] == second["id"]
    assert len(store.history(parcel["number"])) == 3
    with pytest.raises(WarehouseError, match="inej operácii"):
        store.move_parcel(parcel["number"], first["id"], later["parcel"]["version"], "second-scan")


def test_bulk_move_is_atomic_if_one_parcel_changed(store):
    source, target = locations(store)
    one, two = placed(store, "001", source), placed(store, "002", source)
    stale_items = [{"number": row["number"], "version": row["version"]} for row in (one, two)]
    store.move_parcel(two["number"], None, two["version"], "other-device")
    with pytest.raises(WarehouseError):
        store.move_location(source["id"], target["id"], stale_items, "bulk-stale")
    assert store.get_parcel(one["number"])["location_id"] == source["id"]
    two = store.get_parcel(two["number"])
    store.move_parcel(two["number"], source["id"], two["version"], "replace-parcel")
    items = [{"number": number, "version": store.get_parcel(number)["version"]} for number in ("001", "002")]
    result = store.move_location(source["id"], target["id"], items, "bulk-success")
    assert len(result["events"]) == 2
    assert all(row["location_id"] == target["id"] for row in result["parcels"])
    assert store.move_location(source["id"], target["id"], items, "bulk-success") == result
    assert store.snapshot()["locations"][0]["count"] == 0


def test_undo_only_reverses_latest_local_movement_not_carrier_state(store):
    first, second = locations(store)
    parcel = placed(store, "001", first)
    moved = store.move_parcel("001", second["id"], parcel["version"], "move-to-second")
    undone = store.undo_move(moved["event"]["id"], moved["parcel"]["version"], "undo-second")
    assert undone["parcel"]["location_id"] == first["id"]
    store.mark_carrier("001", "accepted")
    with pytest.raises(WarehouseError):
        store.undo_move(moved["event"]["id"], store.get_parcel("001")["version"], "stale-undo")
    assert store.get_parcel("001")["carrier_status"] == "accepted"


def test_movement_history_keeps_label_of_deleted_empty_location(store):
    first, second = locations(store)
    parcel = placed(store, "001", first)
    moved = store.move_parcel("001", second["id"], parcel["version"], "move-and-delete")
    store.delete_location(first["id"])
    event = next(row for row in store.history("001") if row["id"] == moved["event"]["id"])
    assert event["details"]["from_location_name"] == "A1"
    assert event["details"]["to_location_name"] == "A2"


def test_inventory_reports_missing_and_misplaced_without_changing_stock(store):
    first, second = locations(store)
    for number in ("001", "002"):
        placed(store, number, first)
    placed(store, "003", second)
    before = store.snapshot()
    result = store.check_inventory(first["id"], ["001", "001", "003", "999"])
    assert [row["number"] for row in result["matched"]] == ["001"]
    assert [row["number"] for row in result["missing"]] == ["002"]
    assert [row["number"] for row in result["unexpected"]] == ["003", "999"]
    assert result["unexpected"][0]["location_id"] == second["id"]
    assert result["unexpected"][1]["unknown"] is True
    assert store.snapshot() == before


def test_carrier_guard_survives_restart_and_blocks_double_remote_write(store):
    store.register_parcel("001")
    operation = store.begin_carrier_operation("001", "accept", "accept-request")
    assert not operation["replayed"]
    restarted = Warehouse(store.path)
    pending = restarted.get_parcel("001")["pending_operation"]
    assert pending["kind"] == "accept" and pending["outcome"] == "pending"
    assert restarted.snapshot()["parcels"][0]["pending_operation"]["operation_id"] == "accept-request"
    with pytest.raises(WarehouseError, match="čakáme na overenie"):
        restarted.begin_carrier_operation("001", "accept", "second-request")
    restarted.finish_carrier_operation("accept-request", "uncertain", carrier_status="accept_uncertain")
    with pytest.raises(WarehouseError):
        restarted.begin_carrier_operation("001", "accept", "third-request")
    result = restarted.resolve_carrier_operation("001", "accept", "confirmed", "Zariadenie A", "Overené v portáli SPS")
    assert result["parcel"]["carrier_status"] == "accepted"
    assert result["parcel"]["pending_operation"] is None
    assert restarted.begin_carrier_operation("001", "accept", "fourth-request")["replayed"]
    assert any(e["type"] == "carrier_verified" and e["details"]["verified_manually"] for e in store.history("001"))


def test_failed_courier_password_can_retry_with_new_operation(store):
    store.register_parcel("001")
    store.begin_carrier_operation("001", "accept", "bad-password")
    store.finish_carrier_operation("bad-password", "failed", "Nesprávne heslo.")
    assert store.begin_carrier_operation("001", "accept", "bad-password")["replayed"]
    assert not store.begin_carrier_operation("001", "accept", "correct-password")["replayed"]
    finished = store.finish_carrier_operation("correct-password", "confirmed", carrier_status="accepted")
    assert finished["parcel"]["carrier_status"] == "accepted"


def test_journal_and_carrier_status_finish_atomically(store):
    store.register_parcel("001")
    store.begin_carrier_operation("001", "accept", "atomic-request")
    with pytest.raises(WarehouseError):
        store.finish_carrier_operation("atomic-request", "confirmed", carrier_status="made-up-status")
    with pytest.raises(WarehouseError):
        store.begin_carrier_operation("001", "accept", "atomic-request")
    assert store.get_parcel("001")["carrier_status"] == "unconfirmed"
    store.finish_carrier_operation("atomic-request", "confirmed", carrier_status="accepted")
    assert store.get_parcel("001")["carrier_status"] == "accepted"


def test_authoritative_sync_resolves_receipt_not_missing_delivery_or_expiry(store):
    shelf = locations(store)[0]
    placed(store, "001", shelf)
    store.begin_carrier_operation("001", "accept", "receipt-unknown")
    store.finish_carrier_operation("receipt-unknown", "uncertain", carrier_status="accept_uncertain")
    store.sync_carrier_parcels([{"trackNumber": "001", "senderName": "Sender", "expiredDate": "2026-12-01", "returnEligible": False}])
    parcel = store.get_parcel("001")
    assert parcel["carrier_status"] == "accepted"
    assert parcel["location_id"] == shelf["id"]
    assert parcel["expires_at"] == "2026-12-01"
    assert parcel["expiry_source"] == "SPS"
    assert store.begin_carrier_operation("001", "accept", "receipt-retry")["replayed"]
    store.begin_carrier_operation("001", "deliver", "delivery-unknown")
    store.finish_carrier_operation("delivery-unknown", "uncertain", carrier_status="delivery_uncertain")
    store.sync_carrier_parcels([])
    assert store.get_parcel("001")["carrier_status"] == "delivery_uncertain"
    assert store.get_parcel("001")["location_id"] == shelf["id"]
    store.sync_carrier_parcels([{"trackNumber": "001", "expiredDate": "2026-12-04"}])
    assert store.get_parcel("001")["carrier_status"] == "delivery_uncertain"
    assert store.get_parcel("001")["expires_at"] == "2026-12-04"


def test_staging_is_optional_and_does_not_fake_handover(store):
    shelf = locations(store)[0]
    parcel = placed(store, "001", shelf)
    with pytest.raises(WarehouseError, match="vypnutá"):
        store.set_staged([{"number": "001", "version": parcel["version"]}])
    store.update_settings({"return_staging": True})
    staged = store.set_staged([{"number": "001", "version": parcel["version"]}])[0]
    assert staged["staged"] is True
    assert staged["location_id"] == shelf["id"]
    assert staged["carrier_status"] == "unconfirmed"
    returned = store.mark_carrier("001", "returned")
    assert returned["location_id"] is None and returned["staged"] is False


def jpeg_data_url():
    image = Image.new("RGB", (2000, 1000), "red")
    stream = io.BytesIO()
    exif = Image.Exif()
    exif[305] = "private camera metadata"
    image.save(stream, "JPEG", exif=exif)
    return "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode()


def test_photos_are_private_reencoded_and_do_not_change_carrier_or_location(store):
    shelf = locations(store)[0]
    parcel = placed(store, "001", shelf)
    with pytest.raises(WarehouseError, match="vypnuté"):
        store.store_photo("001", jpeg_data_url())
    store.update_settings({"photos": True})
    saved = store.store_photo("001", jpeg_data_url())
    assert saved["location_id"] == parcel["location_id"]
    assert saved["carrier_status"] == "unconfirmed"
    assert saved["photo_url"].startswith("./api/parcels/001/photo?")
    path = store.photo_path("001")
    with Image.open(path) as image:
        assert image.width == 1600
        assert not image.getexif()
    with pytest.raises(WarehouseError):
        store.store_photo("001", "data:image/jpeg;base64,not-a-picture")
    assert store.photo_path("001") == path
    store.store_photo("001", jpeg_data_url())
    assert not path.exists()
    assert len(list(store.photo_dir.iterdir())) == 1


def test_account_databases_and_preferences_do_not_leak(tmp_path):
    first, second = Warehouse(tmp_path / "first.sqlite3"), Warehouse(tmp_path / "second.sqlite3")
    first.register_parcel("001")
    first.update_settings({"last_courier_id": "7", "courier_warning": False})
    assert second.get_parcel("001") is None
    assert second.get_settings()["last_courier_id"] is None
    assert Warehouse(first.path).get_settings()["last_courier_id"] == "7"


def test_public_route_cannot_set_courier_preference_or_fake_accepted_status(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.warehouse_api import register_warehouse_routes
    app = FastAPI()
    register_warehouse_routes(app, store, lambda request: {"actor": "Test"})
    with TestClient(app) as client:
        assert client.post("/api/settings", json={"last_courier_id": "7"}).status_code == 422
        assert client.post("/api/parcels/register", json={"number": "001", "carrier_status": "accepted"}).status_code == 422
        assert client.post("/api/parcels/register", json={"number": "001"}).json()["parcel"]["carrier_status"] == "unconfirmed"
        created = client.post("/api/locations/create", json={"rack_name": "A", "shelf_count": 2, "places_per_shelf": 0})
        assert created.status_code == 200
        assert len(created.json()["locations"]) == 2
        assert client.get("/api/warehouse").json()["parcels"][0]["suffix"] == "01"


def test_rack_rename_is_atomic_and_preserves_location_names_and_barcodes(store):
    original = store.create_locations("A", 2, 2)
    store.create_locations("B", 1)
    with pytest.raises(WarehouseError, match="už existuje"):
        store.rename_rack("A", "B")
    renamed = store.rename_rack("A", "Pri okne")
    assert len(renamed) == 4
    assert [row["name"] for row in renamed] == [row["name"] for row in original]
    assert [row["barcode"] for row in renamed] == [row["barcode"] for row in original]
    assert all(row["rack"] == "Pri okne" for row in renamed)


def test_unknown_location_barcode_cannot_be_registered_as_a_parcel(store):
    with pytest.raises(WarehouseError, match="kód miesta"):
        store.register_parcel("LOC-UNKNOWN00000")
    assert not store.snapshot()["parcels"]


def test_scanner_cannot_move_parcel_during_inflight_handover(store):
    first, second = locations(store)
    parcel = placed(store, "001", first)
    store.mark_carrier("001", "accepted")
    parcel = store.get_parcel("001")
    store.begin_carrier_operation("001", "deliver", "handover-pending")
    with pytest.raises(WarehouseError, match="prebieha odovzdanie"):
        store.move_parcel("001", second["id"], parcel["version"], "concurrent-move")
    store.finish_carrier_operation("handover-pending", "failed")
    assert store.move_parcel("001", second["id"], parcel["version"], "move-after-reject")["parcel"]["location_id"] == second["id"]
