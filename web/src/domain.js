export const normalizeCode = value => String(value).trim().toUpperCase();

export function coverCrop(sourceWidth, sourceHeight, viewWidth, viewHeight) {
  const ratio = viewWidth && viewHeight ? viewWidth / viewHeight : sourceWidth / sourceHeight;
  const width = Math.min(sourceWidth, sourceHeight * ratio);
  const height = Math.min(sourceHeight, sourceWidth / ratio);
  return { x: (sourceWidth - width) / 2, y: (sourceHeight - height) / 2, width, height };
}

export function classifyScan(value, shipments, selected) {
  const code = normalizeCode(value);
  if (!code) return { kind: 'empty', code };
  if (selected.has(code)) return { kind: 'duplicate', code };
  const shipment = shipments.get(code);
  if (!shipment) return { kind: 'unknown', code };
  if (selected.size >= 200) return { kind: 'full', code };
  return { kind: 'match', code, shipment };
}

export const remainingItems = selected => [...selected.values()].filter(item => !['accepted', 'uncertain'].includes(item.state));
export const receiptComplete = selected => selected.size > 0 && [...selected.values()].every(item => item.state === 'accepted');
export const receiptSelectionLocked = item => !!item && ['accepted', 'sending', 'uncertain'].includes(item.state);

export function receiptItems(shipments, selected, query = '') {
  const merged = new Map();
  for (const [code, item] of shipments) merged.set(normalizeCode(code), { ...item, code: normalizeCode(code), isSelected: false });
  for (const [code, item] of selected) merged.set(normalizeCode(code), { ...merged.get(normalizeCode(code)), ...item, code: normalizeCode(code), isSelected: true });
  const filter = normalizeCode(query);
  return [...merged.values()].filter(item => item.code.includes(filter));
}

export function selectReceiptItems(selected, items, limit = 200) {
  const next = new Map(selected); let added = 0, limited = false;
  for (const item of items) {
    const code = normalizeCode(item.trackNumber || item.code);
    if (next.has(code)) continue;
    if (next.size >= limit) { limited = true; continue; }
    next.set(code, { ...item, trackNumber: item.trackNumber || code, state: 'pending', message: '' }); added++;
  }
  return { selected: next, added, limited };
}

export function clearReceiptSelection(selected) {
  return new Map([...selected].filter(([, item]) => receiptSelectionLocked(item)));
}

export function parcelCount(n) {
  return `${n} ${n === 1 ? 'balík' : n >= 2 && n <= 4 ? 'balíky' : 'balíkov'}`;
}
