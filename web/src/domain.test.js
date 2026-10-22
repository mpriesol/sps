import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeCode, classifyScan, remainingItems, coverCrop } from './domain.js';

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
  const selected = new Map([['a', { state: 'accepted' }], ['b', { state: 'failed' }], ['c', { state: 'pending' }]]);
  assert.deepEqual(remainingItems(selected).map(x => x.state), ['failed', 'pending']);
});

test('portrait camera fills a landscape preview by cropping top and bottom', () => {
  assert.deepEqual(coverCrop(720, 1280, 360, 240), { x: 0, y: 400, width: 720, height: 480 });
  assert.deepEqual(coverCrop(1280, 720, 300, 300), { x: 280, y: 0, width: 720, height: 720 });
  assert.deepEqual(coverCrop(720, 1280, 0, 0), { x: 0, y: 0, width: 720, height: 1280 });
});
