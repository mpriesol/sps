import test from 'node:test';
import assert from 'node:assert/strict';
import { chooseCourier, reconcilePickupAttempt, reconcileReceiptItem, routeWarehouseScan, restoreDraft, serializeDraft, suffixCollisions } from './warehouse-domain.js';

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
