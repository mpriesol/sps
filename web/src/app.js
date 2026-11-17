import { BarcodeFormat, DecodeHintType } from '@zxing/library';
import { PreviewBarcodeReader } from './camera.js';
import { playScanSound } from './scan-feedback.js';
import { matchSpokenSuffix, VoiceInput } from './voice.js';
import { classifyScan, normalizeCode, remainingItems, parcelCount, receiptComplete } from './domain.js';
import { WarehouseWorkspace } from './warehouse.js';
import { chooseCourier, operationId, suffix } from './warehouse-domain.js';
import './style.css';

const $ = id => document.getElementById(id);
const state = { csrf: '', demo: false, shipments: new Map(), selected: new Map(), couriers: [], courierId: '', courierResponse: null, loading: false, preparing: false, receiving: false, finished: false };
let courierTimer = null, courierRequest = 0, courierFingerprint = '', scanQueue = Promise.resolve();
let scanner = null, cameraStarting = false, cameraGeneration = 0, torchOn = false, audio = null;
let managedCouriers = [];
let spokenPrompt = null, promptTimer = null, wakeLock = null, wakeGeneration = 0;
const voice = new VoiceInput({ onTranscript: scanSpeech, onState: renderVoice,
  onError: message => feedback(message, 'error') });
const workspace = new WarehouseWorkspace({ api, feedback, beep, state, stopInput: () => { voice.stop(); stopCamera(); }, renderReceipt: render });

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

function scanCode(value) {
  const view = workspace.view;
  scanQueue = scanQueue.then(() => { if (workspace.view === view) return processScan(value); }).catch(error => feedback(error.message, 'error'));
  return scanQueue;
}

async function processScan(value) {
  if (state.preparing || state.receiving || workspace.photoBusy || document.querySelector('dialog[open]')) return;
  const code = normalizeCode(value);
  if (await workspace.scan(code)) return;
  if (state.loading || state.finished) return;
  const result = classifyScan(code, state.shipments, state.selected);
  if (result.kind === 'empty') return;
  beep(result.kind);
  if (result.kind === 'duplicate') return feedback(`${code} už je v tejto dávke.`, 'warning');
  if (result.kind === 'unknown') return feedback(`${code}: nie je v zozname SPS. Skús iný kód na štítku alebo obnov zoznam.`, 'error');
  if (result.kind === 'full') return feedback('V jednej dávke môže byť najviac 200 balíkov.', 'warning');
  state.selected.set(result.code, { ...result.shipment, state: 'pending', message: '' });
  feedback(`✓ ${suffix(code)} · ${result.shipment.trackNumber} · pridané do dávky`, 'success');
  render();
  try { await workspace.onReceiptParcel(code); }
  catch (error) { feedback(`Balík je v dávke, ale lokálny záznam sa neuložil: ${error.message}`, 'error'); }
}

function renderVoice(active) {
  $('voice').textContent = active ? 'Stop hlas' : 'Hlasom';
  $('voice').setAttribute('aria-pressed', String(active));
  $('voice-placeholder').hidden = !active;
  if (!active) cancelVoicePrompt();
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
      spokenPrompt = null; voice.resume();
    };
    // Let the three error beeps finish before speaking.
    promptTimer = setTimeout(() => {
      if (voice.active && spokenPrompt === prompt) {
        try { speechSynthesis.speak(prompt); }
        catch { spokenPrompt = null; voice.resume(); }
      }
    }, 600);
  } catch { spokenPrompt = null; voice.resume(); }
}

function scanSpeech(transcript) {
  if (!voice.active || state.loading || state.preparing || state.receiving || state.finished) return;
  const result = matchSpokenSuffix(transcript, state.shipments, state.selected);
  $('voice-transcript').textContent = `Počul som: „${transcript.slice(0, 100)}“${result.suffix ? ` → ${result.suffix}` : ''}`;
  if (result.kind === 'match') { $('voice-prompt').textContent = 'Povedz posledné 2 znaky'; scanCode(result.code); return; }
  beep('unknown');
  if (result.kind === 'ambiguous') {
    const length = result.suffix.length + 1;
    $('voice-prompt').textContent = `Povedz posledné ${length} ${length <= 4 ? 'znaky' : 'znakov'}`;
    feedback(`Koncovka ${result.suffix}: ${parcelCount(result.count)}. ${$('voice-prompt').textContent} znova.`, 'warning');
    sayVoicePrompt(`Viac zhôd. ${$('voice-prompt').textContent}.`);
  } else if (result.kind === 'unknown') {
    feedback(`${result.suffix}: žiadny kód v zozname SPS takto nekončí. Skús to znova.`, 'error');
    sayVoicePrompt('Balík sa nenašiel. Zopakuj koniec kódu.');
  } else {
    feedback('Povedz posledné dva znaky, každý zvlášť. Napríklad „pé tri“ alebo „nula sedem“.', 'warning');
    sayVoicePrompt('Nerozumiem. Povedz posledné dva znaky, každý zvlášť.');
  }
}

function toggleVoice() {
  if (voice.active) { voice.stop(); feedback('Hlasové zadávanie je vypnuté.'); return; }
  if (state.loading || state.preparing || state.receiving || state.finished) return;
  if (!window.isSecureContext) { feedback('Mikrofón potrebuje HTTPS.', 'error'); return; }
  initAudio(); stopCamera();
  $('voice-transcript').textContent = '';
  $('voice-prompt').textContent = 'Povedz posledné 2 znaky';
  feedback('Povoľ mikrofón a povedz posledné dva znaky kódu.');
  voice.start();
}

function render() {
  const busy = state.loading || state.preparing || state.receiving || workspace.viewLoading;
  const workflowBusy = busy || workspace.busy || workspace.returnBusy;
  $('work-view').setAttribute('aria-busy', String(workflowBusy));
  for (const node of $('workspace-nav').children) node.disabled = workflowBusy;
  const receipt = workspace.view === 'receipt';
  const n = state.selected.size, remaining = remainingItems(state.selected).length;
  const accepted = [...state.selected.values()].filter(x => x.state === 'accepted').length;
  $('selected-count').textContent = n;
  $('available-count').textContent = `${state.shipments.size} v zozname SPS`;
  $('catalog-count').textContent = state.shipments.size;
  $('bottom-count').textContent = state.receiving ? `${accepted} / ${n} prijatých` : parcelCount(n);
  $('bottom-help').textContent = state.receiving ? 'Odosielam do SPS…' : state.finished ? 'Príjem dokončený' : 'Naskenované, ešte nepotvrdené';
  $('prepare').textContent = accepted ? `Prijať zvyšných ${remaining}` : `Prijať ${n || ''} ${n === 1 ? 'balík' : n >= 2 && n <= 4 ? 'balíky' : 'balíkov'}`;
  $('prepare').disabled = !remaining || busy || !state.courierId;
  $('prepare').hidden = state.finished;
  $('new-receipt').hidden = !state.finished;
  $('refresh').disabled = busy;
  $('manage-couriers').disabled = busy || workspace.busy || workspace.returnBusy;
  $('scan').disabled = (receipt && (busy || state.finished)) || cameraStarting || workspace.busy || workspace.returnBusy || workspace.viewLoading;
  $('voice').disabled = busy || state.finished || cameraStarting;
  $('manual-add').disabled = (receipt && (busy || state.finished)) || workspace.busy || workspace.returnBusy || workspace.viewLoading;
  $('manual-code').disabled = (receipt && (busy || state.finished)) || workspace.busy || workspace.returnBusy || workspace.viewLoading;
  $('logout').disabled = workflowBusy;
  $('demo-add').disabled = busy || state.finished;
  $('empty-selection').hidden = n > 0;
  const list = $('selected-list'); list.replaceChildren();
  for (const [code, item] of [...state.selected].reverse()) {
    const li = text('li', '', `parcel ${item.state}`);
    const badge = text('span', suffix(item.trackNumber), 'parcel-icon parcel-suffix-small');
    const info = text('div', '', 'parcel-info');
    info.append(text('strong', item.trackNumber), text('span', item.senderName || 'Zásielka SPS', 'muted'));
    const local = workspace.parcel(item.trackNumber);
    if (local?.location_id) info.append(text('span', workspace.locationName(local.location_id), 'receipt-place-name'));
    if (item.message) info.append(text('small', item.message, item.state === 'failed' ? 'error-text' : 'muted'));
    li.append(badge, info);
    if (item.state === 'uncertain') info.append(text('small', 'Výsledok neznámy — over ho v SPS cez Uložiť / nájsť.', 'error-text'));
    if (!['accepted', 'uncertain'].includes(item.state) && !state.receiving && !state.preparing) {
      const remove = text('button', '×', 'remove');
      remove.type = 'button'; remove.setAttribute('aria-label', `Odstrániť ${item.trackNumber}`);
      remove.addEventListener('click', () => {
        state.selected.delete(code);
        state.finished = receiptComplete(state.selected);
        render();
      });
      li.append(remove);
    }
    list.append(li);
  }
  renderCatalog();
  renderCourierSelection(); workspace.saveDraft(); scheduleCouriers();
}

function renderCourierSelection() {
  const select = $('receipt-courier'), previous = select.value;
  select.replaceChildren(new Option(state.selected.size ? 'Vyber kuriéra' : 'Najprv naskenuj balík', ''));
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

function renderCatalog() {
  const query = normalizeCode($('search').value), list = $('catalog-list');
  list.replaceChildren();
  const matches = [...state.shipments.values()].filter(s => normalizeCode(`${s.trackNumber} ${s.senderName}`).includes(query));
  for (const shipment of matches.slice(0, 100)) {
    const li = text('li', '', 'parcel'), info = text('div', '', 'parcel-info');
    info.append(text('strong', shipment.trackNumber), text('span', shipment.senderName || 'Zásielka SPS', 'muted'));
    const selected = state.selected.has(normalizeCode(shipment.trackNumber));
    const add = text('button', selected ? '✓' : '+', 'catalog-add');
    add.type = 'button'; add.disabled = selected || state.receiving || state.preparing || state.finished || state.loading;
    add.setAttribute('aria-label', `Pridať ${shipment.trackNumber}`);
    add.addEventListener('click', () => { initAudio(); scanCode(shipment.trackNumber); });
    li.append(info, add); list.append(li);
  }
  if (matches.length > 100) list.append(text('li', 'Zobrazených prvých 100. Použi vyhľadávanie.', 'footnote'));
  $('catalog-empty').hidden = matches.length !== 0;
}

async function loadShipments() {
  voice.stop();
  clearTimeout(courierTimer); courierRequest++; courierFingerprint = '';
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
  voice.stop();
  if (scanner) { stopCamera(); return; }
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    feedback('Fotoaparát potrebuje HTTPS. Otvor aplikáciu cez jej zabezpečenú adresu.', 'error'); return;
  }
  initAudio(); cameraStarting = true; render();
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
    $('scan').textContent = 'Pozastaviť kameru'; $('torch').hidden = !controls.switchTorch;
    feedback('Kamera je zapnutá. Namier čiarový kód do rámčeka.');
  } catch (error) {
    stopCamera();
    feedback(error.name === 'NotAllowedError' ? 'Povoľ prístup k fotoaparátu v nastavení prehliadača.' : 'Fotoaparát sa nepodarilo spustiť. Skús ho zapnúť znova alebo zadaj kód ručne.', 'error');
  } finally { cameraStarting = false; render(); }
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

function managerSelection() {
  const courier = managedCouriers.find(c => String(c.id) === $('managed-courier').value);
  $('managed-password').value = '';
  $('save-courier').disabled = !courier;
  $('forget-courier').disabled = !courier?.has_password;
  $('manager-hint').textContent = courier?.has_password ? 'Heslo je uložené. Môžeš ho nahradiť alebo odstrániť.' : 'Vyber kuriéra a zadaj známe heslo.';
}

async function loadManagedCouriers() {
  $('save-courier').disabled = true; $('forget-courier').disabled = true;
  $('managed-courier').replaceChildren(new Option('Načítavam…', ''));
  const saved = await api('courier-passwords');
  let choices = [];
  const numbers = [...state.shipments.values()].slice(0, 200).map(s => s.trackNumber);
  if (numbers.length) {
    try { choices = (await api('couriers', { numbers })).couriers; }
    catch (error) { $('manager-message').textContent = error.message; }
  }
  managedCouriers = [...choices, ...saved.couriers.filter(c => !choices.some(x => String(x.id) === String(c.id))).map(c => ({ ...c, has_password: true }))];
  $('managed-courier').replaceChildren(...managedCouriers.map(c => new Option(`${c.code} · ${c.name}${c.has_password ? ' · uložené heslo' : ''}`, String(c.id))));
  managerSelection();
  if (!managedCouriers.length) $('manager-hint').textContent = 'Zoznam kuriérov sa načíta, keď budú v SPS zásielky na príjem.';
}

async function openCourierManager() {
  voice.stop();
  stopCamera(); $('manager-message').textContent = '';
  $('passwords-dialog').showModal();
  try { await loadManagedCouriers(); }
  catch (error) { $('manager-message').textContent = error.message; }
}

$('passwords-form').addEventListener('submit', async event => {
  event.preventDefault();
  const courierId = $('managed-courier').value, password = $('managed-password').value;
  if (!courierId || !password) return;
  $('save-courier').disabled = true; $('forget-courier').disabled = true;
  try {
    await api('courier-passwords/save', { courier_id: courierId, password });
    $('managed-password').value = '';
    await loadManagedCouriers();
    $('managed-courier').value = courierId; managerSelection();
    $('manager-message').textContent = 'Heslo je uložené.';
  } catch (error) { $('manager-message').textContent = error.message; $('save-courier').disabled = false; }
});
$('forget-courier').addEventListener('click', async () => {
  const courierId = $('managed-courier').value;
  if (!courierId) return;
  $('save-courier').disabled = true; $('forget-courier').disabled = true;
  try {
    await api('courier-passwords/remove', { courier_id: courierId });
    await loadManagedCouriers();
    if (managedCouriers.some(c => String(c.id) === courierId)) $('managed-courier').value = courierId;
    managerSelection(); $('manager-message').textContent = 'Uložené heslo bolo odstránené.';
  } catch (error) { $('manager-message').textContent = error.message; managerSelection(); }
});

function showLogin() {
  voice.stop();
  stopCamera(); $('confirm-dialog').close(); $('courier-password').value = '';
  $('passwords-dialog').close(); $('managed-password').value = ''; state.couriers = []; managedCouriers = [];
  $('manage-couriers').hidden = true;
  clearTimeout(courierTimer); courierRequest++; courierFingerprint = '';
  workspace.reset(); state.courierId = '';
  state.csrf = ''; $('login-view').hidden = false; $('work-view').hidden = true; $('logout').hidden = true;
}

async function showWork(session) {
  state.csrf = session.csrf; state.demo = session.demo;
  $('manage-couriers').hidden = false;
  $('login-view').hidden = true; $('work-view').hidden = false; $('logout').hidden = false;
  $('demo-banner').hidden = !session.demo; $('demo-add').hidden = !session.demo; $('demo-password').hidden = !session.demo;
  try { await workspace.start(session); } catch (error) { workspace.notice(error.message, 'error'); }
  await loadShipments();
}

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
$('manual-form').addEventListener('submit', event => { event.preventDefault(); initAudio(); scanCode($('manual-code').value); $('manual-code').value = ''; });
$('scan').addEventListener('click', startCamera);
$('voice').addEventListener('click', toggleVoice);
$('torch').addEventListener('click', async () => { try { torchOn = !torchOn; await scanner?.switchTorch?.(torchOn); $('torch').textContent = torchOn ? 'Vypnúť svetlo' : 'Prisvietiť'; } catch { feedback('Tento fotoaparát nepodporuje prisvietenie.', 'warning'); } });
$('refresh').addEventListener('click', loadShipments);
$('search').addEventListener('input', renderCatalog);
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
$('passwords-dialog').addEventListener('close', () => { $('managed-password').value = ''; });
$('new-receipt').addEventListener('click', async () => { state.selected.clear(); state.finished = false; state.courierId = ''; courierFingerprint = ''; workspace.receiptNumber = null; workspace.renderReceiptActive(); workspace.saveDraft(); $('result').hidden = true; await loadShipments(); window.scrollTo({ top: 0, behavior: 'smooth' }); });
$('demo-add').addEventListener('click', () => { initAudio(); const next = [...state.shipments.values()].find(s => !state.selected.has(normalizeCode(s.trackNumber))); if (next) scanCode(next.trackNumber); else feedback('Všetky skúšobné balíky sú už pridané.'); });
document.addEventListener('visibilitychange', () => { if (document.hidden) { stopCamera(); voice.stop(); } });
window.addEventListener('pagehide', () => { stopCamera(); voice.stop(); });

api('session').then(showWork).catch(() => showLogin());
