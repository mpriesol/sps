import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeCode, classifyScan, remainingItems, coverCrop, receiptComplete, receiptItems, receiptSelectionLocked, selectReceiptItems, clearReceiptSelection } from './domain.js';

test('preserves leading zeros and accepts a scanner newline', () => {
  assert.equal(normalizeCode('  001234567890\r\n'), '001234567890');
  assert.equal(normalizeCode('sk0abc'), 'SK0ABC');
});
test('matches the complete catalog, not only the visible 100 rows', () => {
  const all = new Map(Array.from({ length: 1500 }, (_, n) => [`TEST${n}`, { trackNumber: `TEST${n}` }]));
  assert.equal(classifyScan('test1499', all, new Map()).kind, 'match');
});
test('unknown labels and repeated camera frames do not add another parcel', () => {
  const all = new Map([['JJD00001', { trackNumber: 'JJD00001' }]]);
  assert.equal(classifyScan('123456', all, new Map()).kind, 'unknown');
  assert.equal(classifyScan('JJD00001', all, new Map([['JJD00001', {}]])).kind, 'duplicate');
});
test('retry includes failed and pending parcels, leaving confirmed ones complete', () => {
  const selected = new Map([['a', { state: 'accepted' }], ['b', { state: 'failed' }], ['c', { state: 'pending' }], ['d', { state: 'uncertain' }]]);
  assert.deepEqual(remainingItems(selected).map(x => x.state), ['failed', 'pending']);
});
test('removing the last pending parcel does not complete a batch with an unknown result', () => {
  const selected = new Map([['accepted', { state: 'accepted' }], ['unknown', { state: 'uncertain' }], ['pending', { state: 'pending' }]]);
  selected.delete('pending');
  assert.equal(remainingItems(selected).length, 0);
  assert.equal(receiptComplete(selected), false);
  selected.set('unknown', { state: 'accepted' });
  assert.equal(receiptComplete(selected), true);
  assert.equal(receiptComplete(new Map()), false);
});

test('portrait camera fills a landscape preview by cropping top and bottom', () => {
  assert.deepEqual(coverCrop(720, 1280, 360, 240), { x: 0, y: 400, width: 720, height: 480 });
  assert.deepEqual(coverCrop(1280, 720, 300, 300), { x: 280, y: 0, width: 720, height: 720 });
  assert.deepEqual(coverCrop(720, 1280, 0, 0), { x: 0, y: 0, width: 720, height: 1280 });
});

test('receipt grid combines the catalog and protected results, filtering only parcel codes', () => {
  const shipments = new Map([['00001', { trackNumber: '00001', senderName: 'Sender name' }], ['00002', { trackNumber: '00002' }]]);
  const selected = new Map([['00001', { trackNumber: '00001', state: 'pending' }], ['OLD003', { trackNumber: 'OLD003', state: 'accepted' }]]);
  assert.deepEqual(receiptItems(shipments, selected).map(item => [item.code, item.isSelected, item.state]), [
    ['00001', true, 'pending'], ['00002', false, undefined], ['OLD003', true, 'accepted'],
  ]);
  assert.equal(receiptItems(shipments, selected, 'sender').length, 0);
  assert.equal(receiptItems(shipments, selected, ' old ').length, 1);
  assert.equal(receiptItems(new Map(), selected, '003')[0].state, 'accepted');
});

test('selecting all is local pending selection with a limit across existing protected results', () => {
  const confirmed = { trackNumber: 'OLD001', state: 'accepted' };
  const before = new Map([['OLD001', confirmed]]);
  const items = Array.from({ length: 201 }, (_, n) => ({ code: `NEW${String(n).padStart(3, '0')}` }));
  const result = selectReceiptItems(before, items);
  assert.equal(result.selected.size, 200);
  assert.equal(result.added, 199);
  assert.equal(result.limited, true);
  assert.equal(before.size, 1, 'does not mutate the previous selection');
  assert.equal(result.selected.get('OLD001'), confirmed, 'retains confirmed state and object');
  assert.equal(result.selected.get('NEW000').state, 'pending');
  assert.equal(selectReceiptItems(result.selected, items.slice(0, 1)).added, 0, 'does not duplicate');
});

test('clear selection retains accepted, sending and uncertain operations without hiding uncertainty', () => {
  const selected = new Map(['pending', 'failed', 'accepted', 'sending', 'uncertain'].map(state => [state, { state }]));
  const cleared = clearReceiptSelection(selected);
  assert.deepEqual([...cleared.keys()], ['accepted', 'sending', 'uncertain']);
  assert.equal(receiptComplete(cleared), false);
  assert.equal(receiptSelectionLocked(selected.get('uncertain')), true);
  assert.equal(receiptSelectionLocked(selected.get('failed')), false);
  assert.equal(receiptSelectionLocked(undefined), false);
});
