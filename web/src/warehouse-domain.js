import { normalizeCode } from './domain.js';

export const operationId = () => crypto.randomUUID();
export const suffix = number => normalizeCode(number).slice(-2);
export const isTerminal = parcel => ['delivered', 'returned'].includes(parcel?.carrier_status);

// Customer drop-off codes are new to this device. A short suffix must never
// become a new shipment merely because it happens to be unique locally.
export function validDropoffCode(value) {
  const code = normalizeCode(value);
  return /^[A-Z0-9][A-Z0-9._-]{5,99}$/.test(code) && !code.startsWith('LOC-');
}

export function canConfirmPickup(attempt, scanRequired = false) {
  if (!attempt?.parcel || attempt.uncertain || attempt.detailsStale || (scanRequired && !attempt.verified)) return false;
  const cod = Number(attempt.parcel.cod ?? 0);
  return Number.isFinite(cod) && cod >= 0 && (cod === 0 || attempt.codConfirmed === true);
}

export function parcelIntakeTime(parcel) {
  if (parcel?.received_at) return { value: parcel.received_at, label: 'Prijaté' };
  if (parcel?.scanned_at) return { value: parcel.scanned_at, label: 'Naskenované' };
  return null;
}

export function chooseCourier(response, currentId = '') {
  const choices = response.couriers || [];
  if (currentId && choices.some(c => String(c.id) === String(currentId))) return String(currentId);
  if (currentId || response.requires_selection || response.suggested == null) return '';
  return choices.some(c => String(c.id) === String(response.suggested)) ? String(response.suggested) : '';
}

export function routeWarehouseScan(value, locations, activeNumber) {
  const code = normalizeCode(value);
  const location = locations.find(place => normalizeCode(place.barcode) === code);
  if (location) return { kind: activeNumber ? 'move' : 'location', location, number: activeNumber || null };
  if (code.startsWith('LOC:') || code.startsWith('LOC-')) return { kind: 'unknown-location', code };
  return { kind: 'parcel', code };
}

export function draftKey(account) { return `sps:receipt:v2:${account || 'default'}`; }

export function serializeDraft(selected, courierId) {
  return JSON.stringify({ version: 2, savedAt: Date.now(), courierId: String(courierId || ''),
    parcels: [...selected.values()].map(item => ({ trackNumber: normalizeCode(item.trackNumber), senderName: String(item.senderName || '').slice(0, 200),
      state: item.state === 'sending' ? 'uncertain' : item.state, message: String(item.message || '').slice(0, 300), operationId: item.operationId || null })) });
}

export function restoreDraft(raw) {
  try {
    const data = JSON.parse(raw);
    if (data.version !== 2 || !Array.isArray(data.parcels) || data.parcels.length > 200) return null;
    const parcels = data.parcels.filter(p => typeof p.trackNumber === 'string' && /^[A-Z0-9._/-]{1,100}$/i.test(p.trackNumber)).map(p => ({
      trackNumber: normalizeCode(p.trackNumber), senderName: String(p.senderName || '').slice(0, 200),
      state: ['accepted', 'uncertain'].includes(p.state) ? p.state : 'pending',
      message: p.state === 'uncertain' ? 'Príjem bol prerušený. Najprv over stav v SPS.' : String(p.message || '').slice(0, 300),
      operationId: typeof p.operationId === 'string' ? p.operationId : null,
    }));
    // Receipt placement is a shared setting. Old drafts must not override it.
    return { parcels, courierId: String(data.courierId || '') };
  } catch { return null; }
}

export function suffixCollisions(parcels, number) {
  return parcels.filter(p => !isTerminal(p) && p.number !== number && suffix(p.number) === suffix(number));
}

export function handoverEligible(remote, local = remote?.local) {
  return remote?.returnEligible === true && remote?.statusCode !== 5555 && !isTerminal(local)
    && !local?.pending_operation && (!local || local.carrier_status === 'accepted');
}

export function handoverGroups(parcels) {
  const groups = { outgoing: [], returns: [], unknown: [] };
  for (const parcel of parcels) groups[parcel.handoverKind === 'outgoing' ? 'outgoing' : parcel.handoverKind === 'return' ? 'returns' : 'unknown'].push(parcel);
  return groups;
}

export function warehouseVoiceContext(workspace) {
  const { view, state, inventory, pickup, dropoff } = workspace;
  const pickupScanRequired = workspace.settings?.pickup_scan_required === true;
  const selected = new Map();
  const candidates = new Map();
  const add = parcel => {
    const code = normalizeCode(parcel?.trackNumber || parcel?.number || '');
    if (code) candidates.set(code, { ...parcel, trackNumber: code });
  };
  if (view === 'receipt') {
    for (const parcel of state.shipments.values()) add(parcel);
    for (const [code, parcel] of state.selected) { add(parcel); selected.set(code, parcel); }
  } else {
    // Include all reasonable physical parcels. The expected subset cannot make
    // an otherwise ambiguous two-character suffix magically unique.
    for (const parcel of workspace.parcels) if (!isTerminal(parcel)) add(parcel);
    if (view === 'returns') {
      for (const parcel of workspace.returns) add(parcel);
      for (const code of workspace.returnScans) selected.set(code, candidates.get(code) || { trackNumber: code });
    }
    if (inventory) for (const code of inventory.numbers) { add({ trackNumber: code }); selected.set(code, candidates.get(code)); }
    if (pickup) add(pickup.parcel);
  }
  const key = JSON.stringify([view, workspace.viewGeneration || 0, workspace.activeNumber, workspace.activeLocation,
    workspace.receiptNumber, workspace.batchLocation, workspace.receiptPlacementEnabled(), pickup?.lookup_id,
    pickup?.verified, pickup?.uncertain, pickup?.detailsStale, pickupScanRequired, workspace.settings?.manual_entry === true,
    dropoff?.number, dropoff?.state, dropoff?.operationId,
    inventory?.location_id, [...selected.keys()].sort(), [...candidates.keys()].sort(),
    workspace.viewLoading, workspace.busy, workspace.photoBusy, workspace.photoGeneration || 0, workspace.returnBusy, state.preparing, state.receiving, state.finished,
    view === 'returns' ? workspace.returnsGeneration || 0 : 0,
    view === 'receipt' || view === 'store' ? workspace.locations.map(location => [location.barcode, location.name]) : []]);
  const enabled = ['receipt', 'store', 'pickup', 'returns', 'dropoff'].includes(view) && !workspace.viewLoading && !workspace.busy
    && !workspace.photoBusy && !workspace.returnBusy && !state.preparing && !state.receiving
    && !(view === 'receipt' && state.finished) && !(view === 'pickup' && (!pickup || pickup.uncertain || !pickupScanRequired));
  return { key, candidates, selected, requireFullCode: view === 'pickup' || view === 'dropoff',
    allowUnknownFullCode: view === 'dropoff', enabled,
    allowLocationNames: view === 'receipt' || view === 'store',
    prompt: view === 'dropoff' ? 'Povedz celé číslo balíka od zákazníka.'
      : view === 'pickup' ? 'Povedz celý kód balíka, ktorý držíš v ruke.'
      : inventory ? 'Povedz koncovku balíka do inventúry.'
      : view === 'returns' ? 'Povedz koncovku balíka na odovzdanie kuriérovi.'
      : view === 'store' ? 'Povedz koncovku balíka alebo „miesto“ a názov police.'
      : 'Povedz koncovku balíka na príjem.' };
}

// Only an authenticated server snapshot may unblock a locally interrupted write.
// Retain the same idempotency ID: an original request may still arrive late.
export function reconcileReceiptItem(item, local) {
  if (!local || item.state === 'sending') return item;
  if (local.pending_operation?.kind === 'accept') return { ...item, state: 'uncertain', message: 'Výsledok príjmu treba overiť v SPS.' };
  if (local.carrier_status === 'accepted' && !local.pending_operation && item.state !== 'sending') return { ...item, state: 'accepted', message: 'Príjem potvrdený v SPS' };
  if (item.state === 'uncertain' && local.carrier_status === 'unconfirmed' && !local.pending_operation) return {
    ...item, state: 'pending', message: 'Požiadavka v evidencii servera nie je potvrdená; môžeš zopakovať kontrolovaný pokus.',
  };
  return item;
}

export function reconcilePickupAttempt(attempt, local) {
  if (!attempt?.uncertain || !local) return { kind: 'blocked' };
  if (local.carrier_status === 'delivered') return {
    kind: 'confirmed', result: { delivered: true, number: local.number, receipt_pending: Number(attempt.parcel.cod || 0) > 0 },
  };
  if (local.carrier_status === 'accepted' && !local.pending_operation) return { kind: 'retry', attempt: { ...attempt, uncertain: false, controlledRetry: true } };
  return { kind: 'blocked' };
}

export function reconcileDropoffAttempt(attempt, local) {
  // A direct successful response and the durable journal are proof. Imported
  // status/queue classification alone cannot prove this customer intake.
  if (attempt.state === 'accepted' || local?.dropoff_confirmed === true) return { ...attempt, state: 'accepted', local: local || attempt.local, controlledRetry: false };
  if (local?.pending_operation || local?.carrier_status?.endsWith('_uncertain')) return {
    ...attempt, state: 'uncertain', local,
    operationId: local.pending_operation?.kind === 'dropoff' ? local.pending_operation.operation_id : attempt.operationId,
  };
  if (local?.carrier_status === 'accepted' && attempt.state !== 'uncertain') return {
    ...attempt, local, state: 'blocked', message: 'SPS už tento balík eviduje na výdajnom mieste. Príjem neopakuj; over ho v SPS.',
  };
  if (attempt.state === 'uncertain' && attempt.operationId && (!local || local.carrier_status === 'unconfirmed')) return {
    ...attempt, state: 'ready', controlledRetry: true,
    message: 'Požiadavka v evidencii servera nie je potvrdená. Môžeš zopakovať kontrolovaný pokus.',
  };
  return { ...attempt, local: local || attempt.local };
}
