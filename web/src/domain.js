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

export const remainingItems = selected => [...selected.values()].filter(item => item.state !== 'accepted');

export function parcelCount(n) {
  return `${n} ${n === 1 ? 'balík' : n >= 2 && n <= 4 ? 'balíky' : 'balíkov'}`;
}
