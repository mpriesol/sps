import test from 'node:test';
import assert from 'node:assert/strict';
import { canConfirmPickup, chooseCourier, handoverEligible, handoverGroups, parcelIntakeTime, reconcileDropoffAttempt, reconcilePickupAttempt, reconcileReceiptItem, routeWarehouseScan, restoreDraft, serializeDraft, suffixCollisions, validDropoffCode, warehouseVoiceContext } from './warehouse-domain.js';

test('last courier and explicit batch choice survive list order changes; missing saved courier is never silently replaced', () => {
  const response = { couriers: [{ id: 1 }, { id: 2 }], suggested: 2, first_id: 1 };
  assert.equal(chooseCourier(response), '2');
  assert.equal(chooseCourier({ ...response, suggested: 1 }, '2'), '2');
  assert.equal(chooseCourier({ ...response, requires_selection: true, suggested: null }), '');
  assert.equal(chooseCourier(response, 'missing'), '');
});

test('location scans are routed by stable barcode, independent of visible name and leading zero parcel IDs', () => {
  const locations = [{ id: 'p1', barcode: 'LOC:ABC', name: 'New name' }];
  assert.equal(routeWarehouseScan('loc:abc', locations, null).kind, 'location');
  assert.equal(routeWarehouseScan('LOC:ABC', locations, '00012').number, '00012');
  assert.equal(routeWarehouseScan('00012', locations, null).code, '00012');
  assert.equal(routeWarehouseScan('LOC:MISSING', locations, null).kind, 'unknown-location');
});

test('simple receipt draft never serializes credentials and interrupted writes cannot be silently retried', () => {
  const selected = new Map([['00012', { trackNumber: '00012', senderName: 'A', state: 'sending', password: 'secret', operationId: 'op1' }]]);
  const raw = serializeDraft(selected, 3, true);
  assert.ok(!raw.includes('secret'));
  const restored = restoreDraft(raw);
  assert.equal(restored.parcels[0].trackNumber, '00012');
  assert.equal(restored.parcels[0].state, 'uncertain');
  assert.equal(restored.courierId, '3');
  assert.equal(restoreDraft('bad'), null);
  assert.equal(Object.hasOwn(restored, 'placeDuringReceipt'), false);
  const legacy = restoreDraft(JSON.stringify({ version: 2, placeDuringReceipt: true, parcels: [] }));
  assert.equal(Object.hasOwn(legacy, 'placeDuringReceipt'), false);
});

test('courier grouping never labels missing carrier classification as an expired return', () => {
  const rows = [{ trackNumber: 'DROP01', handoverKind: 'outgoing' }, { trackNumber: 'EXP02', handoverKind: 'return' }, { trackNumber: 'OTHER03' }];
  const groups = handoverGroups(rows);
  assert.deepEqual(groups.outgoing, [rows[0]]);
  assert.deepEqual(groups.returns, [rows[1]]);
  assert.deepEqual(groups.unknown, [rows[2]]);
});

test('manual/select-all handover eligibility excludes 5555, uncertain, pending and terminal parcels', () => {
  const remote = { trackNumber: 'DROP01', returnEligible: true, statusCode: 441 };
  assert.equal(handoverEligible(remote, { carrier_status: 'accepted' }), true);
  for (const status of ['unconfirmed', 'accept_uncertain', 'delivery_uncertain', 'return_uncertain', 'delivered', 'returned']) {
    assert.equal(handoverEligible(remote, { carrier_status: status }), false);
  }
  assert.equal(handoverEligible(remote, { carrier_status: 'accepted', pending_operation: { kind: 'return' } }), false);
  assert.equal(handoverEligible({ ...remote, statusCode: 5555 }, { carrier_status: 'accepted' }), false);
  assert.equal(handoverEligible({ ...remote, returnEligible: false }, { carrier_status: 'accepted' }), false);
  assert.equal(handoverEligible(undefined), false);
});

function voiceWorkspace(overrides = {}) {
  return { view: 'store', state: { shipments: new Map(), selected: new Map() }, parcels: [
    { number: 'SHELF01', carrier_status: 'accepted' }, { number: 'OTHER01', carrier_status: 'accepted' },
    { number: 'DONE01', carrier_status: 'delivered' }], locations: [{ barcode: 'LOC:A', name: 'A1' }],
    returns: [], returnScans: new Set(), receiptPlacementEnabled: () => false, ...overrides };
}

test('voice ambiguity considers all physical parcels even in inventory or selected handover subset', () => {
  const inventory = voiceWorkspace({ inventory: { location_id: 'a', numbers: new Set(['SHELF01']) } });
  const context = warehouseVoiceContext(inventory);
  assert.deepEqual([...context.candidates.keys()].sort(), ['OTHER01', 'SHELF01']);
  assert.deepEqual([...context.selected.keys()], ['SHELF01']);
  assert.equal(context.enabled, true);
  const handover = warehouseVoiceContext(voiceWorkspace({ view: 'returns', returns: [{ trackNumber: 'DROP01', returnEligible: false }], returnScans: new Set(['SHELF01']) }));
  assert.deepEqual([...handover.candidates.keys()].sort(), ['DROP01', 'OTHER01', 'SHELF01']);
  assert.deepEqual([...handover.selected.keys()], ['SHELF01']);
});

test('voice pickup requires the full code and context key changes with target, photos and view', () => {
  const workspace = voiceWorkspace({ view: 'pickup', settings: { pickup_scan_required: true }, pickup: { lookup_id: 'one', parcel: { trackNumber: 'SHELF01' } } });
  const initial = warehouseVoiceContext(workspace);
  assert.equal(initial.requireFullCode, true);
  assert.equal(initial.allowLocationNames, false);
  assert.equal(initial.enabled, true);
  for (const change of [{ pickup: { lookup_id: 'two', parcel: { trackNumber: 'OTHER01' } } }, { photoBusy: true, photoGeneration: 2 }, { viewGeneration: 1 }, { activeNumber: 'OTHER01' }]) {
    assert.notEqual(warehouseVoiceContext({ ...workspace, ...change }).key, initial.key);
  }
  assert.equal(warehouseVoiceContext({ ...workspace, pickup: null }).enabled, false);
  assert.equal(warehouseVoiceContext({ ...workspace, pickup: { ...workspace.pickup, uncertain: true } }).enabled, false);
  assert.equal(warehouseVoiceContext({ ...workspace, photoBusy: true }).enabled, false);
  const optional = warehouseVoiceContext({ ...workspace, settings: {} });
  assert.equal(optional.enabled, false);
  assert.notEqual(optional.key, initial.key);
});

test('pickup skips the optional scan only, while cash and uncertain/stale outcomes still block release', () => {
  const pickup = { parcel: { trackNumber: 'SHELF01', cod: 0 }, verified: false };
  assert.equal(canConfirmPickup(pickup), true);
  assert.equal(canConfirmPickup(pickup, true), false);
  assert.equal(canConfirmPickup({ ...pickup, verified: true }, true), true);
  const cash = { ...pickup, parcel: { ...pickup.parcel, cod: 12.50 } };
  assert.equal(canConfirmPickup(cash), false);
  assert.equal(canConfirmPickup({ ...cash, codConfirmed: true }), true);
  for (const change of [{ uncertain: true }, { detailsStale: true }]) assert.equal(canConfirmPickup({ ...cash, codConfirmed: true, ...change }), false);
  assert.equal(canConfirmPickup({ ...pickup, parcel: { ...pickup.parcel, cod: 'invalid' } }), false);
  assert.equal(canConfirmPickup({ ...pickup, parcel: { ...pickup.parcel, cod: -1 } }), false);
  assert.equal(canConfirmPickup(null), false);
});

test('pickup intake time uses explicit receipt or scan events, never import or SPS refresh timestamps', () => {
  const imported = { created_at: '2026-11-18T10:00:00Z', expiry_synced_at: '2026-11-18T11:00:00Z', received_at: null, scanned_at: null };
  assert.equal(parcelIntakeTime(imported), null);
  assert.deepEqual(parcelIntakeTime({ ...imported, scanned_at: '2026-11-17T09:00:00Z' }), { label: 'Naskenované', value: '2026-11-17T09:00:00Z' });
  assert.deepEqual(parcelIntakeTime({ ...imported, received_at: '2026-11-17T10:00:00Z', scanned_at: '2026-11-17T09:00:00Z' }), { label: 'Prijaté', value: '2026-11-17T10:00:00Z' });
});

test('customer drop-off takes a whole new code without accepting shelf barcodes or short suffixes', () => {
  assert.equal(validDropoffCode('0000123456789'), true);
  assert.equal(validDropoffCode('DEMODROPOFF01'), true);
  for (const code of ['01', 'ABC12', 'LOC-ABC123', 'LOC:ABC123', 'ABC/123', 'A'.repeat(101)]) assert.equal(validDropoffCode(code), false);
  const workspace = voiceWorkspace({ view: 'dropoff', state: { shipments: new Map(), selected: new Map(), finished: true } });
  const context = warehouseVoiceContext(workspace);
  assert.equal(context.enabled, true);
  assert.equal(context.requireFullCode, true);
  assert.equal(context.allowUnknownFullCode, true);
  assert.equal(context.allowLocationNames, false);
  assert.notEqual(warehouseVoiceContext({ ...workspace, dropoff: { number: 'DEMODROPOFF01', state: 'ready' } }).key, context.key);
});

test('storage voice includes explicit location names and refreshes keys when a collision appears', () => {
  const workspace = voiceWorkspace();
  const initial = warehouseVoiceContext(workspace);
  assert.equal(initial.allowLocationNames, true);
  assert.equal(initial.requireFullCode, false);
  assert.equal(initial.selected.size, 0);
  assert.notEqual(warehouseVoiceContext({ ...workspace, parcels: [...workspace.parcels, { number: 'NEW01', carrier_status: 'accepted' }] }).key, initial.key);
  assert.notEqual(warehouseVoiceContext({ ...workspace, locations: [{ barcode: 'LOC:A', name: 'Pokladňa' }] }).key, initial.key);
});

test('visible suffix is only a finding aid, with active parcel collision detection', () => {
  const parcels = [{ number: 'A12', carrier_status: 'accepted' }, { number: 'B12', carrier_status: 'accepted' }, { number: 'C12', carrier_status: 'delivered' }];
  assert.deepEqual(suffixCollisions(parcels, 'A12').map(p => p.number), ['B12']);
});

test('receipt lost before server journal allows only an explicit retry with the identical operation ID', () => {
  const item = { trackNumber: '00012', state: 'uncertain', operationId: 'original-operation' };
  const retry = reconcileReceiptItem(item, { number: '00012', carrier_status: 'unconfirmed', pending_operation: null });
  assert.equal(retry.state, 'pending');
  assert.equal(retry.operationId, 'original-operation');
  assert.match(retry.message, /kontrolovaný pokus/);
  assert.equal(item.state, 'uncertain');
  assert.equal(reconcileReceiptItem(item, undefined), item);
});

test('server pending or unknown receipt remains blocked and a confirmed receipt completes locally', () => {
  const item = { trackNumber: '00012', state: 'uncertain', operationId: 'original-operation' };
  for (const outcome of ['pending', 'uncertain']) {
    const blocked = reconcileReceiptItem(item, { carrier_status: 'unconfirmed', pending_operation: { kind: 'accept', outcome } });
    assert.equal(blocked.state, 'uncertain');
    assert.equal(blocked.operationId, 'original-operation');
  }
  assert.equal(reconcileReceiptItem(item, { carrier_status: 'accept_uncertain', pending_operation: null }).state, 'uncertain');
  assert.equal(reconcileReceiptItem(item, { carrier_status: 'accepted', pending_operation: null }).state, 'accepted');
  const sending = { ...item, state: 'sending' };
  assert.equal(reconcileReceiptItem(sending, { carrier_status: 'unconfirmed', pending_operation: { kind: 'accept', outcome: 'pending' } }), sending);
});

test('lost pickup response uses confirmed server state and retrieves COD receipt independently', () => {
  const attempt = { parcel: { trackNumber: '00012', cod: 12.5 }, uncertain: true, verified: true, operationId: 'original-delivery' };
  const confirmed = reconcilePickupAttempt(attempt, { number: '00012', carrier_status: 'delivered', pending_operation: null });
  assert.equal(confirmed.kind, 'confirmed');
  assert.deepEqual(confirmed.result, { delivered: true, number: '00012', receipt_pending: true });
  assert.equal(reconcilePickupAttempt({ ...attempt, parcel: { trackNumber: '00012', cod: 0 } }, { number: '00012', carrier_status: 'delivered' }).result.receipt_pending, false);
});

test('pickup retry preserves original operation ID and stays blocked without authoritative safe state', () => {
  const attempt = { parcel: { trackNumber: '00012', cod: 0 }, uncertain: true, verified: true, operationId: 'original-delivery' };
  const retry = reconcilePickupAttempt(attempt, { number: '00012', carrier_status: 'accepted', pending_operation: null });
  assert.equal(retry.kind, 'retry');
  assert.equal(retry.attempt.operationId, 'original-delivery');
  assert.equal(retry.attempt.uncertain, false);
  assert.equal(retry.attempt.controlledRetry, true);
  assert.equal(attempt.uncertain, true);
  for (const local of [undefined, { carrier_status: 'accepted', pending_operation: { kind: 'deliver', outcome: 'pending' } }, { carrier_status: 'delivery_uncertain' }, { carrier_status: 'returned' }]) {
    assert.equal(reconcilePickupAttempt(attempt, local).kind, 'blocked');
  }
});

test('customer intake recovery trusts confirmed journal, not imported accepted/outgoing status', () => {
  const attempt = { number: 'DEMODROPOFF01', state: 'uncertain', operationId: 'original-dropoff' };
  const imported = { number: attempt.number, carrier_status: 'accepted', carrier_data: { handoverKind: 'outgoing' }, dropoff_confirmed: false };
  assert.equal(reconcileDropoffAttempt(attempt, imported).state, 'uncertain');
  assert.equal(reconcileDropoffAttempt({ ...attempt, state: 'ready' }, imported).state, 'blocked');
  const confirmed = reconcileDropoffAttempt(attempt, { ...imported, dropoff_confirmed: true });
  assert.equal(confirmed.state, 'accepted');
  assert.equal(confirmed.operationId, attempt.operationId);
  assert.equal(reconcileDropoffAttempt({ ...attempt, state: 'accepted' }, undefined).state, 'accepted');
  for (const local of [undefined, { carrier_status: 'unconfirmed', pending_operation: null }]) {
    const retry = reconcileDropoffAttempt(attempt, local);
    assert.equal(retry.state, 'ready');
    assert.equal(retry.controlledRetry, true);
    assert.equal(retry.operationId, attempt.operationId);
  }
  for (const local of [{ carrier_status: 'dropoff_uncertain' }, { carrier_status: 'accepted', pending_operation: { kind: 'return', operation_id: 'another-operation' } }, { carrier_status: 'returned' }]) {
    assert.equal(reconcileDropoffAttempt(attempt, local).state, 'uncertain');
  }
  const pending = reconcileDropoffAttempt({ number: attempt.number, state: 'ready' }, { carrier_status: 'unconfirmed', pending_operation: { kind: 'dropoff', operation_id: 'server-operation' } });
  assert.equal(pending.state, 'uncertain');
  assert.equal(pending.operationId, 'server-operation');
});
