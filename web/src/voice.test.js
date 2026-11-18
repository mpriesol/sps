import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spokenSuffix, matchSpokenSuffix, matchSpokenFullCode, matchSpokenLocation, VoiceInput } from './voice.js';

test('Slovak letter names and recognizer-formatted acronyms resolve to MP3', () => {
  for (const phrase of ['em pé tri', 'em pe tri', 'ém, pé, 3.', 'M P 3', 'MP3', 'mp tri', 'em P3']) {
    assert.equal(spokenSuffix(phrase), 'MP3', phrase);
  }
});

test('digits preserve leading zeros and distinguish the letter O from zero', () => {
  assert.equal(spokenSuffix('nula nula sedem'), '007');
  assert.equal(spokenSuffix('0 0 7'), '007');
  assert.equal(spokenSuffix('007'), '007');
  assert.equal(spokenSuffix('ó nula sedem'), 'O07');
  assert.equal(spokenSuffix('tri osem sedem'), '387');
  assert.equal(spokenSuffix('štyri päť šesť deväť'), '4569');
});

test('Slovak spelling names cover the Latin barcode alphabet', () => {
  assert.equal(spokenSuffix('á bé cé dé é ef gé há í jot ká el em en ó pé kú er es té ú vé dvojité vé iks ypsilon zet'), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ');
  assert.equal(spokenSuffix('mäkké i tvrdé i dvojité v'), 'IYW');
});

test('unrecognized speech is not reduced to a plausible code by dropping words', () => {
  for (const phrase of ['', 'ahoj em pé tri', 'asi balík 387', 'em @ pé tri', 'tridsať sedem']) {
    assert.equal(spokenSuffix(phrase), null, phrase);
  }
});

test('unique suffix matches the full catalog and supports duplicate feedback', () => {
  const shipments = new Map(Array.from({ length: 1000 }, (_, n) => [`ABC${String(n).padStart(4, '0')}`, {}]));
  shipments.set('JJD001MP3', {});
  assert.deepEqual(matchSpokenSuffix('em pé tri', shipments, new Map()), { kind: 'match', suffix: 'MP3', code: 'JJD001MP3' });
  const selected = new Map([['JJD001MP3', {}]]);
  assert.equal(matchSpokenSuffix('em pé tri', shipments, selected).code, 'JJD001MP3');
  assert.equal(matchSpokenSuffix('em pé tri', new Map(), selected).code, 'JJD001MP3');
});

test('ambiguous suffixes never choose a pending parcel over an already scanned one', () => {
  const shipments = new Map([['001MP3', {}], ['002MP3', {}]]);
  assert.deepEqual(matchSpokenSuffix('em pé tri', shipments, new Map([['001MP3', {}]])), { kind: 'ambiguous', suffix: 'MP3', count: 2 });
  assert.equal(matchSpokenSuffix('dva em pé tri', shipments, new Map()).code, '002MP3');
  assert.equal(matchSpokenSuffix('deväť deväť', shipments, new Map()).kind, 'unknown');
  assert.equal(matchSpokenSuffix('tri', shipments, new Map()).kind, 'short');
});

test('two characters are sufficient; a third disambiguates without truncation', () => {
  const shipments = new Map([['SPS2MP3', {}], ['SPS7AP3', {}], ['SPS00387', {}]]);
  assert.equal(matchSpokenSuffix('osem sedem', shipments, new Map()).code, 'SPS00387');
  assert.deepEqual(matchSpokenSuffix('pé tri', shipments, new Map()), { kind: 'ambiguous', suffix: 'P3', count: 2 });
  assert.equal(matchSpokenSuffix('em pé tri', shipments, new Map()).code, 'SPS2MP3');
});

test('pickup requires every character even if the expected parcel has a unique suffix', () => {
  const candidates = new Map([['00MP3', {}], ['OTHER09', {}]]);
  assert.equal(matchSpokenFullCode('em pé tri', candidates).kind, 'incomplete');
  assert.equal(matchSpokenFullCode('nula em pé tri', candidates).kind, 'incomplete');
  assert.equal(matchSpokenFullCode('nula nula em pé tri', candidates).code, '00MP3');
  assert.equal(matchSpokenFullCode('00mp3', candidates).code, '00MP3');
  assert.equal(matchSpokenFullCode('other09', candidates).code, 'OTHER09', 'wrong physical parcel remains its own full ID for verification');
  assert.equal(matchSpokenFullCode('deväť deväť', candidates).kind, 'unknown');
});

test('new customer dropoff accepts a complete unknown tracking code without inventing missing characters', () => {
  const candidates = new Map([['DEMO123456', {}]]), options = { allowUnknown: true };
  assert.deepEqual(matchSpokenFullCode('nula nula jedna dva tri štyri', candidates, new Map(), options), { kind: 'match', code: '001234', suffix: '001234' });
  assert.equal(matchSpokenFullCode('NEW-001234', candidates, new Map(), options).code, 'NEW-001234');
  assert.equal(matchSpokenFullCode('nula jedna', candidates, new Map(), options).kind, 'incomplete');
  assert.equal(matchSpokenFullCode('123456', candidates, new Map(), options).kind, 'incomplete', 'a known suffix is never expanded or treated as its own new parcel');
  assert.equal(matchSpokenFullCode('DEMO123456', candidates, new Map(), options).code, 'DEMO123456');
  assert.equal(matchSpokenFullCode('001234', candidates).kind, 'unknown', 'unknown IDs remain forbidden for pickup');
});

test('dropoff full-code intent rejects locations, unsafe input and ordinary speech', () => {
  const match = transcript => matchSpokenFullCode(transcript, new Map(), new Map(), { allowUnknown: true });
  for (const phrase of ['LOC-ABC123', 'ahojte', 'asi balík 123456', 'ABC/123456', 'A'.repeat(101) + '1']) assert.equal(match(phrase).kind, 'invalid', phrase);
  assert.equal(match('nula nula nula nula nula').kind, 'incomplete');
  assert.equal(match('á bé cé dé é ef').code, 'ABCDEF', 'explicit spelling may contain letters only');
});

test('location voice uses an explicit prefix and a unique complete name', () => {
  const locations = [{ name: 'A1', barcode: 'LOC-ONE' }, { name: 'Horná polica', barcode: 'LOC-TWO' }];
  assert.equal(matchSpokenLocation('á jedna', locations), null, 'unprefixed speech remains a parcel suffix');
  assert.equal(matchSpokenLocation('miesto á jedna', locations).code, 'LOC-ONE');
  assert.equal(matchSpokenLocation('polica Horná polica', locations).code, 'LOC-TWO');
  assert.equal(matchSpokenLocation('miesto Horná', locations).kind, 'unknown', 'partial name is insufficient');
  assert.equal(matchSpokenLocation('miesto A1', [...locations, { name: 'A1', barcode: 'LOC-THREE' }]).kind, 'ambiguous');
});

class Recognition {
  static instances = [];
  constructor() { Recognition.instances.push(this); }
  start() { this.started = true; }
  abort() { this.aborted = true; }
  result(transcript, isFinal = true) {
    const result = Object.assign([{ transcript }], { isFinal });
    this.onresult({ resultIndex: 0, results: [result] });
  }
}

function voiceSetup(RecognitionClass = Recognition) {
  const heard = [], states = [], errors = [];
  const voice = new VoiceInput({ Recognition: RecognitionClass,
    onTranscript: t => heard.push(t), onState: s => states.push(s), onError: e => errors.push(e) });
  return { voice, heard, states, errors };
}

test('recognition is Slovak; only final results are processed, once per result', () => {
  const { voice, heard } = voiceSetup();
  voice.start();
  const recognition = voice.recognition;
  assert.equal(recognition.lang, 'sk-SK');
  assert.equal(recognition.continuous, true);
  recognition.result('em', false);
  recognition.result('em pé tri');
  recognition.result('em pé tri');
  assert.deepEqual(heard, ['em pé tri']);
  voice.stop();
  recognition.result('dva em pé tri');
  assert.equal(recognition.aborted, true);
  assert.equal(heard.length, 1);
});

test('recognition resumes after a mobile pause, but stop cancels the restart', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { voice } = voiceSetup();
  voice.start();
  const first = voice.recognition;
  first.onerror({ error: 'no-speech' });
  first.onend();
  t.mock.timers.tick(350);
  assert.notEqual(voice.recognition, first);
  assert.equal(voice.active, true);
  voice.recognition.onend();
  voice.stop();
  t.mock.timers.tick(1000);
  assert.equal(voice.recognition, null);
  assert.equal(voice.active, false);
});

test('permission denial and unsupported browsers stop cleanly', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { voice, errors } = voiceSetup();
  voice.start();
  const first = voice.recognition;
  first.onerror({ error: 'not-allowed' });
  first.onend();
  t.mock.timers.tick(1000);
  assert.equal(voice.active, false);
  assert.equal(voice.recognition, null);
  assert.match(errors[0], /mikrofón/);
  const unsupported = voiceSetup(null);
  unsupported.voice.start();
  assert.equal(unsupported.voice.active, false);
  assert.match(unsupported.errors[0], /nepodporuje/);
});

test('spoken prompts suspend recognition without leaving hands-free mode', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { voice, heard, states } = voiceSetup();
  voice.start();
  const beforePrompt = voice.recognition;
  voice.pause();
  beforePrompt.result('Povedz posledné tri znaky');
  beforePrompt.onend();
  t.mock.timers.tick(1000);
  assert.equal(voice.active, true);
  assert.equal(voice.recognition, null);
  assert.deepEqual(states, [true]);
  assert.deepEqual(heard, []);
  voice.resume();
  t.mock.timers.tick(350);
  assert.notEqual(voice.recognition, beforePrompt);
  voice.recognition.result('em pé tri');
  assert.deepEqual(heard, ['em pé tri']);
  voice.pause();
  voice.stop();
  voice.resume();
  t.mock.timers.tick(1000);
  assert.equal(voice.recognition, null);
});

test('a late result from another view is discarded and recognition restarts in the current context', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let context = 'receipt:parcel-A'; const heard = [];
  const voice = new VoiceInput({ Recognition, getContextKey: () => context,
    onTranscript: (transcript, key) => heard.push([transcript, key]), onState() {}, onError() {} });
  voice.start(); const receiptRecognition = voice.recognition;
  context = 'store:parcel-B';
  receiptRecognition.result('em pé tri');
  assert.deepEqual(heard, []);
  assert.equal(receiptRecognition.aborted, true);
  assert.equal(voice.active, true);
  t.mock.timers.tick(350);
  voice.recognition.result('nula sedem');
  assert.deepEqual(heard, [['nula sedem', 'store:parcel-B']]);
  receiptRecognition.result('deväť deväť');
  assert.equal(heard.length, 1);
  voice.stop();
});

test('multiple final results cannot cross a context change triggered by the first result', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let context = 'store:before'; const heard = [];
  const voice = new VoiceInput({ Recognition, getContextKey: () => context,
    onTranscript: transcript => { heard.push(transcript); context = 'store:after'; }, onState() {}, onError() {} });
  voice.start();
  const results = ['nula jedna', 'miesto á jedna'].map(transcript => Object.assign([{ transcript }], { isFinal: true }));
  voice.recognition.onresult({ resultIndex: 0, results });
  assert.deepEqual(heard, ['nula jedna']);
  voice.stop();
});
