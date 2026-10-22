import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spokenSuffix, matchSpokenSuffix, VoiceInput } from './voice.js';

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
