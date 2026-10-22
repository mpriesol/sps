import { BrowserMultiFormatReader } from '@zxing/browser';
import { BarcodeFormat, DecodeHintType } from '@zxing/library';
import { classifyScan, normalizeCode, remainingItems, parcelCount } from './domain.js';
import './style.css';

const $ = id => document.getElementById(id);
const state = { csrf: '', demo: false, shipments: new Map(), selected: new Map(), loading: false, receiving: false, finished: false };
let scanner = null, cameraStarting = false, cameraGeneration = 0, torchOn = false, audio = null;
let lastSeen = { code: '', time: 0 };

function text(tag, value, className = '') {
  const node = document.createElement(tag);
  node.textContent = value;
  node.className = className;
  return node;
}

async function api(path, data) {
  const response = await fetch(`./api/${path}`, {
    method: data === undefined ? 'GET' : 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'sps-app', 'X-CSRF-Token': state.csrf },
    ...(data === undefined ? {} : { body: JSON.stringify(data) }),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== 'login') showLogin();
    throw new Error(typeof result.detail === 'string' ? result.detail : 'Požiadavka zlyhala. Skús to znova.');
  }
  return result;
}

function feedback(message, type = '') {
  $('scan-feedback').textContent = message;
  $('scan-feedback').className = `feedback ${type}`;
}

function initAudio() {
  try {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (Context) { audio ??= new Context(); audio.resume().catch(() => {}); }
  } catch { /* Sound is optional. */ }
}

function beep() {
  try {
    if (audio?.state === 'running') {
      const oscillator = audio.createOscillator(), gain = audio.createGain();
      oscillator.connect(gain); gain.connect(audio.destination);
      oscillator.frequency.value = 920; gain.gain.value = 0.09;
      oscillator.start(); oscillator.stop(audio.currentTime + 0.09);
    }
    navigator.vibrate?.(65);
  } catch { /* Camera and manual input continue without sound. */ }
}

function scanCode(value, fromCamera = false) {
  if (state.loading || state.receiving || state.finished) return;
  const code = normalizeCode(value);
  if (fromCamera && lastSeen.code === code && Date.now() - lastSeen.time < 2000) return;
  lastSeen = { code, time: Date.now() };
  const result = classifyScan(code, state.shipments, state.selected);
  if (result.kind === 'empty') return;
  if (result.kind === 'duplicate') return feedback(`${code} už je v tejto dávke.`, 'warning');
  if (result.kind === 'unknown') return feedback(`${code}: nie je v zozname SPS. Skús iný kód na štítku alebo obnov zoznam.`, 'error');
  if (result.kind === 'full') return feedback('V jednej dávke môže byť najviac 200 balíkov.', 'warning');
  state.selected.set(result.code, { ...result.shipment, state: 'pending', message: '' });
  feedback(`✓ ${result.shipment.trackNumber} · pridané do dávky`, 'success');
  beep(); render();
}

function render() {
  const n = state.selected.size, remaining = remainingItems(state.selected).length;
  const accepted = [...state.selected.values()].filter(x => x.state === 'accepted').length;
  $('selected-count').textContent = n;
  $('available-count').textContent = `${state.shipments.size} v zozname SPS`;
  $('catalog-count').textContent = state.shipments.size;
  $('bottom-count').textContent = state.receiving ? `${accepted} / ${n} prijatých` : parcelCount(n);
  $('bottom-help').textContent = state.receiving ? 'Odosielam do SPS…' : state.finished ? 'Príjem dokončený' : 'Naskenované, ešte nepotvrdené';
  $('prepare').textContent = accepted ? `Prijať zvyšných ${remaining}` : `Prijať ${n || ''} ${n === 1 ? 'balík' : n >= 2 && n <= 4 ? 'balíky' : 'balíkov'}`;
  $('prepare').disabled = !remaining || state.loading || state.receiving;
  $('prepare').hidden = state.finished;
  $('new-receipt').hidden = !state.finished;
  $('refresh').disabled = state.loading || state.receiving;
  $('scan').disabled = state.loading || state.receiving || state.finished || cameraStarting;
  $('manual-add').disabled = state.loading || state.receiving || state.finished;
  $('manual-code').disabled = state.receiving || state.finished;
  $('logout').disabled = state.receiving;
  $('demo-add').disabled = state.loading || state.receiving || state.finished;
  $('empty-selection').hidden = n > 0;
  const list = $('selected-list'); list.replaceChildren();
  for (const [code, item] of [...state.selected].reverse()) {
    const li = text('li', '', `parcel ${item.state}`);
    const badge = text('span', item.state === 'accepted' ? '✓' : item.state === 'sending' ? '…' : item.state === 'failed' ? '!' : '▣', 'parcel-icon');
    const info = text('div', '', 'parcel-info');
    info.append(text('strong', item.trackNumber), text('span', item.senderName || 'Zásielka SPS', 'muted'));
    if (item.message) info.append(text('small', item.message, item.state === 'failed' ? 'error-text' : 'muted'));
    li.append(badge, info);
    if (item.state !== 'accepted' && !state.receiving) {
      const remove = text('button', '×', 'remove');
      remove.type = 'button'; remove.setAttribute('aria-label', `Odstrániť ${item.trackNumber}`);
      remove.addEventListener('click', () => {
        state.selected.delete(code);
        state.finished = state.selected.size > 0 && remainingItems(state.selected).length === 0;
        render();
      });
      li.append(remove);
    }
    list.append(li);
  }
  renderCatalog();
  $('step-scan').className = !state.receiving && !state.finished ? 'active' : '';
  $('step-confirm').className = state.receiving ? 'active' : '';
  $('step-done').className = state.finished ? 'active' : '';
}

function renderCatalog() {
  const query = normalizeCode($('search').value), list = $('catalog-list');
  list.replaceChildren();
  const matches = [...state.shipments.values()].filter(s => normalizeCode(`${s.trackNumber} ${s.senderName}`).includes(query));
  for (const shipment of matches.slice(0, 100)) {
    const li = text('li', '', 'parcel'), info = text('div', '', 'parcel-info');
    info.append(text('strong', shipment.trackNumber), text('span', shipment.senderName || 'Zásielka SPS', 'muted'));
    const selected = state.selected.has(normalizeCode(shipment.trackNumber));
    const add = text('button', selected ? '✓' : '+', 'catalog-add');
    add.type = 'button'; add.disabled = selected || state.receiving || state.finished || state.loading;
    add.setAttribute('aria-label', `Pridať ${shipment.trackNumber}`);
    add.addEventListener('click', () => scanCode(shipment.trackNumber));
    li.append(info, add); list.append(li);
  }
  if (matches.length > 100) list.append(text('li', 'Zobrazených prvých 100. Použi vyhľadávanie.', 'footnote'));
  $('catalog-empty').hidden = matches.length !== 0;
}

async function loadShipments() {
  state.loading = true; $('load-error').hidden = true; render();
  try {
    const result = await api('shipments');
    state.shipments = new Map(result.shipments.map(s => [normalizeCode(s.trackNumber), s]));
    feedback(state.shipments.size ? `Pripravené. V SPS čaká ${parcelCount(state.shipments.size)}.` : 'V SPS momentálne nie sú zásielky na príjem.');
    // Preserve the user's selected list; stale entries can be removed explicitly.
  } catch (error) {
    $('load-error').textContent = error.message; $('load-error').hidden = false;
    feedback('Zoznam sa nepodarilo načítať. Stlač Obnoviť.', 'error');
  } finally { state.loading = false; render(); }
}

function stopCamera() {
  cameraGeneration++;
  scanner?.stop(); scanner = null;
  const video = $('video');
  video.srcObject?.getTracks().forEach(track => track.stop()); video.srcObject = null;
  cameraStarting = false; torchOn = false;
  $('camera-placeholder').hidden = false; $('scan-frame').hidden = true; $('live-badge').hidden = true;
  $('torch').hidden = true; $('torch').textContent = 'Prisvietiť'; $('scan').textContent = 'Zapnúť skenovanie';
  render();
}

async function startCamera() {
  if (scanner) { stopCamera(); return; }
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    feedback('Fotoaparát potrebuje HTTPS. Otvor aplikáciu cez jej zabezpečenú adresu.', 'error'); return;
  }
  initAudio(); cameraStarting = true; render();
  const generation = ++cameraGeneration;
  const hints = new Map([[DecodeHintType.POSSIBLE_FORMATS, [BarcodeFormat.CODE_128, BarcodeFormat.CODE_39, BarcodeFormat.CODE_93, BarcodeFormat.ITF, BarcodeFormat.DATA_MATRIX, BarcodeFormat.QR_CODE, BarcodeFormat.PDF_417, BarcodeFormat.EAN_13]]]);
  const reader = new BrowserMultiFormatReader(hints, { delayBetweenScanAttempts: 100, delayBetweenScanSuccess: 350 });
  try {
    const controls = await reader.decodeFromConstraints({ audio: false, video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } } }, $('video'), result => {
      if (result && generation === cameraGeneration) scanCode(result.getText(), true);
    });
    if (generation !== cameraGeneration) { controls.stop(); return; }
    scanner = controls;
    $('camera-placeholder').hidden = true; $('scan-frame').hidden = false; $('live-badge').hidden = false;
    $('scan').textContent = 'Pozastaviť kameru'; $('torch').hidden = !controls.switchTorch;
    feedback('Kamera je zapnutá. Namier čiarový kód do rámčeka.');
  } catch (error) {
    stopCamera();
    feedback(error.name === 'NotAllowedError' ? 'Povoľ prístup k fotoaparátu v nastavení prehliadača.' : 'Fotoaparát sa nepodarilo spustiť. Skús ho zapnúť znova alebo zadaj kód ručne.', 'error');
  } finally { cameraStarting = false; render(); }
}

async function prepareReceipt() {
  stopCamera(); $('confirm-error').textContent = ''; $('courier-password').value = '';
  $('courier').replaceChildren(new Option('Načítavam kuriérov…', ''));
  $('confirm-submit').disabled = true;
  const remaining = remainingItems(state.selected);
  $('confirm-title').textContent = `Prijať ${parcelCount(remaining.length)}`;
  $('confirm-dialog').showModal();
  try {
    const result = await api('couriers', { numbers: remaining.map(x => x.trackNumber) });
    $('courier').replaceChildren(new Option('Vyber kuriéra', ''), ...result.couriers.map(c => new Option(`${c.code} · ${c.name}`, String(c.id))));
    if (result.suggested !== null) $('courier').value = String(result.suggested);
    $('confirm-submit').disabled = !result.couriers.length;
    if (result.suggested !== null) $('courier-password').focus();
  } catch (error) { $('confirm-error').textContent = error.message; }
}

async function receive(event) {
  event.preventDefault();
  if (state.receiving) return;
  let password = $('courier-password').value;
  const courierId = $('courier').value;
  if (!password || !courierId) return;
  $('courier-password').value = ''; $('confirm-dialog').close();
  state.receiving = true; $('result').hidden = true; render();
  let errorMessage = '';
  try {
    for (const item of remainingItems(state.selected)) {
      item.state = 'sending'; item.message = ''; render();
      try {
        await api('accept', { number: item.trackNumber, courier_id: courierId, password });
        item.state = 'accepted'; item.message = state.demo ? 'Prijaté v demo režime' : 'Prijaté do SPS';
      } catch (error) {
        item.state = 'failed'; item.message = error.message; errorMessage = error.message; break;
      }
      render();
    }
  } finally {
    password = null; state.receiving = false;
    const accepted = [...state.selected.values()].filter(x => x.state === 'accepted').length;
    state.finished = accepted === state.selected.size;
    $('result').hidden = false;
    $('result').classList.toggle('result-error', !state.finished);
    $('result-title').textContent = `${accepted} z ${state.selected.size} balíkov prijatých${state.demo ? ' v deme' : ' do SPS'}`;
    $('result-text').textContent = state.finished ? 'Hotovo. Môžeš začať nový príjem.' : `${errorMessage} Po oprave môžeš potvrdiť zostávajúce balíky znova.`;
    feedback(state.finished ? '✓ Príjem bol dokončený.' : 'Príjem sa zastavil. Pozri výsledok nižšie.', state.finished ? 'success' : 'error');
    render(); $('result').scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

function showLogin() {
  stopCamera(); $('confirm-dialog').close(); $('courier-password').value = '';
  state.csrf = ''; $('login-view').hidden = false; $('work-view').hidden = true; $('logout').hidden = true;
}

async function showWork(session) {
  state.csrf = session.csrf; state.demo = session.demo;
  $('login-view').hidden = true; $('work-view').hidden = false; $('logout').hidden = false;
  $('demo-banner').hidden = !session.demo; $('demo-add').hidden = !session.demo; $('demo-password').hidden = !session.demo;
  await loadShipments();
}

$('login-form').addEventListener('submit', async event => {
  event.preventDefault(); $('login-button').disabled = true; $('login-error').textContent = '';
  try { const session = await api('login', { password: $('app-password').value }); $('app-password').value = ''; await showWork(session); }
  catch (error) { $('login-error').textContent = error.message; }
  finally { $('login-button').disabled = false; }
});
$('logout').addEventListener('click', async () => {
  try { await api('logout', {}); state.selected.clear(); state.shipments.clear(); state.finished = false; showLogin(); }
  catch (error) { feedback(error.message, 'error'); }
});
$('manual-form').addEventListener('submit', event => { event.preventDefault(); initAudio(); scanCode($('manual-code').value); $('manual-code').value = ''; });
$('scan').addEventListener('click', startCamera);
$('torch').addEventListener('click', async () => { try { torchOn = !torchOn; await scanner?.switchTorch?.(torchOn); $('torch').textContent = torchOn ? 'Vypnúť svetlo' : 'Prisvietiť'; } catch { feedback('Tento fotoaparát nepodporuje prisvietenie.', 'warning'); } });
$('refresh').addEventListener('click', loadShipments);
$('search').addEventListener('input', renderCatalog);
$('prepare').addEventListener('click', prepareReceipt);
$('cancel-confirm').addEventListener('click', () => { $('courier-password').value = ''; $('confirm-dialog').close(); });
$('confirm-dialog').addEventListener('cancel', () => { $('courier-password').value = ''; });
$('confirm-form').addEventListener('submit', receive);
$('new-receipt').addEventListener('click', async () => { state.selected.clear(); state.finished = false; $('result').hidden = true; await loadShipments(); window.scrollTo({ top: 0, behavior: 'smooth' }); });
$('demo-add').addEventListener('click', () => { const next = [...state.shipments.values()].find(s => !state.selected.has(normalizeCode(s.trackNumber))); if (next) scanCode(next.trackNumber); else feedback('Všetky skúšobné balíky sú už pridané.'); });
document.addEventListener('visibilitychange', () => { if (document.hidden) stopCamera(); });

api('session').then(showWork).catch(() => showLogin());
