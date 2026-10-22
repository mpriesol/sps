import { normalizeCode } from './domain.js';

const words = new Map(Object.entries({
  nula: '0', nulka: '0', jeden: '1', jedna: '1', jedno: '1', jednotka: '1', jednotku: '1',
  dva: '2', dve: '2', dvojka: '2', tri: '3', trojka: '3', styri: '4', stvorka: '4',
  pat: '5', patka: '5', sest: '6', sestka: '6', sedem: '7', sedmicka: '7',
  osem: '8', osmicka: '8', devat: '9', deviatka: '9',
  be: 'B', ce: 'C', de: 'D', ef: 'F', ge: 'G', ha: 'H', jot: 'J', je: 'J',
  ka: 'K', el: 'L', em: 'M', en: 'N', pe: 'P', ku: 'Q', kve: 'Q', er: 'R',
  es: 'S', te: 'T', ve: 'V', dvojve: 'W', dvojiteve: 'W', iks: 'X',
  ypsilon: 'Y', ipsilon: 'Y', ygrek: 'Y', zet: 'Z', ze: 'Z',
}));

export function spokenSuffix(transcript) {
  if (typeof transcript !== 'string' || transcript.length > 500) return null;
  const normalized = transcript.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/\bdvojite\s+(?:ve|v)\b/g, 'w')
    .replace(/\btvrde\s+i\b/g, 'y').replace(/\bmakke\s+i\b/g, 'i')
    .replace(/[.,!?;:„“"'’()\-]/g, ' ').trim();
  if (!normalized || /[^a-z0-9\s]/.test(normalized)) return null;
  const parts = normalized.split(/\s+/).map(token => {
    if (words.has(token)) return words.get(token);
    // Speech engines may already return digits or an acronym such as MP3 / MP.
    if (/^[a-z]{1,3}$/.test(token) || (/^[a-z0-9]+$/.test(token) && /\d/.test(token))) return token.toUpperCase();
    return null;
  });
  if (parts.includes(null)) return null;
  const suffix = parts.join('');
  return suffix.length <= 100 ? suffix : null;
}

export function matchSpokenSuffix(transcript, shipments, selected) {
  const suffix = spokenSuffix(transcript);
  if (!suffix) return { kind: 'invalid', suffix: null };
  if (suffix.length < 2) return { kind: 'short', suffix };
  // Include scanned parcels: never resolve an ambiguous suffix by silently
  // skipping a duplicate, even after the SPS list was refreshed.
  const codes = new Set([...shipments.keys(), ...selected.keys()].map(normalizeCode));
  const matches = [...codes].filter(code => code.endsWith(suffix));
  if (!matches.length) return { kind: 'unknown', suffix };
  if (matches.length > 1) return { kind: 'ambiguous', suffix, count: matches.length };
  return { kind: 'match', suffix, code: matches[0] };
}

const errors = {
  'not-allowed': 'Povoľ mikrofón a hlasové rozpoznávanie v nastavení prehliadača.',
  'service-not-allowed': 'Prehliadač nepovolil hlasové rozpoznávanie. Skontroluj jeho nastavenia alebo použi kameru.',
  'audio-capture': 'Mikrofón nie je dostupný. Skontroluj povolenie alebo ho uvoľni v inej aplikácii.',
  network: 'Hlasové rozpoznávanie sa nepripojilo. Skús tlačidlo Hlasom znova.',
  'language-not-supported': 'Rozpoznávanie slovenčiny v tomto prehliadači nie je dostupné. Použi kameru alebo ručné zadanie.',
};

export class VoiceInput {
  constructor({ Recognition = globalThis.SpeechRecognition || globalThis.webkitSpeechRecognition,
    onTranscript, onState, onError }) {
    this.Recognition = Recognition;
    this.onTranscript = onTranscript;
    this.onState = onState;
    this.onError = onError;
    this.active = false;
    this.recognition = null;
    this.timer = null;
  }

  start() {
    if (this.active) return;
    if (!this.Recognition) {
      this.onError('Tento prehliadač nepodporuje hlasové zadávanie. Skús iný mobilný prehliadač alebo použi kameru.');
      return;
    }
    this.active = true;
    this.onState(true);
    this.listen();
  }

  listen() {
    if (!this.active || this.recognition) return;
    let recognition;
    try {
      recognition = new this.Recognition();
      this.recognition = recognition;
      recognition.lang = 'sk-SK';
      recognition.continuous = true;
      recognition.interimResults = false;
      recognition.maxAlternatives = 1;
      let consumed = 0;
      recognition.onresult = event => {
        if (!this.active || this.recognition !== recognition) return;
        for (let i = Math.max(event.resultIndex, consumed); i < event.results.length; i++) {
          if (!this.active || this.recognition !== recognition) break;
          if (!event.results[i].isFinal) continue;
          consumed = i + 1;
          this.onTranscript(event.results[i][0].transcript);
        }
      };
      recognition.onnomatch = () => {
        if (this.active && this.recognition === recognition) this.onTranscript('');
      };
      recognition.onerror = event => {
        if (!this.active || this.recognition !== recognition || event.error === 'no-speech') return;
        this.stop();
        this.onError(errors[event.error] || 'Hlasové zadávanie sa zastavilo. Skús ho zapnúť znova.');
      };
      recognition.onend = () => {
        if (this.recognition !== recognition) return;
        this.recognition = null;
        // Mobile browsers can end recognition after a pause; keep the mode on.
        if (this.active) this.timer = setTimeout(() => this.listen(), 350);
      };
      recognition.start();
    } catch {
      this.stop();
      this.onError('Hlasové zadávanie sa nepodarilo zapnúť. Skús tlačidlo Hlasom znova.');
    }
  }

  pause() {
    clearTimeout(this.timer);
    this.timer = null;
    const recognition = this.recognition;
    this.recognition = null;
    try { recognition?.abort(); } catch { /* Already stopped by the browser. */ }
  }

  resume() {
    clearTimeout(this.timer);
    if (this.active && !this.recognition) this.timer = setTimeout(() => this.listen(), 350);
  }

  stop() {
    this.active = false;
    this.pause();
    this.onState(false);
  }
}
