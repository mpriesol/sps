import JsBarcode from 'jsbarcode';
import { normalizeCode, parcelCount, receiptComplete } from './domain.js';
import { chooseCourier, draftKey, isTerminal, operationId, reconcilePickupAttempt, reconcileReceiptItem, restoreDraft, routeWarehouseScan, serializeDraft, suffix, suffixCollisions } from './warehouse-domain.js';

const $ = id => document.getElementById(id);
const el = (tag, content = '', cls = '') => { const node = document.createElement(tag); node.textContent = content; node.className = cls; return node; };
const button = (label, action, cls = 'secondary compact') => { const node = el('button', label, cls); node.type = 'button'; node.addEventListener('click', action); return node; };
const statuses = { unconfirmed: 'Príjem do SPS nepotvrdený', accepted: 'Prijatý v SPS', accept_uncertain: 'Príjem treba overiť v SPS', delivery_uncertain: 'Výdaj treba overiť v SPS', return_uncertain: 'Vrátenie treba overiť v SPS', delivered: 'Vydaný zákazníkovi', returned: 'Odovzdaný kuriérovi' };
const PHOTO_MAX_EDGE = 640, PHOTO_MAX_BYTES = 40 * 1024;
const PHOTO_QUALITIES = [.70, .58, .46];

export class WarehouseWorkspace {
  constructor({ api, feedback, beep, state, stopInput, renderReceipt }) {
    Object.assign(this, { api, feedback, beep, state, stopInput, renderReceipt });
    this.view = 'receipt'; this.locations = []; this.parcels = []; this.settings = {}; this.activeNumber = null; this.activeLocation = null;
    this.receiptNumber = null; this.batchLocation = null; this.busy = false; this.photoBusy = false; this.inventory = null; this.pickup = null;
    this.returns = []; this.returnScans = new Set(); this.returnCouriers = []; this.returnCourierId = ''; this.returnBusy = false;
    this.account = ''; this.query = ''; this.printSelection = new Set(); this.bulkSource = ''; this.bulkTarget = ''; this.bulkSelected = new Set();
    this.viewLoading = false; this.refreshSequence = 0;
    this.mount();
  }

  mount() {
    const leading = el('section'); leading.id = 'workspace-leading'; $('scanner-workspace').before(leading);
    for (const [id, label] of [['receipt', 'Príjem'], ['store', 'Uložiť / nájsť'], ['pickup', 'Výdaj'], ['returns', 'Vratky'], ['locations', 'Miesta'], ['settings', 'Nastavenia']]) {
      const item = button(label, () => this.open(id), 'nav-button'); item.dataset.view = id; item.id = `nav-${id}`; $('workspace-nav').append(item);
    }
    $('receipt-place').addEventListener('change', () => { this.receiptNumber = null; this.saveDraft(); this.renderReceiptActive(); });
    const dialog = el('dialog'); dialog.id = 'warehouse-dialog'; document.body.append(dialog);
    const confirmAction = el('dialog'); confirmAction.id = 'confirm-action-dialog'; document.body.append(confirmAction);
    const photo = el('dialog'); photo.id = 'photo-dialog'; photo.innerHTML = '<h2>Fotografia balíka</h2><p id="photo-number" class="mono"></p><img id="photo-preview" class="photo-preview" alt="Náhľad fotografie balíka" hidden><p id="photo-error" class="error-text" role="alert"></p><div class="photo-actions"><button id="photo-frame" type="button" class="secondary">Odfotiť z kamery</button><button id="photo-file-button" type="button" class="secondary">Fotoaparát / súbor</button><input id="photo-file" type="file" accept="image/*" capture="environment" hidden></div><p class="footnote">Odtiahni telefón tak, aby bolo vidieť celý balík. Skenovanie kódov je zatiaľ pozastavené.</p><div class="dialog-actions"><button id="photo-cancel" type="button" class="secondary">Preskočiť</button><button id="photo-save" type="button" class="primary" disabled>Uložiť fotku</button></div>';
    document.body.append(photo);
    $('photo-cancel').addEventListener('click', () => photo.close());
    photo.addEventListener('close', () => { this.photoBusy = false; this.photoData = null; this.photoGeneration = (this.photoGeneration || 0) + 1; $('photo-file').value = ''; });
    $('photo-frame').addEventListener('click', () => {
      const video = $('video'); if (!video.videoWidth) return;
      this.preparePhoto(video, video.videoWidth, video.videoHeight);
    });
    $('photo-file-button').addEventListener('click', () => $('photo-file').click());
    $('photo-file').addEventListener('change', async () => {
      const file = $('photo-file').files[0]; if (!file) return;
      const generation = this.photoGeneration = (this.photoGeneration || 0) + 1;
      const current = () => generation === this.photoGeneration && photo.open;
      this.photoData = null; $('photo-preview').hidden = true; $('photo-save').disabled = true; $('photo-save').textContent = 'Pripravujem…'; $('photo-error').textContent = '';
      if (!file.type.startsWith('image/') || file.size > 20 * 1024 * 1024) { $('photo-error').textContent = 'Vyber fotografiu do 20 MB.'; $('photo-save').textContent = 'Uložiť fotku'; return; }
      try {
        const url = URL.createObjectURL(file), image = new Image();
        try {
          image.src = url; await image.decode();
          if (current()) await this.preparePhoto(image, image.naturalWidth, image.naturalHeight);
        }
        finally { URL.revokeObjectURL(url); }
      } catch { if (current()) $('photo-error').textContent = 'Fotografiu sa nepodarilo načítať. Skús JPEG alebo PNG.'; }
      finally { if (current()) $('photo-save').textContent = 'Uložiť fotku'; }
    });
    $('photo-save').addEventListener('click', async () => {
      if (!this.photoData || !this.photoNumber) return;
      const number = this.photoNumber, image = this.photoData;
      $('photo-save').disabled = true;
      try { await this.api('parcels/photo', { number, image }); if (this.photoNumber === number) photo.close(); await this.refresh(); this.notice(`Fotografia ${suffix(number)} je uložená.`, 'success'); }
      catch (error) { $('photo-error').textContent = `${error.message} Príjem ani miesto balíka sa nezmenili.`; $('photo-save').disabled = false; }
    });
    this.setNav();
  }

  async start(session) {
    this.viewLoading = true; this.renderReceipt(); this.render();
    this.account = session.account || (session.demo ? 'demo' : 'default');
    this.restoringDraft = true;
    let rawDraft = null;
    try { rawDraft = localStorage.getItem(draftKey(this.account)); } catch { /* Storage may be unavailable. */ }
    try { await this.refresh(); } catch (error) { this.notice(error.message, 'error'); }
    try {
      const draft = restoreDraft(rawDraft);
      if (draft?.parcels.length && this.state.selected.size === 0) {
        for (const item of draft.parcels) {
          const local = this.parcel(item.trackNumber);
          if (local?.carrier_status === 'accepted') { item.state = 'accepted'; item.message = 'Prijaté v SPS'; }
          if (isTerminal(local)) continue;
          this.state.selected.set(item.trackNumber, item);
        }
        this.state.courierId = draft.courierId;
        $('receipt-place').checked = draft.placeDuringReceipt;
        this.state.finished = receiptComplete(this.state.selected);
        this.notice('Obnovená posledná rozpracovaná dávka na tomto zariadení.');
      }
    } catch { /* Draft storage is optional. */ }
    finally { this.restoringDraft = false; this.viewLoading = false; }
    this.renderReceipt(); this.render();
  }

  reset() {
    this.stopInput(); this.pickup = null; this.activeNumber = null; this.receiptNumber = null; this.returnScans.clear(); this.photoBusy = false;
    $('photo-dialog').close(); $('warehouse-dialog').close(); $('confirm-action-dialog').close(); this.view = 'receipt'; this.setNav();
  }

  saveDraft() {
    if (!this.account || this.restoringDraft) return;
    try {
      if (!this.state.selected.size) localStorage.removeItem(draftKey(this.account));
      else localStorage.setItem(draftKey(this.account), serializeDraft(this.state.selected, this.state.courierId, $('receipt-place').checked));
    } catch { /* Receipt still works if local storage is unavailable. */ }
  }

  async refresh() {
    const sequence = ++this.refreshSequence;
    const data = await this.api('warehouse');
    if (sequence !== this.refreshSequence) return data;
    this.locations = data.locations || []; this.parcels = data.parcels || []; this.settings = data.settings || {};
    for (const [number, item] of this.state.selected) this.state.selected.set(number, reconcileReceiptItem(item, this.parcel(number)));
    this.state.finished = receiptComplete(this.state.selected);
    if (this.pickup?.uncertain) {
      const decision = reconcilePickupAttempt(this.pickup, this.parcel(this.pickup.parcel.trackNumber));
      if (decision.kind === 'confirmed') {
        this.lastPickupReceipt = decision.result; this.pickup = null;
        this.notice(`SPS výdaj ${decision.result.number} potvrdilo. Odpoveď sa stratila pri prenose; výdaj neopakuj.`, 'success');
      } else if (decision.kind === 'retry') {
        this.pickup = decision.attempt;
        this.notice('Požiadavka v evidencii servera nie je potvrdená; môžeš zopakovať kontrolovaný pokus.', 'warning');
      }
    }
    this.render(); this.renderReceiptActive(); this.renderReceipt(); return data;
  }

  async sync() {
    this.notice('Overujem stav uložených zásielok v SPS…');
    try { await this.api('warehouse/sync'); await this.refresh(); this.notice('Údaje zo SPS sú obnovené.', 'success'); }
    catch (error) { this.notice(error.message, 'error'); }
  }

  parcel(number) { return this.parcels.find(p => p.number === normalizeCode(number)); }
  location(id) { return this.locations.find(p => p.id === id); }
  locationName(id) { return this.location(id)?.name || 'Bez miesta'; }
  activeParcels() { return this.parcels.filter(p => !isTerminal(p)); }
  notice(message, kind = '') { $('workspace-status').textContent = message; $('workspace-status').className = `workspace-status ${kind}`; }

  async open(view) {
    if (this.state.preparing || this.state.receiving || this.returnBusy || this.busy || this.viewLoading) return;
    this.viewLoading = true; this.stopInput(); this.view = view; this.activeNumber = null; this.activeLocation = null; this.batchLocation = null; this.inventory = null;
    this.setNav(); this.render();
    this.feedback(view === 'store' ? 'Naskenuj balík alebo čiarový kód miesta.' : view === 'pickup' ? 'Najprv zadaj kód od zákazníka.' : view === 'returns' ? 'Naskenuj balíky, ktoré fyzicky odovzdávaš kuriérovi.' : 'Pripravené na skenovanie.');
    try { await this.refresh(); if (view === 'returns') await this.loadReturns(); }
    catch (error) { this.notice(error.message, 'error'); }
    finally { this.viewLoading = false; this.render(); this.renderReceipt(); }
  }

  setNav() {
    $('work-view').dataset.view = this.view;
    for (const node of $('workspace-nav').children) { const active = node.dataset.view === this.view; node.classList.toggle('active', active); node.setAttribute('aria-current', active ? 'page' : 'false'); }
    $('receipt-area').hidden = this.view !== 'receipt' || this.viewLoading; $('warehouse-panel').hidden = this.view === 'receipt' && !this.viewLoading;
    $('scanner-workspace').hidden = this.viewLoading || ['locations', 'settings'].includes(this.view) || (this.view === 'pickup' && !this.pickup); $('voice').hidden = this.view !== 'receipt';
    $('workspace-leading').hidden = this.view !== 'pickup';
    $('demo-add').hidden = !this.state.demo || this.view !== 'receipt';
    $('manual-code').placeholder = this.view === 'receipt' ? 'Číslo balíka alebo kód miesta' : 'Naskenuj alebo zadaj celý kód';
    $('manual-add').textContent = this.view === 'receipt' ? 'Pridať' : 'Načítať';
  }

  async onReceiptParcel(number) {
    const previous = this.receiptNumber;
    const known = this.state.shipments.get(normalizeCode(number));
    await this.api('parcels/register', { number, sender_name: known?.senderName || '' });
    await this.refresh();
    if ($('receipt-place').checked && previous && previous !== number && !this.parcel(previous)?.location_id) this.notice(`Predošlý balík ${suffix(previous)} (${previous}) zostal bez miesta. Môžeš ho uložiť neskôr cez Uložiť / nájsť.`, 'warning');
    this.receiptNumber = number; this.renderReceiptActive(); this.saveDraft();
  }

  async scan(value) {
    if (this.photoBusy || this.busy) return true;
    const code = normalizeCode(value); if (!code) return true;
    if (this.view === 'receipt') {
      const route = routeWarehouseScan(code, this.locations, $('receipt-place').checked ? this.receiptNumber : null);
      if (route.kind === 'parcel') return false;
      if (route.kind === 'unknown-location') { this.feedback('Toto miesto nepoznáme. Obnov zoznam miest.', 'error'); return true; }
      if (route.kind === 'move') await this.moveOne(route.number, route.location.id, true);
      else this.showLocation(route.location);
      return true;
    }
    if (this.view === 'pickup') { this.verifyPickup(code); return true; }
    if (this.view === 'returns') { await this.scanReturn(code); return true; }
    if (this.view !== 'store') return true;
    const route = routeWarehouseScan(code, this.locations, this.activeNumber);
    if (route.kind === 'unknown-location') { this.feedback('Neznámy kód miesta. Obnov zoznam.', 'error'); return true; }
    if (this.inventory) {
      if (route.location) {
        if (route.location.id !== this.inventory.location_id) this.feedback('Inventúra patrí inému miestu. Najprv ju ukonči.', 'warning');
      } else {
        const duplicate = this.inventory.numbers.has(code); this.inventory.numbers.add(code); this.beep(duplicate ? 'duplicate' : 'match');
        this.feedback(`${suffix(code)} · ${code}${duplicate ? ' už je v inventúre.' : ' načítaný do inventúry.'}`, duplicate ? 'warning' : 'success');
        await this.checkInventory();
      }
      return true;
    }
    if (route.location) {
      if (this.batchLocation) { this.batchLocation = route.location.id; this.activeLocation = route.location.id; this.render(); this.feedback(`Hromadné ukladanie do ${route.location.name}. Teraz skenuj balíky.`); }
      else if (route.kind === 'move') await this.moveOne(route.number, route.location.id);
      else this.showLocation(route.location);
      return true;
    }
    let parcel = this.parcel(code);
    if (!parcel) {
      this.stopInput();
      const confirmed = await this.ask('Balík zatiaľ nie je v evidencii', `${code}\nVytvoriť interný záznam a uložiť ho? Týmto sa nepotvrdí príjem do SPS.`, 'Vytvoriť záznam');
      if (!confirmed) return true;
      try { const result = await this.api('parcels/register', { number: code }); parcel = result.parcel; await this.refresh(); }
      catch (error) { this.feedback(error.message, 'error'); return true; }
    }
    if (isTerminal(parcel)) { this.feedback(`${code}: ${statuses[parcel.carrier_status]}. Miesto sa nezmenilo.`, 'error'); return true; }
    this.activeNumber = code; this.activeLocation = null; this.beep('match');
    if (this.batchLocation) await this.moveOne(code, this.batchLocation);
    else { this.render(); this.feedback(`${suffix(code)} · teraz ${this.locationName(parcel.location_id)}. Naskenuj cieľové miesto.`, 'success'); }
    return true;
  }

  async moveOne(number, locationId, receipt = false) {
    const parcel = this.parcel(number); if (!parcel) return;
    this.busy = true; this.renderReceipt();
    try {
      await this.api('parcels/move', { number, location_id: locationId, expected_version: parcel.version, operation_id: operationId() });
      await this.refresh(); this.beep('match'); this.feedback(`✓ ${suffix(number)} · uložené na ${this.locationName(locationId)}`, 'success');
      if (receipt) this.receiptNumber = null; else this.activeNumber = null;
      this.activeLocation = locationId; this.render(); this.renderReceiptActive();
    } catch (error) { this.feedback(error.message, 'error'); await this.refresh().catch(() => {}); }
    finally { this.busy = false; this.renderReceipt(); }
  }

  showLocation(location) {
    this.activeLocation = location.id;
    if (this.view === 'receipt') {
      const content = el('div'); this.appendParcelList(content, this.activeParcels().filter(p => p.location_id === location.id));
      this.showDialog(`Miesto ${location.name}`, content);
    } else this.render();
    this.feedback(`${location.name} · ${parcelCount(this.activeParcels().filter(p => p.location_id === location.id).length)}`, 'success');
  }

  parcelCard(parcel, { detailed = false, actions = true } = {}) {
    const card = el('article', '', `parcel-card ${isTerminal(parcel) ? 'terminal' : ''}`);
    const heading = el('div', '', 'parcel-card-heading'), tail = el('strong', suffix(parcel.number), 'parcel-suffix');
    const info = el('div', '', 'parcel-card-info'); info.append(el('strong', this.locationName(parcel.location_id), 'location-title'), el('span', parcel.number, 'mono'), el('span', parcel.sender_name || '', 'muted'));
    heading.append(tail, info); card.append(heading);
    const status = el('p', statuses[parcel.carrier_status] || parcel.carrier_status, `status-label ${parcel.carrier_status !== 'accepted' ? 'attention' : ''}`); card.append(status);
    if (parcel.pending_operation) card.append(el('p', parcel.pending_operation.outcome === 'pending' ? 'Operácia prebieha alebo čaká na overenie v SPS.' : 'Výsledok operácie nie je známy. Najprv ho over v SPS.', 'warning-note'));
    if (parcel.expires_at) card.append(el('p', `SPS: do ${parcel.expires_at}${parcel.expiry_synced_at ? ` · overené ${this.formatTime(parcel.expiry_synced_at)}` : ''}`, 'muted'));
    if (suffixCollisions(this.activeParcels().filter(p => p.location_id === parcel.location_id), parcel.number).length) card.append(el('p', 'Na tomto mieste je viac balíkov s rovnakou koncovkou. Over celý kód.', 'warning-note'));
    if (parcel.photo_url && detailed) { const image = el('img', '', 'parcel-photo'); image.src = parcel.photo_url; image.alt = `Balík ${parcel.number}`; card.append(image); }
    if (actions) {
      const row = el('div', '', 'inline-actions'); row.append(button('História', () => this.history(parcel.number)));
      if (parcel.pending_operation || parcel.carrier_status?.endsWith('_uncertain')) row.append(button('Overiť výsledok v SPS', () => this.resolveOperation(parcel), 'secondary compact'));
      if (this.settings.photos && !isTerminal(parcel)) row.append(button(parcel.photo_url ? 'Nová fotka' : 'Odfotiť', () => this.openPhoto(parcel.number)));
      if (this.view === 'store' && !isTerminal(parcel)) row.append(button('Presunúť', () => { this.activeNumber = parcel.number; this.activeLocation = null; this.render(); this.feedback(`${suffix(parcel.number)} · naskenuj cieľové miesto.`); }));
      card.append(row);
    }
    return card;
  }

  renderReceiptActive() {
    const node = $('receipt-active'); node.replaceChildren(); node.hidden = !this.receiptNumber;
    if (!this.receiptNumber) return;
    const parcel = this.parcel(this.receiptNumber); if (!parcel) return;
    if ($('receipt-place').checked) node.append(el('p', 'Teraz naskenuj miesto pre tento balík:', 'step-prompt'));
    node.append(this.parcelCard(parcel, { detailed: false }));
  }

  appendParcelList(container, parcels, options = {}) {
    if (!parcels.length) { container.append(el('p', 'Na tomto mieste nie sú evidované balíky.', 'empty-state')); return; }
    for (const parcel of parcels) container.append(this.parcelCard(parcel, options));
  }

  render() {
    this.setNav(); const panel = $('warehouse-panel');
    if (this.viewLoading) { panel.replaceChildren(el('p', 'Načítavam evidenciu…', 'empty-state')); $('workspace-leading').replaceChildren(); return; }
    if (this.view === 'receipt') return;
    panel.replaceChildren(); $('workspace-leading').replaceChildren();
    if (this.view === 'store') this.renderStore(panel);
    if (this.view === 'locations') this.renderLocations(panel);
    if (this.view === 'settings') this.renderSettings(panel);
    if (this.view === 'pickup') this.renderPickup(panel);
    if (this.view === 'returns') this.renderReturns(panel);
  }

  renderStore(panel) {
    const intro = el('div', '', 'card panel-heading'); intro.append(el('h2', this.inventory ? `Inventúra · ${this.locationName(this.inventory.location_id)}` : this.batchLocation ? `Ukladanie do ${this.locationName(this.batchLocation)}` : 'Uložiť, presunúť alebo nájsť'));
    intro.append(el('p', this.inventory ? 'Skenuj všetky balíky, ktoré na mieste fyzicky sú. Evidencia sa pri inventúre nemení.' : this.batchLocation ? 'Každý nasledujúci balík sa uloží na toto miesto. Cieľ zostáva viditeľný.' : 'Balík → miesto. Samotný sken miesta ukáže jeho obsah.', 'muted'));
    const controls = el('div', '', 'inline-actions'); controls.append(button('Obnoviť zo SPS', () => this.sync()), button('Presun medzi miestami', () => this.bulkMove()));
    if (this.inventory) controls.append(button('Ukončiť inventúru', () => { this.inventory = null; this.render(); }));
    if (this.batchLocation) controls.append(button('Ukončiť hromadné ukladanie', () => { this.batchLocation = null; this.render(); }));
    intro.append(controls); panel.append(intro);
    if (this.inventory) { this.renderInventory(panel); return; }
    if (this.activeNumber) {
      const parcel = this.parcel(this.activeNumber); if (parcel) panel.append(this.parcelCard(parcel, { detailed: true }));
      const chooser = this.locationSelect('manual-destination', '', 'Vyber cieľové miesto');
      const row = el('div', '', 'manual-location-row'); row.append(chooser, button('Uložiť', () => { if (chooser.value) this.moveOne(this.activeNumber, chooser.value); }, 'primary'));
      panel.append(row, button('Zrušiť výber balíka', () => { this.activeNumber = null; this.render(); })); return;
    }
    if (this.activeLocation) {
      const place = this.location(this.activeLocation); if (!place) return;
      const heading = el('div', '', 'location-overview'); heading.append(el('h2', place.name));
      const row = el('div', '', 'inline-actions');
      row.append(button('Ukladať viac balíkov sem', () => { this.batchLocation = place.id; this.render(); this.feedback(`Cieľ: ${place.name}. Skenuj balíky.`); }), button('Inventúra miesta', () => this.startInventory(place.id)), button('Všetky balíky', () => { this.activeLocation = null; this.render(); }));
      heading.append(row); panel.append(heading); this.appendParcelList(panel, this.activeParcels().filter(p => p.location_id === place.id)); return;
    }
    const counts = el('div', '', 'summary-strip'); counts.append(el('strong', `${this.activeParcels().length} v evidencii`), el('span', `${this.activeParcels().filter(p => !p.location_id).length} bez miesta`)); panel.append(counts);
    const search = el('input'); search.id = 'warehouse-search'; search.type = 'search'; search.placeholder = 'Hľadať celé číslo, koncovku alebo odosielateľa'; search.value = this.query;
    const list = el('div'); const draw = () => { list.replaceChildren(); const query = normalizeCode(search.value); this.query = search.value; const rows = this.activeParcels().filter(p => normalizeCode(`${p.number} ${p.sender_name} ${this.locationName(p.location_id)}`).includes(query)); this.appendParcelList(list, rows.slice(0, 200)); if (rows.length > 200) list.append(el('p', 'Zúž vyhľadávanie; zobrazených prvých 200.', 'muted')); };
    search.addEventListener('input', draw); panel.append(search);
    const choose = this.locationSelect('browse-location', '', 'Zobraziť obsah miesta…'); choose.addEventListener('change', () => { if (choose.value) this.showLocation(this.location(choose.value)); }); panel.append(choose, list); draw();
  }

  locationSelect(id, selected = '', placeholder = 'Vyber miesto', exclude = '') {
    const select = el('select'); select.id = id; select.setAttribute('aria-label', placeholder); select.append(new Option(placeholder, ''));
    for (const place of this.locations.filter(p => p.id !== exclude)) select.append(new Option(`${place.name} · ${place.count || 0} bal.`, place.id));
    select.value = selected; return select;
  }

  async bulkMove() {
    this.stopInput(); await this.refresh();
    const content = el('div'), source = this.locationSelect('bulk-source', this.bulkSource, 'Zo zdrojového miesta'), target = this.locationSelect('bulk-target', this.bulkTarget, 'Na cieľové miesto');
    const list = el('div', '', 'bulk-list'); const error = el('p', '', 'error-text');
    const draw = () => { list.replaceChildren(); this.bulkSelected = new Set(); for (const parcel of this.activeParcels().filter(p => p.location_id === source.value)) {
      const label = el('label', '', 'check-label'); const box = el('input'); box.type = 'checkbox'; box.checked = true; this.bulkSelected.add(parcel.number);
      box.addEventListener('change', () => box.checked ? this.bulkSelected.add(parcel.number) : this.bulkSelected.delete(parcel.number)); label.append(box, el('span', `${suffix(parcel.number)} · ${parcel.number}`)); list.append(label);
    } if (!list.children.length) list.append(el('p', 'Vyber zdrojové miesto s balíkmi.', 'muted')); };
    source.addEventListener('change', draw); content.append(el('p', 'Vyber balíky, ktoré fyzicky presúvaš. Presun sa zapíše naraz až po potvrdení.', 'muted'), source, target, list, error);
    const submit = button('Skontrolovať presun', async () => {
      if (!source.value || !target.value || source.value === target.value || !this.bulkSelected.size) { error.textContent = 'Vyber dve rôzne miesta a aspoň jeden balík.'; return; }
      const moving = this.activeParcels().filter(p => p.location_id === source.value && this.bulkSelected.has(p.number)).map(p => ({ number: p.number, version: p.version }));
      const from = source.value, to = target.value; this.bulkSource = from; this.bulkTarget = to;
      const yes = await this.ask('Potvrdiť fyzický presun', `${parcelCount(moving.length)}: ${this.locationName(from)} → ${this.locationName(to)}\n${moving.map(p => `${suffix(p.number)} · ${p.number}`).join('\n')}`, 'Balíky sú presunuté');
      if (!yes) return;
      this.busy = true; this.renderReceipt();
      try { await this.api('locations/move', { source_id: from, target_id: to, parcels: moving, operation_id: operationId() }); await this.refresh(); this.notice(`${parcelCount(moving.length)} presunutých na ${this.locationName(to)}.`, 'success'); }
      catch (err) { this.notice(`${err.message} Obnov evidenciu a skontroluj zmenené balíky.`, 'error'); await this.refresh().catch(() => {}); }
      finally { this.busy = false; this.renderReceipt(); }
    }, 'primary'); content.append(submit); this.showDialog('Presun obsahu miesta', content); draw();
  }

  async startInventory(locationId) { this.inventory = { location_id: locationId, numbers: new Set(), result: null }; await this.checkInventory(); this.feedback(`Inventúra ${this.locationName(locationId)}: skenuj balíky.`); }
  async checkInventory() { try { this.inventory.result = await this.api('inventory/check', { location_id: this.inventory.location_id, numbers: [...this.inventory.numbers] }); this.render(); } catch (error) { this.notice(error.message, 'error'); } }
  renderInventory(panel) {
    const result = this.inventory.result; if (!result) return;
    const summary = el('div', '', 'summary-strip'); summary.append(el('strong', `${result.matched.length} z ${result.expected.length} nájdených`), el('span', `${result.missing.length} chýba · ${result.unexpected.length} navyše`)); panel.append(summary);
    for (const [key, title] of [['missing', 'Ešte nenaskenované'], ['unexpected', 'Navyše alebo na inom mieste'], ['matched', 'Sedia s evidenciou']]) {
      const section = el('section', '', 'inventory-section'); section.append(el('h3', `${title} (${result[key].length})`));
      for (const parcel of result[key]) section.append(el('p', `${suffix(parcel.number)} · ${parcel.number}${key === 'unexpected' ? ` · evidencia: ${parcel.unknown ? 'neznámy balík' : this.locationName(parcel.location_id)}` : ''}`, 'mono'));
      panel.append(section);
    }
  }

  renderLocations(panel) {
    const heading = el('div', '', 'card panel-heading'); heading.append(el('h2', 'Regály, police a miesta'), el('p', 'Názvy môžeš meniť. Vytlačený čiarový kód zostáva platný aj po premenovaní.', 'muted'));
    const form = el('form', '', 'rack-form'); form.id = 'rack-form'; form.innerHTML = '<label for="rack-name">Názov regálu</label><input id="rack-name" required maxlength="60" placeholder="A"><div class="form-columns"><div><label for="shelf-count">Počet políc</label><input id="shelf-count" type="number" min="1" max="50" value="3" required></div><div><label for="place-count">Miest na polici</label><input id="place-count" type="number" min="0" max="50" value="0" required></div></div><p class="footnote">0 miest = celá polica je jedno miesto (A1, A2…). 3 miesta = A1-1, A1-2, A1-3…</p><button class="primary" type="submit">Vytvoriť regál</button><p class="error-text" id="rack-error" role="alert"></p>';
    form.addEventListener('submit', async event => { event.preventDefault(); const submit = form.querySelector('button'); submit.disabled = true; try { await this.api('locations/create', { rack_name: $('rack-name').value.trim(), shelf_count: Number($('shelf-count').value), places_per_shelf: Number($('place-count').value) }); await this.refresh(); this.notice('Miesta sú pripravené. Vyber štítky na tlač.', 'success'); } catch (error) { $('rack-error').textContent = error.message; submit.disabled = false; } });
    heading.append(form, button('Pridať samostatné miesto', () => this.createSpecial())); panel.append(heading);
    const tools = el('div', '', 'print-tools'); const format = el('select'); format.id = 'label-format'; format.setAttribute('aria-label', 'Formát štítkov'); format.append(new Option('A4 · 2 stĺpce', 'a4'), new Option('Štítok · 100 × 60 mm', 'label'));
    tools.append(format, button('Tlačiť vybrané', () => this.printLabels(false), 'primary'), button('Tlačiť všetky', () => this.printLabels(true))); panel.append(tools);
    if (!this.locations.length) panel.append(el('p', 'Vytvor prvý regál alebo samostatné miesto, napríklad Pult či Nadrozmerné.', 'empty-state'));
    if (this.locations.length) {
      const rackTools = el('div', '', 'inline-actions'); for (const rack of [...new Set(this.locations.map(p => p.rack).filter(Boolean))]) rackTools.append(button(`Premenovať regál ${rack}`, () => this.renameRack(rack))); panel.append(rackTools);
    }
    for (const place of this.locations) {
      const row = el('div', '', 'location-row card'), label = el('label', '', 'check-label'), check = el('input'); check.type = 'checkbox'; check.checked = this.printSelection.has(place.id); check.setAttribute('aria-label', `Tlačiť ${place.name}`); check.addEventListener('change', () => check.checked ? this.printSelection.add(place.id) : this.printSelection.delete(place.id));
      const details = el('div'); details.append(el('strong', place.name), el('span', `${place.count || 0} balíkov · ${place.rack || 'samostatné miesto'}`, 'muted'));
      label.append(check, details); row.append(label);
      const actions = el('div', '', 'inline-actions'); actions.append(button('Premenovať', () => this.renameLocation(place)), button('Obsah', () => { this.open('store').then(() => this.showLocation(place)); }), button('Zmazať', () => this.deleteLocation(place)));
      row.append(actions); panel.append(row);
    }
  }

  async createSpecial() {
    const content = el('form'); content.append(el('label', 'Názov miesta')); const name = el('input'); name.required = true; name.maxLength = 80; name.placeholder = 'Nadrozmerné'; name.id = 'special-name'; content.append(name);
    const error = el('p', '', 'error-text'), submit = el('button', 'Vytvoriť miesto', 'primary'); submit.type = 'submit'; content.append(error, submit);
    content.addEventListener('submit', async e => { e.preventDefault(); try { await this.api('locations/create', { rack_name: name.value.trim(), name: name.value.trim(), shelf_count: 1, places_per_shelf: 0, kind: 'custom' }); $('warehouse-dialog').close(); await this.refresh(); } catch (err) { error.textContent = err.message; } }); this.showDialog('Samostatné miesto', content);
  }

  renameLocation(place) {
    const form = el('form'); const input = el('input'); input.value = place.name; input.required = true; input.maxLength = 80; input.setAttribute('aria-label', 'Nový názov miesta'); const error = el('p', '', 'error-text'), submit = el('button', 'Uložiť názov', 'primary'); submit.type = 'submit';
    form.append(input, el('p', 'Pôvodný čiarový kód bude fungovať ďalej.', 'footnote'), error, submit);
    form.addEventListener('submit', async event => { event.preventDefault(); try { await this.api('locations/update', { id: place.id, name: input.value.trim(), expected_version: place.version }); $('warehouse-dialog').close(); await this.refresh(); } catch (err) { error.textContent = err.message; } }); this.showDialog('Premenovať miesto', form);
  }

  renameRack(rack) {
    const form = el('form'), input = el('input'), error = el('p', '', 'error-text'), submit = el('button', 'Premenovať regál', 'primary');
    input.value = rack; input.required = true; input.maxLength = 60; input.setAttribute('aria-label', 'Nový názov regálu'); submit.type = 'submit';
    form.append(input, el('p', 'Zmení sa názov regálu pri všetkých jeho miestach. Názvy jednotlivých miest a vytlačené kódy ostanú platné.', 'footnote'), error, submit);
    form.addEventListener('submit', async event => { event.preventDefault(); try { await this.api('racks/rename', { rack_name: rack, new_name: input.value.trim() }); $('warehouse-dialog').close(); await this.refresh(); } catch (err) { error.textContent = err.message; } }); this.showDialog(`Regál ${rack}`, form);
  }

  async deleteLocation(place) {
    if (place.count) { this.notice('Miesto obsahuje balíky. Najprv ich presuň na iné miesto.', 'error'); return; }
    if (!await this.ask('Zmazať prázdne miesto?', `${place.name}\nJeho vytlačený kód už nebude fungovať.`, 'Zmazať miesto')) return;
    try { await this.api('locations/delete', { id: place.id }); this.printSelection.delete(place.id); await this.refresh(); } catch (error) { this.notice(error.message, 'error'); }
  }

  printLabels(all) {
    const places = this.locations.filter(p => all || this.printSelection.has(p.id));
    if (!places.length) { this.notice('Vyber aspoň jedno miesto na tlač.', 'warning'); return; }
    let print = $('print-labels'); if (!print) { print = el('section'); print.id = 'print-labels'; document.body.append(print); }
    print.replaceChildren(); print.className = `print-labels print-${$('label-format').value}`;
    for (const place of places) {
      const label = el('article', '', 'location-label'); label.append(el('strong', place.name, `print-location-name${place.name.length > 25 ? ' long-name' : place.name.length > 12 ? ' medium-name' : ''}`));
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); JsBarcode(svg, place.barcode, { format: 'CODE128', width: 2, height: 72, margin: 20, displayValue: false, background: '#ffffff', lineColor: '#000000' });
      // JsBarcode emits inline CSS, which this app's CSP deliberately blocks.
      // SVG presentation attributes preserve the white quiet zone and black bars.
      svg.setAttribute('fill', '#000000');
      svg.firstElementChild.setAttribute('fill', '#ffffff');
      for (const group of svg.querySelectorAll('g')) group.setAttribute('fill', '#000000');
      svg.removeAttribute('style');
      for (const node of svg.querySelectorAll('[style]')) node.removeAttribute('style');
      label.append(svg, el('span', place.barcode, 'print-location-code')); print.append(label);
    }
    document.documentElement.classList.toggle('label-print-mode', $('label-format').value === 'label'); window.print();
  }

  renderSettings(panel) {
    const card = el('section', '', 'card panel-heading'); card.append(el('h2', 'Nastavenia výdajného miesta'), el('p', 'Tieto voľby platia pre všetky prihlásené zariadenia.', 'muted'));
    for (const [key, title, help] of [ ['courier_warning', 'Upozorniť, keď kuriér nie je prvý v SPS', 'Použije sa posledný úspešne použitý kuriér. Upozornenie príjem neblokuje.'], ['photos', 'Fotografie balíkov', 'Voliteľná fotka po načítaní. Fotku môžeš preskočiť alebo doplniť neskôr.'], ['return_staging', 'Príprava vratiek vopred', 'Pridá označenie pripravených vratiek. Priame odovzdanie kuriérovi zostáva dostupné.'] ]) {
      const label = el('label', '', 'settings-option'); const input = el('input'); input.type = 'checkbox'; input.checked = !!this.settings[key]; input.id = `setting-${key}`;
      const text = el('div'); text.append(el('strong', title), el('p', help, 'muted')); label.append(input, text);
      input.addEventListener('change', async () => { input.disabled = true; try { const response = await this.api('settings', { [key]: input.checked }); this.settings = response.settings; this.notice('Nastavenie uložené.', 'success'); this.renderReceipt(); } catch (error) { input.checked = !input.checked; this.notice(error.message, 'error'); } finally { input.disabled = false; } }); card.append(label);
    }
    card.append(el('p', 'Rozpracovaná prijímacia dávka sa ukladá v tomto prehliadači. Heslá ani zákaznícke PIN kódy sa do nej neukladajú.', 'footnote')); panel.append(card);
  }

  openPhoto(number) {
    this.photoGeneration = (this.photoGeneration || 0) + 1;
    this.photoNumber = number; this.photoData = null; this.photoBusy = true; $('photo-number').textContent = `${suffix(number)} · ${number}`; $('photo-preview').hidden = true; $('photo-error').textContent = ''; $('photo-save').disabled = true; $('photo-save').textContent = 'Uložiť fotku'; $('photo-frame').disabled = !$('video').videoWidth; $('photo-dialog').showModal();
  }
  async preparePhoto(source, width, height) {
    const generation = this.photoGeneration = (this.photoGeneration || 0) + 1;
    const current = () => generation === this.photoGeneration && $('photo-dialog').open;
    this.photoData = null; $('photo-save').disabled = true; $('photo-save').textContent = 'Pripravujem…'; $('photo-error').textContent = '';
    try {
      if (!(width > 0 && height > 0)) throw new Error('Fotografiu sa nepodarilo načítať.');
      const canvas = document.createElement('canvas'), captured = document.createElement('canvas');
      // HTMLImageElement.decode() honors EXIF orientation. Drawing its decoded
      // pixels also drops camera metadata before anything leaves the device.
      const captureScale = Math.min(1, PHOTO_MAX_EDGE / Math.max(width, height));
      captured.width = Math.max(1, Math.round(width * captureScale)); captured.height = Math.max(1, Math.round(height * captureScale));
      const captureContext = captured.getContext('2d'); captureContext.imageSmoothingQuality = 'high';
      captureContext.fillStyle = '#ffffff'; captureContext.fillRect(0, 0, captured.width, captured.height);
      captureContext.drawImage(source, 0, 0, captured.width, captured.height);
      // Freeze a camera frame once; slower encodes must not photograph a later
      // frame after the operator has already moved the phone to another parcel.
      let edge = Math.max(captured.width, captured.height);
      while (current()) {
        const scale = Math.min(1, edge / Math.max(captured.width, captured.height));
        canvas.width = Math.max(1, Math.round(captured.width * scale)); canvas.height = Math.max(1, Math.round(captured.height * scale));
        const context = canvas.getContext('2d'); context.imageSmoothingQuality = 'high';
        context.fillStyle = '#ffffff'; context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(captured, 0, 0, canvas.width, canvas.height);
        for (const quality of PHOTO_QUALITIES) {
          // toBlob yields while the browser encodes; the scanner stays paused.
          const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', quality));
          if (!current()) return;
          if (!blob || blob.type !== 'image/jpeg') throw new Error('Prehliadač nedokáže pripraviť fotografiu JPEG.');
          if (blob.size > PHOTO_MAX_BYTES) continue;
          const data = await new Promise((resolve, reject) => {
            const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error('Fotografiu sa nepodarilo načítať.')); reader.readAsDataURL(blob);
          });
          if (!current()) return;
          this.photoData = data; $('photo-preview').src = data; $('photo-preview').hidden = false; $('photo-save').disabled = false;
          return;
        }
        if (edge <= 128) throw new Error('Fotografiu sa nepodarilo zmenšiť. Skús ju odfotiť znova.');
        edge = Math.max(128, Math.floor(edge * .8));
      }
    } catch (error) { if (current()) $('photo-error').textContent = error.message; }
    finally { if (current()) $('photo-save').textContent = 'Uložiť fotku'; }
  }

  async history(number) {
    this.stopInput();
    try {
      const result = await this.api(`history?number=${encodeURIComponent(number)}`); const content = el('div');
      for (const event of result.events) {
        const row = el('div', '', 'history-row'); const names = { registered: 'Záznam balíka', move: 'Zmena miesta', bulk_move: 'Hromadný presun', undo: 'Vrátenie presunu', photo: 'Fotografia', staged: 'Vratka pripravená', unstaged: 'Zrušená príprava vratky', carrier_sync: 'Obnovené údaje zo SPS', carrier_accepted: 'Príjem potvrdený v SPS', carrier_accept_uncertain: 'Nejasný výsledok príjmu', carrier_delivered: 'Vydané zákazníkovi', carrier_returned: 'Odovzdané kuriérovi', carrier_delivery_uncertain: 'Nejasný výsledok výdaja', carrier_return_uncertain: 'Nejasný výsledok vrátenia', carrier_verified: 'Výsledok overený obsluhou' };
        row.append(el('strong', names[event.type] || 'Zmena evidencie'), el('p', `${this.formatTime(event.created_at)} · ${event.actor || 'Obsluha'}`, 'muted'));
        if (event.from_location_id || event.to_location_id) row.append(el('p', `${this.location(event.from_location_id)?.name || event.details?.from_location_name || 'Bez miesta'} → ${this.location(event.to_location_id)?.name || event.details?.to_location_name || 'Bez miesta'}`));
        const current = this.parcel(number);
        if (['move', 'bulk_move'].includes(event.type) && current?.version === event.version_after && !isTerminal(current)) row.append(button('Vrátiť tento presun', async () => { try { await this.api('moves/undo', { event_id: event.id, expected_version: current.version, operation_id: operationId() }); $('warehouse-dialog').close(); await this.refresh(); this.notice('Miesto bolo vrátené. Presuň späť aj fyzický balík.', 'success'); } catch (error) { this.notice(error.message, 'error'); } }));
        content.append(row);
      }
      if (!result.events.length) content.append(el('p', 'Zatiaľ bez histórie.', 'muted')); this.showDialog(`História · ${suffix(number)}`, content);
    } catch (error) { this.notice(error.message, 'error'); }
  }

  resolveOperation(parcel) {
    this.stopInput();
    const kind = parcel.pending_operation?.kind || ({ accept_uncertain: 'accept', delivery_uncertain: 'deliver', return_uncertain: 'return' })[parcel.carrier_status];
    if (!kind) return;
    const form = el('form'), link = el('a', 'Otvoriť portál SPS'); link.href = 'https://ps.sps-sro.sk'; link.target = '_blank'; link.rel = 'noopener noreferrer';
    const info = el('p', `Balík ${parcel.number}. Najprv skontroluj jeho výsledný stav v portáli SPS. Samotné zatvorenie okna, chyba siete alebo neprítomnosť v zozname výsledok nepotvrdzujú.`, 'confirmation-copy');
    const label = el('label', '', 'check-label'), checked = el('input'); checked.type = 'checkbox'; label.append(checked, el('span', 'Výsledok som overil priamo v SPS'));
    const result = el('select'); result.setAttribute('aria-label', 'Overený výsledok'); result.append(new Option('Vyber overený výsledok…', ''), new Option('Operácia v SPS prebehla úspešne', 'confirmed'), new Option('SPS operáciu nevykonalo', 'failed'));
    const note = el('input'); note.required = true; note.minLength = 5; note.maxLength = 500; note.placeholder = 'Čo si overil (bez PIN kódu či hesla)'; note.setAttribute('aria-label', 'Poznámka k overeniu');
    const error = el('p', '', 'error-text'), submit = el('button', 'Uložiť overený výsledok', 'primary full'); submit.type = 'submit'; submit.disabled = true;
    const valid = () => { submit.disabled = !checked.checked || !result.value || note.value.trim().length < 5; };
    checked.addEventListener('change', valid); result.addEventListener('change', valid); note.addEventListener('input', valid);
    form.append(info, link, label, result, note, error, submit);
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (submit.disabled) return; submit.disabled = true;
      try {
        await this.api('operations/resolve', { number: parcel.number, kind, outcome: result.value, note: note.value.trim(), verified_in_sps: true });
        const receipt = this.state.selected.get(parcel.number);
        if (kind === 'accept' && receipt) { receipt.state = result.value === 'confirmed' ? 'accepted' : 'pending'; receipt.message = 'Stav overený v SPS'; receipt.operationId = null; this.state.finished = receiptComplete(this.state.selected); }
        if (kind === 'return') this.returnScans.delete(parcel.number);
        if (kind === 'deliver') this.pickup = null;
        $('warehouse-dialog').close(); await this.refresh(); this.renderReceipt(); this.notice('Overený výsledok je uložený.', 'success');
      } catch (err) { error.textContent = err.message; valid(); }
    }); this.showDialog('Overenie nejasného výsledku', form);
  }

  renderPickup(panel) {
    const card = el('section', '', 'card panel-heading'); card.append(el('h2', 'Výdaj zákazníkovi'), el('p', 'Kód zákazníka vyhľadá balík. Výdaj potvrdíš až po načítaní celého kódu nájdeného balíka.', 'muted'));
    const form = el('form', '', 'manual-form'); form.id = 'pickup-form'; const input = el('input'); input.id = 'pickup-pin'; input.placeholder = 'Kód od zákazníka'; input.autocomplete = 'off'; input.maxLength = 100; input.required = true; input.setAttribute('aria-label', 'Kód od zákazníka'); const submit = el('button', 'Nájsť', 'primary'); submit.type = 'submit'; form.append(input, submit);
    form.addEventListener('submit', async event => {
      event.preventDefault(); submit.disabled = true; this.stopInput(); this.pickup = null; this.lastPickupReceipt = null; panel.replaceChildren(); this.setNav();
      try { const result = await this.api('pickup/lookup', { pin: input.value.trim() }); input.value = ''; this.pickup = { ...result, verified: false, codConfirmed: false, paidWithTerminal: false }; await this.refresh(); this.feedback('Vyber balík z uvedeného miesta a naskenuj jeho celý kód.'); }
      catch (error) { this.notice(error.message, 'error'); submit.disabled = false; }
    }); card.append(form); $('workspace-leading').append(card);
    if (this.lastPickupReceipt) {
      const receipt = el('section', '', 'card panel-heading'); receipt.append(el('h3', `Výdaj ${this.lastPickupReceipt.number} je potvrdený`));
      if (this.lastPickupReceipt.receipt_url) { const link = el('a', 'Otvoriť / vytlačiť doklad', 'primary'); link.href = this.lastPickupReceipt.receipt_url; link.target = '_blank'; link.rel = 'noopener'; const row = el('div', '', 'receipt-result-actions'); row.append(link); receipt.append(row); }
      if (this.lastPickupReceipt.receipt_pending) receipt.append(el('p', 'Doklad sa nepodarilo načítať. Výdaj je už potvrdený, neopakuj ho.', 'warning-note'), button('Načítať doklad znova', async () => { try { const result = await this.api('pickup/receipt', { number: this.lastPickupReceipt.number }); Object.assign(this.lastPickupReceipt, result, { receipt_pending: false }); this.render(); } catch (error) { this.notice(error.message, 'error'); } }));
      panel.append(receipt);
    }
    if (!this.pickup) { if (this.state.demo) panel.append(el('p', 'Demo: PIN 111111 nájde DEMOSTORED01. Po príjme DEMO00000001 funguje PIN 000001.', 'footnote')); return; }
    const remote = this.pickup.parcel, local = this.parcel(remote.trackNumber) || this.pickup.local || { number: remote.trackNumber, sender_name: remote.senderName || '', carrier_status: 'accepted', location_id: null };
    if (local.pending_operation?.kind === 'deliver' || local.carrier_status === 'delivery_uncertain') this.pickup.uncertain = true;
    panel.append(this.parcelCard(local, { detailed: true }));
    if (remote.recipientName) panel.append(el('p', remote.recipientName, 'recipient-name'));
    if (remote.psRemark) panel.append(el('p', remote.psRemark, 'warning-note'));
    const cod = Number(remote.cod || 0);
    const confirm = el('div', '', 'card pickup-confirm'); confirm.append(el('p', this.pickup.verified ? `✓ Správny balík ${suffix(remote.trackNumber)} je overený.` : 'Naskenuj celý čiarový kód tohto balíka.', this.pickup.verified ? 'verified-note' : 'step-prompt'));
    if (cod > 0) {
      confirm.append(el('strong', `Dobierka: ${cod.toFixed(2)} €`, 'cod-amount'));
      const label = el('label', '', 'check-label'), check = el('input'); check.type = 'checkbox'; check.id = 'pickup-cod-confirmed'; check.checked = this.pickup.codConfirmed;
      label.append(check, el('span', 'Dobierka bola uhradená')); const payment = el('select'); payment.id = 'pickup-payment'; payment.setAttribute('aria-label', 'Spôsob úhrady dobierky'); payment.append(new Option('Hotovosť', 'cash')); if (remote.paymentTerminalAllowed) payment.append(new Option('Uhradené na platobnom termináli', 'terminal')); payment.value = this.pickup.paidWithTerminal && remote.paymentTerminalAllowed ? 'terminal' : 'cash';
      check.addEventListener('change', () => { this.pickup.codConfirmed = check.checked; this.render(); }); payment.addEventListener('change', () => { this.pickup.paidWithTerminal = payment.value === 'terminal'; }); confirm.append(label, payment);
      if (remote.paymentTerminalAllowed) confirm.append(el('p', 'Voľba terminálu platbu nespustí. Potvrď ju až po úspešnej úhrade.', 'footnote'));
    }
    if (this.pickup.uncertain) confirm.append(button('Overiť stav v evidencii', async () => {
      try { await this.refresh(); if (this.pickup?.uncertain) this.notice('Výsledok stále čaká na overenie. Skontroluj operáciu v portáli SPS.', 'warning'); }
      catch (error) { this.notice(`Evidenciu sa nepodarilo načítať. Výdaj zatiaľ neopakuj. ${error.message}`, 'error'); }
    }));
    const release = button(this.pickup.uncertain ? 'Výsledok treba overiť v SPS' : this.pickup.controlledRetry ? 'Zopakovať kontrolovaný pokus' : 'Potvrdiť odovzdanie zákazníkovi', () => this.confirmPickup(), 'primary full'); release.id = 'pickup-confirm'; release.disabled = !this.pickup.verified || (cod > 0 && !this.pickup.codConfirmed) || !!this.pickup.uncertain || this.busy;
    confirm.append(release); panel.append(confirm);
  }

  verifyPickup(code) {
    if (!this.pickup || this.pickup.uncertain) { this.feedback(this.pickup?.uncertain ? 'Najprv over výsledok predchádzajúceho výdaja.' : 'Najprv vyhľadaj balík kódom zákazníka.', 'warning'); return; }
    if (normalizeCode(this.pickup.parcel.trackNumber) !== code) { this.pickup.verified = false; this.render(); this.beep('unknown'); this.feedback('Toto nie je balík pre vyhľadaného zákazníka. Výdaj sa nepotvrdil.', 'error'); return; }
    this.pickup.verified = true; this.beep('match'); this.feedback('✓ Správny balík. Teraz potvrď fyzické odovzdanie zákazníkovi.', 'success'); this.render();
  }

  async confirmPickup() {
    if (!this.pickup?.verified || this.pickup.uncertain || this.busy) return;
    this.stopInput(); this.busy = true; this.render(); this.renderReceipt();
    let confirmed = null;
    try {
      const result = await this.api('pickup/confirm', { lookup_id: this.pickup.lookup_id, scanned_number: this.pickup.parcel.trackNumber, cod_confirmed: this.pickup.codConfirmed && !this.pickup.paidWithTerminal, paid_with_terminal: this.pickup.codConfirmed && this.pickup.paidWithTerminal, operation_id: this.pickup.operationId ||= operationId() });
      if (result.delivered !== true) { const error = new Error('Server nepotvrdil výsledok výdaja. Najprv ho over v SPS.'); error.outcome = 'uncertain'; throw error; }
      confirmed = result; this.pickup = null; this.lastPickupReceipt = result; await this.refresh(); this.feedback(`✓ ${result.number}: výdaj potvrdený v SPS.`, 'success'); this.notice(result.local_saved === false ? 'SPS výdaj potvrdilo. Lokálnu evidenciu treba obnoviť; výdaj neopakuj.' : 'Balík je vydaný zákazníkovi. Miesto bolo uvoľnené.', 'success');
    } catch (error) {
      if (confirmed) this.notice(`SPS výdaj ${confirmed.number} potvrdilo. Evidenciu sa nepodarilo obnoviť; výdaj neopakuj. ${error.message}`, 'warning');
      else {
        if (this.pickup && (error.outcome === 'uncertain' || error.name === 'TypeError')) {
          this.pickup.uncertain = true;
          try { await this.refresh(); if (this.pickup?.uncertain) this.notice('Výsledok výdaja nie je známy. Najprv ho over v SPS.', 'warning'); }
          catch { this.notice('Spojenie sa prerušilo a stav sa nepodarilo overiť. Výdaj zatiaľ neopakuj; obnov evidenciu po návrate spojenia.', 'error'); }
        } else {
          if (this.pickup) { this.pickup.operationId = null; this.pickup.controlledRetry = false; }
          this.notice(error.message, 'error');
        }
      }
    }
    finally { this.busy = false; this.render(); this.renderReceipt(); }
  }

  async loadReturns() {
    const result = await this.api('returns'); this.returns = result.parcels || []; this.returnsSyncedAt = result.synced_at;
    const known = new Set(this.returns.map(p => normalizeCode(p.trackNumber))); for (const code of this.returnScans) if (!known.has(code)) this.returnScans.delete(code);
    await this.refresh();
  }

  async scanReturn(code) {
    if (this.returnBusy) return;
    const remote = this.returns.find(p => normalizeCode(p.trackNumber) === code);
    if (!remote) { this.beep('unknown'); this.feedback(`${code}: nie je v aktuálnom zozname vratiek SPS.`, 'error'); return; }
    if (!remote.returnEligible) { this.beep('unknown'); this.feedback('SPS nedovoľuje túto zásielku vrátiť. Skontroluj jej stav v portáli.', 'error'); return; }
    if (this.parcel(code)?.pending_operation) { this.feedback('Pred ďalším odovzdaním over výsledok predošlej operácie v SPS.', 'warning'); return; }
    if (this.returnScans.has(code)) { this.beep('duplicate'); this.feedback(`${suffix(code)} už je vybraný na odovzdanie.`, 'warning'); return; }
    this.returnScans.add(code); this.beep('match'); this.feedback(`✓ ${suffix(code)} · pripravený na odovzdanie kuriérovi.`, 'success');
    await this.loadReturnCouriers(); this.render();
  }

  async loadReturnCouriers() {
    if (!this.returnScans.size) { this.returnCouriers = []; return; }
    this.returnCouriers = [];
    try { const result = await this.api('returns/couriers', { numbers: [...this.returnScans] }); this.returnCouriers = result.couriers; this.returnCourierId = chooseCourier(result, this.returnCourierId); this.returnCourierResponse = result; }
    catch (error) { this.notice(error.message, 'error'); }
  }

  renderReturns(panel) {
    const intro = el('section', '', 'card panel-heading'); intro.append(el('h2', 'Vratky kuriérovi'), el('p', this.settings.return_staging ? 'Balíky môžeš označiť ako pripravené vopred alebo ich priamo odovzdať kuriérovi.' : 'Keď príde kuriér, naskenuj vratky z políc a potvrď odovzdanie.', 'muted'), button('Obnoviť vratky zo SPS', async () => { try { await this.loadReturns(); await this.refresh(); } catch (error) { this.notice(error.message, 'error'); } }));
    if (this.returnsSyncedAt) intro.append(el('p', `Overené v SPS: ${this.formatTime(this.returnsSyncedAt)}`, 'footnote')); panel.append(intro);
    if (!this.returns.length) panel.append(el('p', 'SPS momentálne nevrátilo žiadne expirované zásielky.', 'empty-state'));
    const rows = [...this.returns].sort((a, b) => this.locationName((a.local || this.parcel(a.trackNumber))?.location_id).localeCompare(this.locationName((b.local || this.parcel(b.trackNumber))?.location_id), 'sk', { numeric: true }));
    for (const remote of rows) {
      const code = normalizeCode(remote.trackNumber), local = this.parcel(code) || remote.local || { number: code, sender_name: remote.senderName, carrier_status: 'accepted', location_id: null };
      const card = this.parcelCard(local, { detailed: false }); card.classList.toggle('return-scanned', this.returnScans.has(code));
      card.append(el('p', remote.returnEligible ? this.returnScans.has(code) ? '✓ Naskenovaný na odovzdanie' : local.staged ? 'Pripravený vopred · pred odovzdaním naskenuj' : 'Nájdi a naskenuj celý kód' : 'SPS blokuje vrátenie tejto zásielky', remote.returnEligible ? 'muted' : 'warning-note'));
      if (this.returnScans.has(code)) card.append(button('Odobrať z odovzdania', () => { this.returnScans.delete(code); this.render(); })); panel.append(card);
    }
    if (!this.returnScans.size) return;
    const controls = el('section', '', 'card return-controls'); controls.append(el('h3', `${parcelCount(this.returnScans.size)} na odovzdanie`));
    if (this.settings.return_staging) controls.append(button('Označiť ako pripravené vopred', async () => { try { await this.api('returns/stage', { parcels: [...this.returnScans].map(number => ({ number, version: this.parcel(number).version })), staged: true }); this.returnScans.clear(); await this.refresh(); this.notice('Balíky sú pripravené. Pri príchode kuriéra ich naskenuj na odovzdanie; SPS zatiaľ nič nepotvrdilo.', 'success'); } catch (error) { this.notice(error.message, 'error'); } }));
    const label = el('label', 'Kuriér, ktorému balíky odovzdávaš'); label.htmlFor = 'return-courier'; const select = el('select'); select.id = 'return-courier'; select.append(new Option('Vyber kuriéra', '')); for (const courier of this.returnCouriers) select.append(new Option(`${courier.code} · ${courier.name}`, String(courier.id))); select.value = this.returnCourierId;
    select.addEventListener('change', () => { this.returnCourierId = select.value; this.render(); }); controls.append(label, select);
    const selected = this.returnCouriers.find(c => String(c.id) === this.returnCourierId);
    if (this.settings.courier_warning && selected && String(this.returnCourierResponse?.first_id) !== this.returnCourierId) controls.append(el('p', '⚠ Tento kuriér nie je prvý v zozname SPS.', 'warning-note'));
    const passwordLabel = el('label', selected?.has_password ? 'Uložené heslo sa použije automaticky; prípadne zadaj nové' : 'Heslo kuriéra'); passwordLabel.htmlFor = 'return-password'; const password = el('input'); password.type = 'password'; password.id = 'return-password'; password.autocomplete = 'off'; password.maxLength = 1024; password.placeholder = selected?.has_password ? 'Uložené heslo' : 'Heslo kuriéra'; controls.append(passwordLabel, password);
    if (this.state.demo) controls.append(el('p', 'Demo heslo: demo', 'footnote'));
    const blocked = [...this.returnScans].some(number => this.parcel(number)?.pending_operation);
    const submit = button(this.returnBusy ? 'Potvrdzujem odovzdanie…' : blocked ? 'Najprv over nejasný výsledok v SPS' : 'Potvrdiť odovzdanie kuriérovi', () => this.confirmReturns(), 'primary full'); submit.id = 'return-confirm'; submit.disabled = !selected || this.returnBusy || blocked; controls.append(submit); panel.append(controls);
  }

  async confirmReturns() {
    if (this.returnBusy || !this.returnScans.size || !this.returnCourierId) return;
    const courier = this.returnCouriers.find(c => String(c.id) === this.returnCourierId); let password = $('return-password').value;
    if (!password && !courier?.has_password) { this.notice('Zadaj heslo vybraného kuriéra.', 'error'); return; }
    const count = this.returnScans.size, courierId = this.returnCourierId;
    if (!await this.ask('Potvrdiť odovzdanie kuriérovi', `${parcelCount(count)} · ${courier.code} · ${courier.name}\nPotvrdíš balíky, ktoré fyzicky preberá kuriér.`, 'Kuriér prevzal balíky')) { password = null; return; }
    this.stopInput(); this.returnBusy = true; this.render(); this.renderReceipt(); let completed = 0;
    try {
      for (const number of [...this.returnScans]) {
        try {
          const result = await this.api('returns/confirm', { number, courier_id: courierId, ...(password ? { password } : {}), operation_id: operationId() });
          if (result.returned !== true) { const error = new Error('Server nepotvrdil výsledok vrátenia. Najprv ho over v SPS.'); error.outcome = 'uncertain'; throw error; }
          this.returnScans.delete(number); completed++;
        } catch (error) { this.notice(`${completed} z ${count} potvrdených. ${error.message}${error.outcome === 'uncertain' ? ' Výsledok najprv over v SPS.' : ''}`, 'error'); break; }
      }
      if (completed === count) this.notice(`✓ ${parcelCount(completed)} odovzdaných kuriérovi.`, 'success');
      await this.refresh(); await this.loadReturns();
    } catch (error) { this.notice(`${completed} z ${count} odovzdaní je potvrdených v SPS. Evidenciu sa nepodarilo obnoviť; úspešné odovzdania neopakuj. ${error.message}`, 'warning'); }
    finally { password = null; this.returnBusy = false; this.render(); this.renderReceipt(); }
  }

  formatTime(value) { const date = new Date(typeof value === 'number' ? value * (value < 1e12 ? 1000 : 1) : value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('sk-SK'); }
  showDialog(title, content) {
    const dialog = $('warehouse-dialog'); if (dialog.open) dialog.close(); dialog.replaceChildren(el('h2', title), content, button('Zavrieť', () => dialog.close(), 'secondary full')); dialog.showModal();
  }
  ask(title, message, confirmLabel) {
    const dialog = $('confirm-action-dialog');
    if (dialog.open) return Promise.resolve(false);
    if ($('warehouse-dialog').open) $('warehouse-dialog').close();
    return new Promise(resolve => {
      const copy = el('p', message, 'confirmation-copy'), row = el('div', '', 'dialog-actions');
      let answer = false; row.append(button('Späť', () => dialog.close(), 'secondary'), button(confirmLabel, () => { answer = true; dialog.close(); }, 'primary'));
      dialog.replaceChildren(el('h2', title), copy, row); dialog.addEventListener('close', () => resolve(answer), { once: true }); dialog.showModal();
    });
  }
}
