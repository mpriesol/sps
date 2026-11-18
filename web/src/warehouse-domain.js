import { normalizeCode } from './domain.js';

export const operationId = () => crypto.randomUUID();
export const suffix = number => normalizeCode(number).slice(-2);
export const isTerminal = parcel => ['delivered', 'returned'].includes(parcel?.carrier_status);

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

export function serializeDraft(selected, courierId, placeDuringReceipt) {
  return JSON.stringify({ version: 2, savedAt: Date.now(), courierId: String(courierId || ''), placeDuringReceipt: !!placeDuringReceipt,
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
    return { parcels, courierId: String(data.courierId || ''), placeDuringReceipt: !!data.placeDuringReceipt };
  } catch { return null; }
}

export function suffixCollisions(parcels, number) {
  return parcels.filter(p => !isTerminal(p) && p.number !== number && suffix(p.number) === suffix(number));
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
