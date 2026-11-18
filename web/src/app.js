import { BarcodeFormat, DecodeHintType } from '@zxing/library';
import { PreviewBarcodeReader } from './camera.js';
import { playScanSound } from './scan-feedback.js';
import { matchSpokenSuffix, matchSpokenFullCode, matchSpokenLocation, VoiceInput } from './voice.js';
import { classifyScan, normalizeCode, remainingItems, parcelCount, receiptComplete, receiptItems, receiptSelectionLocked, selectReceiptItems, clearReceiptSelection } from './domain.js';
import { WarehouseWorkspace } from './warehouse.js';
import { chooseCourier, operationId, suffix } from './warehouse-domain.js';
import './style.css';

const $ = id => document.getElementById(id);
const state = { csrf: '', demo: false, shipments: new Map(), selected: new Map(), couriers: [], courierId: '', courierResponse: null, loading: false, preparing: false, receiving: false, finished: false };
let courierTimer = null, courierRequest = 0, courierFingerprint = '', scanQueue = Promise.resolve();
let scanner = null, cameraStarting = false, cameraGeneration = 0, torchOn = false, audio = null;
let managedCouriers = [], savedCouriers = [], managerLoad = 0, revealGeneration = 0, managerBusy = false;
let spokenPrompt = null, promptTimer = null, wakeLock = null, wakeGeneration = 0;
let currentVoiceKey = null, voiceSuspended = false;
const voice = new VoiceInput({ onTranscript: scanSpeech, onState: renderVoice,
  getContextKey: () => workspace?.voiceContext?.().key || workspace?.view || '',
  onError: message => feedback(message, 'error') });
let workspace = null;
workspace = new WarehouseWorkspace({ api, feedback, beep, state, stopInput: () => { voice.stop(); stopCamera(); }, renderReceipt: render, onVoiceContextChange: syncVoiceContext });

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
    const error = new Error(typeof result.detail === 'string' ? result.detail : 'Požiadavka zlyhala. Skús to znova.');
    error.status = response.status; error.outcome = result.outcome; error.needs_credentials = result.needs_credentials;
    throw error;
  }
  return result;
}

function feedback(message, type = '') {
  $('scan-feedback').textContent = message;
  $('scan-feedback').title = message;
  $('scan-feedback').className = `feedback ${type}`;
}

function initAudio() {
  try {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (Context) { audio ??= new Context(); audio.resume().catch(() => {}); }
  } catch { /* Sound is optional. */ }
}

function beep(kind) {
  void playScanSound(audio, kind);
  try {
    navigator.vibrate?.(kind === 'match' ? 80 : kind === 'duplicate' ? [40, 50, 40] : [150, 70, 150]);
  } catch { /* Camera and manual input continue without sound. */ }
}

function scanCode(value, expectedVoiceContext = null) {
  const view = workspace.view;
  scanQueue = scanQueue.then(() => {
    if (workspace.view === view && (expectedVoiceContext === null || currentVoiceContext().key === expectedVoiceContext)) return processScan(value);
  }).catch(error => feedback(error.message, 'error'));
  return scanQueue;
}

async function processScan(value, { scanned = true } = {}) {
  if (state.preparing || state.receiving || workspace.photoBusy || document.querySelector('dialog[open]')) return;
  const code = normalizeCode(value);
  if (await workspace.scan(code)) return;
  if (state.loading || state.finished) return;
  const result = classifyScan(code, state.shipments, state.selected);
  if (result.kind === 'empty') return;
  beep(result.kind);
  if (result.kind === 'duplicate') {
    feedback(`${code} už je vybraný.`, 'warning');
    if ((workspace.receiptPlacementEnabled?.() || workspace.settings.photos) && workspace.receiptNumber !== code && !receiptSelectionLocked(state.selected.get(code))) await workspace.onReceiptParcel(code, scanned);
    return;
  }
  if (result.kind === 'unknown') return feedback(`${code}: nie je v zozname SPS. Skús iný kód na štítku alebo obnov zoznam.`, 'error');
  if (result.kind === 'full') return feedback('V jednej dávke môže byť najviac 200 balíkov.', 'warning');
  state.selected.set(result.code, { ...result.shipment, state: 'pending', message: '' });
  feedback(`✓ ${suffix(code)} · ${result.shipment.trackNumber} · pridané do dávky`, 'success');
  render();
  try { await workspace.onReceiptParcel(code, scanned); }
  catch (error) { feedback(`Balík je v dávke, ale lokálny záznam sa neuložil: ${error.message}`, 'error'); }
}

function toggleReceipt(code) {
  scanQueue = scanQueue.then(async () => {
    if (state.loading || state.preparing || state.receiving || state.finished || workspace.view !== 'receipt') return;
    const item = state.selected.get(code);
    if (item?.state === 'uncertain') {
      await workspace.refresh();
      const parcel = workspace.parcel(code);
      if (parcel?.pending_operation || parcel?.carrier_status?.endsWith('_uncertain')) workspace.resolveOperation(parcel);
      else feedback('Stav je obnovený. Pred ďalším príjmom skontroluj výsledok balíka.', 'warning');
      return;
    }
    if (receiptSelectionLocked(item)) return;
    if (item) {
      state.selected.delete(code); state.finished = receiptComplete(state.selected);
      if (workspace.receiptNumber === code) { workspace.receiptNumber = null; workspace.renderReceiptActive(); }
      feedback(`${code} · výber zrušený.`); render();
    } else await processScan(code, { scanned: false });
  }).catch(error => feedback(error.message, 'error'));
}

function selectAllReceipt() {
  scanQueue = scanQueue.then(() => {
    if (state.loading || state.preparing || state.receiving || state.finished || workspace.view !== 'receipt') return;
    const rows = receiptItems(state.shipments, state.selected, $('search').value).filter(item => state.shipments.has(item.code));
    const result = selectReceiptItems(state.selected, rows);
    state.selected = result.selected;
    feedback(result.limited ? 'Vybraných najviac 200 balíkov. Zvyšné prijmi v ďalšej dávke.' : `Vybrané: ${parcelCount(state.selected.size)}. Príjem ešte nie je potvrdený.`, result.limited ? 'warning' : 'success');
    render();
  }).catch(error => feedback(error.message, 'error'));
}

function clearReceipt() {
  scanQueue = scanQueue.then(() => {
    if (state.loading || state.preparing || state.receiving || workspace.view !== 'receipt') return;
    state.selected = clearReceiptSelection(state.selected); state.finished = receiptComplete(state.selected);
    if (!state.selected.has(workspace.receiptNumber)) { workspace.receiptNumber = null; workspace.renderReceiptActive(); }
    render(); feedback('Výber nepotvrdených balíkov bol zrušený.');
  }).catch(error => feedback(error.message, 'error'));
}

function renderVoice(active) {
  $('voice').textContent = active ? 'Stop hlas' : 'Hlasom';
  $('voice').setAttribute('aria-pressed', String(active));
  $('voice-placeholder').hidden = !active;
  if (!active) { currentVoiceKey = null; voiceSuspended = false; cancelVoicePrompt(); }
  void keepScreenOn(active);
}

async function keepScreenOn(active) {
  const generation = ++wakeGeneration, previous = wakeLock;
  wakeLock = null;
  try {
    await previous?.release();
    if (!active || !navigator.wakeLock) return;
    const lock = await navigator.wakeLock.request('screen');
    if (generation === wakeGeneration && voice.active) wakeLock = lock;
    else await lock.release();
  } catch { /* Some devices decline wake locks, for example in power-saving mode. */ }
}

function cancelVoicePrompt() {
  clearTimeout(promptTimer);
  if (spokenPrompt) {
    spokenPrompt.onend = null; spokenPrompt.onerror = null;
    spokenPrompt = null; window.speechSynthesis?.cancel();
  }
}

function sayVoicePrompt(message) {
  if (!voice.active || !window.speechSynthesis || !window.SpeechSynthesisUtterance) return;
  cancelVoicePrompt();
  // Do not mistake the app's own spoken instructions for a parcel code.
  voice.pause();
  try {
    const prompt = new SpeechSynthesisUtterance(message);
    spokenPrompt = prompt;
    prompt.lang = 'sk-SK'; prompt.rate = 1; prompt.volume = 1;
    const slovak = speechSynthesis.getVoices().find(v => v.lang.toLowerCase().startsWith('sk'));
    if (slovak) prompt.voice = slovak;
    prompt.onend = prompt.onerror = () => {
      if (spokenPrompt !== prompt) return;
      spokenPrompt = null; resumeVoiceIfAllowed();
    };
    // Let the three error beeps finish before speaking.
    promptTimer = setTimeout(() => {
      if (voice.active && spokenPrompt === prompt) {
        try { speechSynthesis.speak(prompt); }
        catch { spokenPrompt = null; resumeVoiceIfAllowed(); }
      }
    }, 600);
  } catch { spokenPrompt = null; resumeVoiceIfAllowed(); }
}

function currentVoiceContext() {
  return workspace?.voiceContext?.() || { key: workspace?.view || '', candidates: state.shipments, selected: state.selected, requireFullCode: false, enabled: workspace?.view === 'receipt', prompt: 'Povedz posledné 2 znaky' };
}

function scannerVisible() {
  return !!workspace && !$('scanner-workspace').hidden && ['receipt', 'store', 'pickup', 'returns', 'dropoff'].includes(workspace.view)
    && (workspace.view !== 'pickup' || workspace.settings.pickup_scan_required === true);
}

function voiceAllowed() {
  if (!scannerVisible() || !state.csrf) return false;
  return !!currentVoiceContext().enabled && !state.loading && !state.preparing && !state.receiving && !workspace.viewLoading && !workspace.busy && !workspace.returnBusy && !workspace.photoBusy && !(workspace.view === 'receipt' && state.finished) && !document.querySelector('dialog[open]');
}

function resumeVoiceIfAllowed() {
  if (voiceAllowed()) voice.resume();
  else voiceSuspended = true;
}

function syncVoiceContext() {
  if (!workspace) return;
  if (!scannerVisible()) {
    if (voice.active) voice.stop();
    if (scanner || cameraStarting) stopCamera();
    return;
  }
  const context = currentVoiceContext();
  const changed = currentVoiceKey !== context.key;
  if (!voice.active || changed) {
    $('voice-prompt').textContent = context.prompt || (context.requireFullCode ? 'Povedz celé číslo balíka' : 'Povedz posledné 2 znaky');
    $('voice-help').textContent = context.allowUnknownFullCode ? 'Povedz celé číslo po znakoch. Nový príjem potvrdíš až po kontrole čísla.' : context.requireFullCode ? 'Pre overenie výdaja povedz každý znak celého čísla alebo naskenuj celý kód.' : context.allowLocationNames ? 'Koniec kódu po znakoch. Pre policu povedz napríklad „miesto á jedna“.' : 'Každý znak zvlášť, napríklad „pé tri“.';
    currentVoiceKey = context.key;
  }
  if (!voice.active) return;
  if (!state.csrf || (workspace.view === 'receipt' && state.finished) || (workspace.view === 'pickup' && !workspace.pickup)) { voice.stop(); return; }
  if (!voiceAllowed()) {
    cancelVoicePrompt(); voice.pause(); voiceSuspended = true;
    return;
  }
  if (changed || voiceSuspended) {
    cancelVoicePrompt(); voice.pause(); voice.resume(); voiceSuspended = false;
  }
}

function scanSpeech(transcript, contextKey) {
  if (!voice.active || !voiceAllowed()) return;
  const context = currentVoiceContext();
  if (contextKey !== undefined && contextKey !== null && contextKey !== context.key) { syncVoiceContext(); return; }
  const location = context.allowLocationNames ? matchSpokenLocation(transcript, workspace.locations) : null;
  const result = location || (context.requireFullCode ? matchSpokenFullCode(transcript, context.candidates, context.selected, { allowUnknown: context.allowUnknownFullCode === true }) : matchSpokenSuffix(transcript, context.candidates, context.selected));
  $('voice-transcript').textContent = `Počul som: „${transcript.slice(0, 100)}“${result.suffix ? ` → ${result.suffix}` : ''}`;
  if (result.kind === 'match') { $('voice-prompt').textContent = context.prompt; scanCode(result.code, context.key); return; }
  if (workspace.view === 'pickup' && context.requireFullCode && workspace.pickup) { workspace.pickup.verified = false; workspace.render(); }
  if (workspace.view === 'dropoff' && workspace.dropoff) { workspace.dropoff = null; workspace.render(); }
  beep('unknown');
  if (result.location) {
    feedback(result.kind === 'ambiguous' ? 'Tento názov má viac miest. Použi čiarový kód konkrétneho miesta.' : 'Miesto s týmto názvom sa nenašlo. Povedz celý názov alebo naskenuj jeho kód.', 'warning');
    sayVoicePrompt('Miesto sa nedá jednoznačne určiť. Použi jeho čiarový kód.');
  } else if (context.requireFullCode) {
    $('voice-prompt').textContent = 'Povedz celé číslo balíka';
    feedback(context.allowUnknownFullCode ? 'Na príjem od zákazníka povedz celé číslo balíka, aspoň 6 znakov. Samotná koncovka nestačí.' : 'Na overenie výdaja treba celé číslo balíka. Samotná koncovka nestačí.', 'warning');
    sayVoicePrompt('Povedz celé číslo balíka, každý znak zvlášť.');
  } else if (result.kind === 'ambiguous') {
    const length = result.suffix.length + 1;
    $('voice-prompt').textContent = `Povedz posledné ${length} ${length <= 4 ? 'znaky' : 'znakov'}`;
    feedback(`Koncovka ${result.suffix}: ${parcelCount(result.count)}. ${$('voice-prompt').textContent} znova.`, 'warning');
    sayVoicePrompt(`Viac zhôd. ${$('voice-prompt').textContent}.`);
  } else if (result.kind === 'unknown') {
    feedback(`${result.suffix}: v tomto prehľade sa taký balík nenašiel. Skús to znova.`, 'error');
    sayVoicePrompt('Balík sa nenašiel. Zopakuj koniec kódu.');
  } else {
    feedback('Povedz posledné dva znaky, každý zvlášť. Napríklad „pé tri“ alebo „nula sedem“.', 'warning');
    sayVoicePrompt('Nerozumiem. Povedz posledné dva znaky, každý zvlášť.');
  }
}

function toggleVoice() {
  if (voice.active) { voice.stop(); feedback('Hlasové zadávanie je vypnuté.'); return; }
  if (!voiceAllowed()) return;
  if (!window.isSecureContext) { feedback('Mikrofón potrebuje HTTPS.', 'error'); return; }
  initAudio(); stopCamera();
  $('voice-transcript').textContent = '';
  syncVoiceContext();
  feedback(currentVoiceContext().requireFullCode ? 'Povoľ mikrofón a povedz celé číslo balíka.' : 'Povoľ mikrofón a povedz posledné dva znaky kódu.');
  voice.start();
}

function render() {
  const busy = state.loading || state.preparing || state.receiving || workspace.viewLoading;
  const workflowBusy = busy || workspace.busy || workspace.returnBusy;
  $('work-view').setAttribute('aria-busy', String(workflowBusy));
  for (const node of $('workspace-nav').children) node.disabled = workflowBusy;
  for (const node of $('tools-menu').querySelectorAll('button')) node.disabled = workflowBusy;
  $('more-tools').disabled = workflowBusy;
  const receipt = workspace.view === 'receipt';
  const n = state.selected.size, remaining = remainingItems(state.selected).length;
  const accepted = [...state.selected.values()].filter(x => x.state === 'accepted').length;
  $('selected-count').textContent = n;
  $('available-count').textContent = `${state.shipments.size} v zozname SPS`;
  $('catalog-count').textContent = state.shipments.size;
  $('bottom-count').textContent = state.receiving ? `${accepted} / ${n} prijatých` : parcelCount(n);
  $('bottom-help').textContent = state.receiving ? 'Odosielam do SPS…' : state.finished ? 'Príjem dokončený' : 'Vybrané, ešte nepotvrdené';
  $('prepare').textContent = accepted ? `Prijať zvyšných ${remaining}` : `Prijať ${n || ''} ${n === 1 ? 'balík' : n >= 2 && n <= 4 ? 'balíky' : 'balíkov'}`;
  $('prepare').disabled = !remaining || busy || !state.courierId;
  $('prepare').hidden = state.finished;
  $('new-receipt').hidden = !state.finished;
  $('refresh').disabled = busy;
  $('manage-couriers').disabled = busy || workspace.busy || workspace.returnBusy;
  $('scan').disabled = !scannerVisible() || (receipt && (busy || state.finished)) || cameraStarting || workspace.busy || workspace.returnBusy || workspace.viewLoading;
  $('voice').disabled = (!voice.active && !voiceAllowed()) || cameraStarting;
  $('manual-add').disabled = (receipt && (busy || state.finished)) || workspace.busy || workspace.returnBusy || workspace.viewLoading;
  $('manual-code').disabled = (receipt && (busy || state.finished)) || workspace.busy || workspace.returnBusy || workspace.viewLoading;
  $('logout').disabled = workflowBusy;
  $('demo-add').disabled = busy || state.finished;
  renderCatalog();
  renderCourierSelection(); workspace.saveDraft(); scheduleCouriers(); syncVoiceContext();
}

function renderCourierSelection() {
  const select = $('receipt-courier'), previous = select.value;
  select.replaceChildren(new Option(state.selected.size ? 'Vyber kuriéra' : 'Najprv vyber balík', ''));
  for (const courier of state.couriers) select.append(new Option(`${courier.code} · ${courier.name}${courier.has_password ? ' · heslo uložené' : ''}`, String(courier.id)));
  select.value = state.courierId || ''; select.disabled = state.preparing || state.receiving || state.finished || !state.couriers.length;
  const first = state.couriers.find(c => String(c.id) === String(state.courierResponse?.first_id));
  const warning = $('courier-warning');
  const different = !!state.courierId && !!first && state.courierId !== String(first.id);
  warning.hidden = !(different && workspace.settings.courier_warning !== false);
  warning.textContent = different ? `⚠ Nie je prvý v zozname SPS. Prvý: ${first.code} · ${first.name}.` : '';
  if (!state.courierId && state.courierResponse?.requires_selection) { warning.hidden = false; warning.textContent = 'Posledný kuriér nie je v aktuálnom zozname. Vyber kuriéra ručne.'; }
}

function scheduleCouriers() {
  if (!state.csrf || state.loading || state.receiving || state.preparing) return;
  const numbers = remainingItems(state.selected).map(x => x.trackNumber), fingerprint = numbers.join('|');
  if (fingerprint === courierFingerprint) return;
  courierFingerprint = fingerprint; clearTimeout(courierTimer); const request = ++courierRequest;
  if (!numbers.length) return;
  courierTimer = setTimeout(async () => {
    try {
      const result = await api('couriers', { numbers }); if (request !== courierRequest || state.receiving) return;
      state.couriers = result.couriers; state.courierResponse = result; state.courierId = chooseCourier(result, state.courierId);
      render();
    } catch (error) { if (request === courierRequest) feedback(error.message, 'error'); }
  }, 200);
}

function setReceiptFilter(open) {
  const input = $('search'), toggle = $('toggle-filter');
  input.hidden = !open;
  toggle.setAttribute('aria-expanded', String(open));
  toggle.title = open ? 'Zavrieť filter' : 'Zobraziť filter';
  if (open) input.focus({ preventScroll: true });
  else {
    input.value = '';
    renderCatalog();
  }
}

function renderCatalog() {
  const list = $('receipt-list');
  const rows = receiptItems(state.shipments, state.selected, $('search').value);
  const busy = state.loading || state.receiving || state.preparing || workspace.viewLoading;
  const labels = { accepted: 'Príjem potvrdený', sending: 'Odosielam do SPS', uncertain: 'Nejasný výsledok — otvor overenie', failed: 'Príjem sa nepodaril' };
  // Keep focused tiles and the browser's scroll anchor in the document. Clearing
  // the grid and focusing a partly rebuilt list can clamp a scrolled viewport.
  const existing = new Map([...list.children].map(tile => [tile.dataset.code, tile]));
  let next = list.firstElementChild;
  for (const item of rows) {
    let tile = existing.get(item.code);
    if (!tile) {
      tile = text('button', item.trackNumber || item.code);
      tile.type = 'button'; tile.dataset.code = item.code;
      tile.addEventListener('click', () => { initAudio(); toggleReceipt(item.code); });
    }
    const label = item.trackNumber || item.code;
    if (tile.textContent !== label) tile.textContent = label;
    tile.className = `parcel-tile ${item.isSelected ? 'is-selected' : ''} ${item.state || ''}`;
    tile.setAttribute('aria-pressed', String(item.isSelected));
    const description = labels[item.state] || (item.isSelected ? 'Vybraný na príjem' : 'Vybrať na príjem');
    tile.title = `${description}${item.message ? ` · ${item.message}` : ''}`;
    tile.setAttribute('aria-label', `${item.trackNumber || item.code} · ${description}`);
    tile.disabled = busy || state.finished || ['accepted', 'sending'].includes(item.state);
    if (tile !== next) list.insertBefore(tile, next);
    next = tile.nextElementSibling;
  }
  while (next) { const removed = next; next = next.nextElementSibling; removed.remove(); }
  $('catalog-count').textContent = receiptItems(state.shipments, state.selected).length;
  $('catalog-empty').hidden = rows.length > 0;
  $('select-all').disabled = busy || state.finished || state.selected.size >= 200 || !rows.some(item => !item.isSelected && state.shipments.has(item.code));
  $('clear-selection').disabled = busy || ![...state.selected.values()].some(item => !receiptSelectionLocked(item));
  const uncertain = [...state.selected.values()].filter(item => item.state === 'uncertain').length;
  const failed = [...state.selected.values()].filter(item => item.state === 'failed').length;
  $('receipt-selection-note').textContent = uncertain ? `${uncertain} ${uncertain === 1 ? 'balík má' : 'balíky majú'} nejasný výsledok. Klikni na zvýraznené číslo a over stav.` : failed ? 'Príjem zvýraznených balíkov sa nepodaril. Skontroluj výsledok príjmu a potvrď zostávajúce balíky.' : '';
}

async function loadShipments() {
  voice.stop();
  clearTimeout(courierTimer); courierRequest++; courierFingerprint = '';
  state.loading = true; $('load-error').hidden = true; render();
  try {
    const result = await api('shipments');
    state.shipments = new Map(result.shipments.map(s => [normalizeCode(s.trackNumber), s]));
    feedback(state.shipments.size ? `Pripravené. V SPS čaká ${parcelCount(state.shipments.size)}.` : 'V SPS nie sú zásielky na príjem.');
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
  $('camera-preview').hidden = true;
  $('camera-placeholder').hidden = false; $('scan-frame').hidden = true; $('live-badge').hidden = true;
  $('torch').hidden = true; $('torch').textContent = 'Prisvietiť'; $('scan').textContent = 'Skenovať'; $('scan').setAttribute('aria-pressed', 'false');
  render();
}

async function startCamera() {
  voice.stop();
  if (scanner) { stopCamera(); return; }
  if (!scannerVisible() || state.preparing || state.receiving || workspace.busy || workspace.returnBusy || workspace.viewLoading || (workspace.view === 'receipt' && state.finished)) return;
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    feedback('Fotoaparát potrebuje HTTPS. Otvor aplikáciu cez jej zabezpečenú adresu.', 'error'); return;
  }
  initAudio(); cameraStarting = true; $('camera-preview').hidden = false; render();
  const generation = ++cameraGeneration;
  const hints = new Map([[DecodeHintType.POSSIBLE_FORMATS, [BarcodeFormat.CODE_128, BarcodeFormat.CODE_39, BarcodeFormat.CODE_93, BarcodeFormat.ITF, BarcodeFormat.DATA_MATRIX, BarcodeFormat.QR_CODE, BarcodeFormat.PDF_417, BarcodeFormat.EAN_13]]]);
  const reader = new PreviewBarcodeReader($('video'), hints, { delayBetweenScanAttempts: 100, delayBetweenScanSuccess: 900 });
  try {
    const controls = await reader.decodeFromConstraints({ audio: false, video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } } }, $('video'), result => {
      if (result && generation === cameraGeneration) scanCode(result.getText());
    });
    if (generation !== cameraGeneration) { controls.stop(); return; }
    scanner = controls;
    $('camera-placeholder').hidden = true; $('scan-frame').hidden = false; $('live-badge').hidden = false;
    $('scan').textContent = 'Zastaviť kameru'; $('scan').setAttribute('aria-pressed', 'true'); $('torch').hidden = !controls.switchTorch;
    feedback('Kamera je zapnutá. Namier čiarový kód do rámčeka.');
  } catch (error) {
    if (generation !== cameraGeneration) return;
    stopCamera();
    feedback(error.name === 'NotAllowedError' ? 'Povoľ prístup k fotoaparátu v nastavení prehliadača.' : 'Fotoaparát sa nepodarilo spustiť. Skús ho zapnúť znova alebo zadaj kód ručne.', 'error');
  } finally { if (generation === cameraGeneration) { cameraStarting = false; render(); } }
}

function courierChanged(forcePassword = false) {
  const courier = state.couriers.find(c => String(c.id) === $('courier').value);
  const saved = courier?.has_password && !forcePassword;
  $('courier-password').value = '';
  $('courier-password').required = !saved;
  $('courier-password').placeholder = saved ? 'Uložené heslo sa použije automaticky' : 'Zadaj heslo kuriéra';
  $('courier-hint').textContent = saved ? 'Môžeš použiť uložené heslo alebo zadať nové.' : 'Po úspešnom prijatí si heslo zapamätáme.';
}

function showCourierDialog(courierId, error = '', forcePassword = false) {
  $('confirm-title').textContent = `Prijať ${parcelCount(remainingItems(state.selected).length)}`;
  $('courier').replaceChildren(new Option('Vyber kuriéra', ''), ...state.couriers.map(c => new Option(`${c.code} · ${c.name}${c.has_password ? ' · uložené heslo' : ''}`, String(c.id))));
  if (courierId !== undefined) $('courier').value = String(courierId);
  courierChanged(forcePassword);
  $('confirm-error').textContent = error;
  $('confirm-submit').disabled = !state.couriers.length;
  if (!$('confirm-dialog').open) $('confirm-dialog').showModal();
  if ($('courier-password').required) $('courier-password').focus();
}

async function prepareReceipt() {
  if (state.preparing || state.receiving) return;
  const visibleCourier = state.courierId;
  clearTimeout(courierTimer); courierRequest++;
  voice.stop();
  state.preparing = true; stopCamera();
  feedback('Načítavam kuriérov zo SPS…');
  try {
    const result = await api('couriers', { numbers: remainingItems(state.selected).map(x => x.trackNumber) });
    state.couriers = result.couriers; state.courierResponse = result;
    state.courierId = chooseCourier(result, visibleCourier);
    if (!result.couriers.length) throw new Error('SPS nevrátilo žiadneho kuriéra. Obnov zoznam zásielok.');
    const selected = state.couriers.find(c => String(c.id) === state.courierId);
    if (selected?.has_password && visibleCourier === state.courierId) {
      feedback(`Príjem od kuriéra: ${selected.code} · ${selected.name}.`);
      await receiveBatch(state.courierId);
    } else showCourierDialog(state.courierId, !state.courierId ? 'Vyber kuriéra pre tento príjem.' : '');
  } catch (error) { feedback(error.message, 'error'); }
  finally { state.preparing = false; render(); }
}

async function receive(event) {
  event.preventDefault();
  if (state.receiving) return;
  const password = $('courier-password').value;
  if (!$('courier').value || (!password && $('courier-password').required)) return;
  state.courierId = $('courier').value;
  await receiveBatch($('courier').value, password || undefined);
}

async function receiveBatch(courierId, password) {
  $('courier-password').value = ''; $('confirm-dialog').close();
  state.receiving = true; $('result').hidden = true; render();
  let failed = null, saved = true;
  try {
    for (const item of remainingItems(state.selected)) {
      item.state = 'sending'; item.message = ''; render();
      try {
        const result = await api('accept', { number: item.trackNumber, courier_id: courierId, operation_id: item.operationId ||= operationId(), ...(password === undefined ? {} : { password }) });
        if (result.accepted !== true) { const error = new Error('Server nepotvrdil výsledok príjmu. Najprv over stav v SPS.'); error.outcome = 'uncertain'; throw error; }
        saved = saved && result.password_saved !== false;
        item.state = 'accepted'; item.message = result.local_saved === false ? 'SPS príjem potvrdilo. Lokálny záznam treba obnoviť.' : state.demo ? 'Prijaté v demo režime' : 'Prijaté do SPS';
        if (result.password_saved !== false) {
          const courier = state.couriers.find(c => String(c.id) === courierId);
          if (courier) courier.has_password = true;
        }
      } catch (error) {
        const uncertain = error.outcome === 'uncertain' || error.name === 'TypeError';
        item.state = uncertain ? 'uncertain' : 'failed'; item.message = error.message; failed = error;
        if (!uncertain) item.operationId = null;
        break;
      }
      render();
    }
  } finally {
    password = null; state.receiving = false;
    await workspace.refresh().catch(() => {});
    const accepted = [...state.selected.values()].filter(x => x.state === 'accepted').length;
    state.finished = accepted === state.selected.size;
    $('result').hidden = false;
    $('result').classList.toggle('result-error', !state.finished);
    $('result-title').textContent = `${accepted} z ${state.selected.size} balíkov prijatých${state.demo ? ' v deme' : ' do SPS'}`;
    const uncertain = [...state.selected.values()].some(item => item.state === 'uncertain');
    const controlledRetry = !uncertain && (failed?.outcome === 'uncertain' || failed?.name === 'TypeError');
    $('result-text').textContent = state.finished ? 'Hotovo. Môžeš začať nový príjem alebo uložiť balíky do políc.' : controlledRetry ? 'Požiadavka v evidencii servera nie je potvrdená; môžeš zopakovať kontrolovaný pokus.' : `${failed?.message || ''} ${uncertain ? 'Nejasný výsledok najprv over v SPS. Tento balík sa automaticky neodošle znova.' : 'Zostávajúce nepotvrdené balíky môžeš prijať znova.'}`;
    if (!saved) $('result-text').textContent += ' Heslo sa nepodarilo uložiť; pri ďalšom príjme ho bude treba zadať znova.';
    feedback(state.finished ? '✓ Príjem bol dokončený.' : 'Príjem sa zastavil. Skontroluj kuriéra a heslo.', state.finished ? 'success' : 'error');
    render();
    if (failed && !state.finished && state.csrf && failed.status !== 401 && failed.needs_credentials) showCourierDialog(courierId, failed.message, true);
    else if (state.csrf) $('result').scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

function clearRevealedPassword() {
  revealGeneration++;
  $('revealed-password').value = ''; $('revealed-password').hidden = true;
  $('reveal-courier').textContent = 'Zobraziť uložené heslo';
  $('reveal-courier').setAttribute('aria-expanded', 'false');
}

function renderSavedCouriers() {
  const list = $('saved-couriers-list'); list.replaceChildren();
  for (const courier of savedCouriers) {
    const selected = String(courier.id) === $('managed-courier').value;
    const button = text('button', `${courier.code} · ${courier.name}`, `saved-courier-button ${selected ? 'is-selected' : ''}`);
    button.type = 'button'; button.dataset.courierId = String(courier.id);
    button.setAttribute('aria-pressed', String(selected)); button.disabled = managerBusy;
    button.addEventListener('click', () => { $('managed-courier').value = String(courier.id); managerSelection(); });
    list.append(button);
  }
  if (!savedCouriers.length) list.append(text('p', 'Zatiaľ nie je uložené žiadne heslo kuriéra.', 'muted'));
}

function setManagerBusy(busy) {
  managerBusy = busy; $('passwords-form').setAttribute('aria-busy', String(busy));
  const courier = managedCouriers.find(c => String(c.id) === $('managed-courier').value);
  $('managed-courier').disabled = busy; $('managed-password').disabled = busy;
  $('save-courier').disabled = busy || !courier;
  $('forget-courier').disabled = busy || !courier?.has_password;
  $('reveal-courier').disabled = busy || !courier?.has_password;
  for (const button of $('saved-couriers-list').querySelectorAll('button')) button.disabled = busy;
}

function managerSelection() {
  clearRevealedPassword(); $('manager-message').textContent = '';
  const courier = managedCouriers.find(c => String(c.id) === $('managed-courier').value);
  $('managed-password').value = ''; $('managed-password').required = !!courier;
  $('manager-editor').hidden = !courier;
  $('manager-hint').textContent = courier?.has_password ? 'Heslo je uložené. Môžeš ho zobraziť, nahradiť alebo odstrániť.' : courier ? 'Zadaj známe heslo tohto kuriéra.' : 'Klikni na uloženého kuriéra alebo vyber kuriéra zo zoznamu.';
  setManagerBusy(managerBusy); renderSavedCouriers();
}

async function loadManagedCouriers(preferred = '') {
  const generation = ++managerLoad;
  setManagerBusy(true); clearRevealedPassword();
  $('managed-courier').replaceChildren(new Option('Načítavam…', '')); $('manager-editor').hidden = true;
  let choicesError = '';
  try {
    const saved = await api('courier-passwords');
    if (generation !== managerLoad || !$('passwords-dialog').open) return false;
    savedCouriers = saved.couriers;
    const savedIds = new Set(savedCouriers.map(c => String(c.id)));
    for (const courier of state.couriers) courier.has_password = savedIds.has(String(courier.id));
    renderSavedCouriers(); renderCourierSelection();
    let choices = [];
    const numbers = [...state.shipments.values()].slice(0, 200).map(s => s.trackNumber);
    if (numbers.length) {
      try { choices = (await api('couriers', { numbers })).couriers; }
      catch (error) { choicesError = error.message; }
    }
    if (generation !== managerLoad || !$('passwords-dialog').open) return false;
    managedCouriers = [...choices, ...savedCouriers.filter(c => !choices.some(x => String(x.id) === String(c.id)))].map(c => ({ ...c, has_password: savedIds.has(String(c.id)) }));
    $('managed-courier').replaceChildren(new Option('Vyber kuriéra', ''), ...managedCouriers.map(c => new Option(`${c.code} · ${c.name}${c.has_password ? ' · uložené heslo' : ''}`, String(c.id))));
    if (managedCouriers.some(c => String(c.id) === preferred)) $('managed-courier').value = preferred;
    managerSelection();
    if (!managedCouriers.length) $('manager-hint').textContent = 'Noví kuriéri sa zobrazia, keď budú v SPS zásielky na príjem. Uložení kuriéri sú dostupní aj bez zásielok.';
    if (choicesError) $('manager-message').textContent = choicesError;
    return true;
  } catch (error) {
    if (generation === managerLoad && $('passwords-dialog').open) $('manager-message').textContent = error.message;
    throw error;
  } finally { if (generation === managerLoad && $('passwords-dialog').open) setManagerBusy(false); }
}

async function openCourierManager() {
  voice.stop(); stopCamera(); clearRevealedPassword();
  $('manager-message').textContent = ''; $('managed-password').value = '';
  savedCouriers = []; managedCouriers = []; renderSavedCouriers();
  if (!$('passwords-dialog').open) $('passwords-dialog').showModal();
  try { await loadManagedCouriers(); }
  catch (error) { if ($('passwords-dialog').open) $('manager-message').textContent = error.message; }
}

$('reveal-courier').addEventListener('click', async () => {
  if (managerBusy) return;
  if (!$('revealed-password').hidden) { clearRevealedPassword(); return; }
  const courierId = $('managed-courier').value;
  if (!managedCouriers.some(c => String(c.id) === courierId && c.has_password)) return;
  const generation = ++revealGeneration;
  $('manager-message').textContent = ''; $('reveal-courier').disabled = true;
  try {
    const result = await api('courier-passwords/reveal', { courier_id: courierId });
    if (generation !== revealGeneration || !$('passwords-dialog').open || $('managed-courier').value !== courierId) return;
    $('revealed-password').value = result.password; $('revealed-password').hidden = false;
    $('reveal-courier').textContent = 'Skryť uložené heslo'; $('reveal-courier').setAttribute('aria-expanded', 'true');
  } catch (error) {
    if (generation === revealGeneration && $('passwords-dialog').open) $('manager-message').textContent = error.message;
  } finally { if (generation === revealGeneration && $('passwords-dialog').open) setManagerBusy(managerBusy); }
});

$('passwords-form').addEventListener('submit', async event => {
  event.preventDefault();
  const courierId = $('managed-courier').value, password = $('managed-password').value, generation = managerLoad;
  if (managerBusy || !courierId || !password) return;
  clearRevealedPassword(); setManagerBusy(true); $('manager-message').textContent = '';
  try {
    await api('courier-passwords/save', { courier_id: courierId, password });
    if (generation !== managerLoad || !$('passwords-dialog').open) return;
    $('managed-password').value = '';
    if (await loadManagedCouriers(courierId)) $('manager-message').textContent = 'Heslo je uložené.';
  } catch (error) {
    if ($('passwords-dialog').open && generation === managerLoad) { $('manager-message').textContent = error.message; setManagerBusy(false); }
  }
});
$('forget-courier').addEventListener('click', async () => {
  const courierId = $('managed-courier').value, generation = managerLoad;
  if (managerBusy || !courierId) return;
  clearRevealedPassword(); setManagerBusy(true); $('manager-message').textContent = '';
  try {
    await api('courier-passwords/remove', { courier_id: courierId });
    if (generation !== managerLoad || !$('passwords-dialog').open) return;
    if (await loadManagedCouriers(courierId)) $('manager-message').textContent = 'Uložené heslo bolo odstránené.';
  } catch (error) {
    if ($('passwords-dialog').open && generation === managerLoad) { $('manager-message').textContent = error.message; setManagerBusy(false); }
  }
});

function showLogin() {
  voice.stop();
  stopCamera(); $('confirm-dialog').close(); $('courier-password').value = '';
  clearRevealedPassword(); managerLoad++; $('passwords-dialog').close(); $('managed-password').value = ''; state.couriers = []; managedCouriers = []; savedCouriers = [];
  $('manage-couriers').hidden = true;
  closeToolsMenu(); $('more-tools').hidden = true;
  clearTimeout(courierTimer); courierRequest++; courierFingerprint = '';
  workspace.reset(); state.courierId = '';
  state.csrf = ''; $('login-view').hidden = false; $('work-view').hidden = true; $('logout').hidden = true;
}

async function showWork(session) {
  state.csrf = session.csrf; state.demo = session.demo;
  $('manage-couriers').hidden = false;
  $('more-tools').hidden = false;
  $('login-view').hidden = true; $('work-view').hidden = false; $('logout').hidden = false;
  $('demo-banner').hidden = !session.demo; $('demo-add').hidden = !session.demo; $('demo-password').hidden = !session.demo;
  try { await workspace.start(session); } catch (error) { workspace.notice(error.message, 'error'); }
  await loadShipments();
}

function closeToolsMenu() {
  $('tools-menu').hidden = true; $('more-tools').setAttribute('aria-expanded', 'false');
}

$('more-tools').addEventListener('click', () => {
  const open = $('tools-menu').hidden;
  if (open) { voice.stop(); stopCamera(); }
  $('tools-menu').hidden = !open; $('more-tools').setAttribute('aria-expanded', String(open));
});
$('tools-menu').addEventListener('click', event => { if (event.target.closest('button')) closeToolsMenu(); });
document.addEventListener('pointerdown', event => { if (!event.target.closest('.more-tools-wrap')) closeToolsMenu(); });
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !$('tools-menu').hidden) { closeToolsMenu(); $('more-tools').focus({ preventScroll: true }); }
});

$('login-form').addEventListener('submit', async event => {
  event.preventDefault(); $('login-button').disabled = true; $('login-error').textContent = '';
  try { const session = await api('login', { password: $('app-password').value }); $('app-password').value = ''; await showWork(session); }
  catch (error) { $('login-error').textContent = error.message; }
  finally { $('login-button').disabled = false; }
});
$('logout').addEventListener('click', async () => {
  voice.stop();
  try { await api('logout', {}); workspace.saveDraft(); workspace.account = ''; state.selected.clear(); state.shipments.clear(); state.finished = false; showLogin(); }
  catch (error) { feedback(error.message, 'error'); }
});
$('manual-form').addEventListener('submit', event => { event.preventDefault(); if ($('manual-form').hidden || !scannerVisible()) return; initAudio(); scanCode($('manual-code').value); $('manual-code').value = ''; });
$('scan').addEventListener('click', startCamera);
$('voice').addEventListener('click', toggleVoice);
$('torch').addEventListener('click', async () => { try { torchOn = !torchOn; await scanner?.switchTorch?.(torchOn); $('torch').textContent = torchOn ? 'Vypnúť svetlo' : 'Prisvietiť'; } catch { feedback('Tento fotoaparát nepodporuje prisvietenie.', 'warning'); } });
$('refresh').addEventListener('click', loadShipments);
$('toggle-filter').addEventListener('click', () => setReceiptFilter($('search').hidden));
$('search').addEventListener('input', renderCatalog);
$('search').addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  event.preventDefault(); setReceiptFilter(false); $('toggle-filter').focus({ preventScroll: true });
});
$('select-all').addEventListener('click', selectAllReceipt);
$('clear-selection').addEventListener('click', clearReceipt);
$('prepare').addEventListener('click', prepareReceipt);
$('cancel-confirm').addEventListener('click', () => { $('courier-password').value = ''; $('confirm-dialog').close(); });
$('confirm-dialog').addEventListener('cancel', () => { $('courier-password').value = ''; });
$('confirm-form').addEventListener('submit', receive);
$('courier').addEventListener('change', () => { state.courierId = $('courier').value; courierChanged(); render(); });
$('receipt-courier').addEventListener('change', () => { state.courierId = $('receipt-courier').value; render(); });
$('confirm-dialog').addEventListener('close', () => { $('courier-password').value = ''; });
$('manage-couriers').addEventListener('click', openCourierManager);
$('managed-courier').addEventListener('change', managerSelection);
$('close-passwords').addEventListener('click', () => $('passwords-dialog').close());
$('passwords-dialog').addEventListener('close', () => { managerLoad++; clearRevealedPassword(); $('managed-password').value = ''; setManagerBusy(false); });
$('new-receipt').addEventListener('click', async () => { state.selected.clear(); state.finished = false; state.courierId = ''; courierFingerprint = ''; workspace.receiptNumber = null; workspace.renderReceiptActive(); workspace.saveDraft(); $('result').hidden = true; await loadShipments(); window.scrollTo({ top: 0, behavior: 'smooth' }); });
$('demo-add').addEventListener('click', () => { initAudio(); const next = [...state.shipments.values()].find(s => !state.selected.has(normalizeCode(s.trackNumber))); if (next) scanCode(next.trackNumber); else feedback('Všetky skúšobné balíky sú už pridané.'); });
document.addEventListener('visibilitychange', () => { if (document.hidden) { stopCamera(); voice.stop(); } });
window.addEventListener('pagehide', () => { stopCamera(); voice.stop(); });

api('session').then(showWork).catch(() => showLogin());
