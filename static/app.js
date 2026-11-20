'use strict';

function defaultTimes(now = new Date()) {
  const start = new Date(now);
  start.setUTCMinutes(0, 0, 0);
  start.setUTCHours(start.getUTCHours() + 1);
  const end = new Date(start.getTime() + 3600000);
  return { start: start.toISOString().slice(0, 16), end: end.toISOString().slice(0, 16) };
}

function reservationPayload(values) {
  const member_id = Number(values.member_id);
  const equipment_id = Number(values.equipment_id);
  if (!Number.isInteger(member_id) || member_id < 1) throw new Error('Choose a member.');
  if (!Number.isInteger(equipment_id) || equipment_id < 1) throw new Error('Choose available equipment.');
  const parseUTC = value => {
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) throw new Error('Enter a valid UTC date and time.');
    const result = value + ':00Z';
    const date = new Date(result);
    if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 19) + 'Z' !== result) throw new Error('Enter a valid UTC date and time.');
    return result;
  };
  const start_at = parseUTC(values.start_at);
  const end_at = parseUTC(values.end_at);
  if (end_at <= start_at) throw new Error('End time must be after start time.');
  const purpose = String(values.purpose || '').trim();
  if (!purpose) throw new Error('Enter a purpose for this reservation.');
  return { member_id, equipment_id, start_at, end_at, purpose };
}

async function apiRequest(path, options = {}, fetcher = globalThis.fetch) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  const headers = { Accept: 'application/json', ...options.headers };
  const request = { ...options, headers, signal: controller.signal, cache: 'no-store' };
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    request.body = JSON.stringify(options.body);
  }
  try {
    let response;
    try { response = await fetcher(path, request); }
    catch (error) { throw new Error(error.name === 'AbortError' ? 'The request timed out. Check the local server and retry.' : 'Cannot connect to the local server. Start CampusGear and retry.'); }
    let data;
    try { data = await response.json(); }
    catch { throw new Error('The server did not return valid JSON. Check the local server and retry.'); }
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
    return data;
  } finally { clearTimeout(timer); }
}

function createWorkspace(doc, request = apiRequest) {
  const $ = id => doc.getElementById(id);
  const state = { equipment: [], members: [], reservations: [], selected: null, view: 'equipment', busy: false, searchSerial: 0 };
  function el(tag, text, className) {
    const node = doc.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function message(id, text, kind = 'error') {
    const node = $(id);
    node.textContent = text;
    node.className = id === 'notice' ? `notice ${kind}` : `form-feedback ${kind}`;
    node.hidden = !text;
  }
  function empty(body, columns, text) {
    const row = el('tr');
    const cell = el('td', text, 'empty');
    cell.setAttribute('colspan', columns);
    row.append(cell);
    body.replaceChildren(row);
  }
  function badge(status) { return el('span', String(status).replaceAll('_', ' '), `badge ${['available', 'maintenance', 'retired', 'confirmed', 'checked_out', 'returned', 'cancelled'].includes(status) ? status : 'unknown'}`); }
  function selectEquipment(item) {
    state.selected = item;
    $('booking-equipment').value = String(item.id);
    $('selected-equipment').textContent = `${item.name} · ${item.asset_tag}`;
    $('selected-location').textContent = `${item.category_name} / ${item.location}`;
    $('booking-submit').disabled = state.busy || !state.members.length;
    message('booking-feedback', '');
    renderEquipment();
    $('booking-member').focus();
  }
  function renderEquipment() {
    const body = $('equipment-body');
    body.replaceChildren();
    $('equipment-count').textContent = state.equipment.length;
    if (!state.equipment.length) return empty(body, 4, 'No equipment matches. Try a different search or category.');
    for (const item of state.equipment) {
      const row = el('tr');
      if (state.selected && state.selected.id === item.id) row.className = 'selected-row';
      const name = el('td');
      name.append(el('strong', item.name, 'asset-name'), el('span', `${item.asset_tag} · ${item.category_name}`, 'table-secondary'));
      const status = el('td'); status.append(badge(item.status));
      const action = el('td');
      const selected = state.selected && state.selected.id === item.id;
      const button = el('button', selected ? 'Selected ✓' : 'Reserve', selected ? 'button selected-button small' : 'button outline small');
      button.type = 'button';
      button.disabled = item.status !== 'available' || state.busy;
      button.setAttribute('aria-label', `${selected ? 'Selected' : 'Reserve'} ${item.name}`);
      button.setAttribute('aria-pressed', String(Boolean(selected)));
      button.addEventListener('click', () => selectEquipment(item));
      action.append(button);
      row.append(name, el('td', item.location, 'location-cell'), status, action);
      body.append(row);
    }
  }
  function renderMembers() {
    const select = $('booking-member');
    const previous = select.value;
    select.replaceChildren();
    for (const member of state.members) {
      const option = el('option', `${member.name} · ${member.department_name}`); option.value = String(member.id); select.append(option);
    }
    select.value = state.members.some(member => String(member.id) === previous) ? previous : String((state.members.find(member => member.id === 1) || state.members[0] || {}).id || '');
    $('booking-submit').disabled = state.busy || !state.selected || !state.members.length;
    const member = state.members.find(item => String(item.id) === select.value);
    $('member-detail').textContent = member ? `${member.email} · Synthetic demo identity` : 'No demo members are available.';
  }
  async function loadEquipment() {
    const serial = ++state.searchSerial;
    const params = new URLSearchParams();
    if ($('equipment-search').value.trim()) params.set('q', $('equipment-search').value.trim());
    if ($('category-filter').value) params.set('category_id', $('category-filter').value);
    const result = await request('/api/equipment' + (params.size ? `?${params}` : ''));
    if (serial !== state.searchSerial) return;
    state.equipment = result.items;
    const selected = state.selected && state.equipment.find(item => item.id === state.selected.id);
    if (selected && selected.status !== 'available') {
      state.selected = null; $('booking-equipment').value = ''; $('selected-equipment').textContent = 'Choose equipment from the catalogue'; $('selected-location').textContent = 'Only available assets can be reserved.'; $('booking-submit').disabled = true;
      message('booking-feedback', 'Your selected asset is no longer available. Choose another asset.');
    }
    renderEquipment();
  }
  async function loadCore() {
    const results = await Promise.allSettled([
      request('/api/dashboard').then(data => {
        for (const [id, key] of [['metric-equipment', 'equipment_count'], ['metric-reservations', 'active_reservations'], ['metric-loans', 'open_loans'], ['metric-members', 'total_members']]) $(id).textContent = data[key];
      }),
      loadEquipment(),
      request('/api/members').then(data => { state.members = data.items; renderMembers(); }),
      request('/api/categories').then(data => {
        const select = $('category-filter'); const previous = select.value;
        const all = el('option', 'All categories'); all.value = ''; select.replaceChildren(all);
        for (const category of data.items) { const option = el('option', category.name); option.value = String(category.id); select.append(option); }
        select.value = previous;
      })
    ]);
    const failures = results.filter(result => result.status === 'rejected');
    $('connection-status').textContent = failures.length ? 'Connection issue' : 'Database connected';
    $('connection-status').className = failures.length ? 'connection-status offline' : 'connection-status online';
    if (failures.length) throw new Error(failures.map(result => result.reason.message).join(' '));
    $('last-updated').textContent = `Last synced ${new Date().toISOString().slice(11, 19)} UTC`;
  }
  function utcText(value) { return String(value).replace('T', ' ').replace('Z', ' UTC'); }
  function renderReservations() {
    const body = $('reservations-body'); body.replaceChildren();
    const filter = $('reservation-filter').value || 'all';
    const items = state.reservations.filter(item => filter === 'all' || item.status === filter);
    $('reservation-count').textContent = `${items.length} / ${state.reservations.length}`;
    if (!items.length) return empty(body, 5, 'No reservations in this view. Choose equipment to create one.');
    for (const item of items) {
      const row = el('tr');
      const asset = el('td'); asset.append(el('strong', item.equipment_name, 'asset-name'), el('span', `#${item.id} · ${item.asset_tag}`, 'table-secondary'));
      const member = el('td'); member.append(el('strong', item.member_name), el('span', item.purpose, 'table-secondary purpose-text'));
      const times = el('td', undefined, 'time-cell'); times.append(el('span', utcText(item.start_at)), el('span', `→ ${utcText(item.end_at)}`, 'table-secondary'));
      const status = el('td'); status.append(badge(item.status));
      const actions = el('td', undefined, 'action-cell');
      for (const action of item.status === 'confirmed' ? ['checkout', 'cancel'] : item.status === 'checked_out' ? ['return'] : []) {
        const button = el('button', { checkout: 'Check out', cancel: 'Cancel', return: 'Return' }[action], `button small ${action === 'cancel' ? 'danger-quiet' : 'outline'}`);
        button.type = 'button'; button.disabled = state.busy;
        button.setAttribute('aria-label', `${button.textContent} reservation #${item.id}`);
        button.addEventListener('click', () => mutateReservation(item.id, action)); actions.append(button);
      }
      if (!actions.children.length) actions.textContent = '—';
      row.append(asset, member, times, status, actions);
      body.append(row);
    }
  }
  async function loadReservations() {
    const result = await request('/api/reservations');
    state.reservations = result.items; renderReservations();
  }
  async function submitBooking(event) {
    event.preventDefault();
    if (state.busy) return;
    let payload;
    try { payload = reservationPayload({ member_id: $('booking-member').value, equipment_id: $('booking-equipment').value, start_at: $('booking-start').value, end_at: $('booking-end').value, purpose: $('booking-purpose').value }); }
    catch (error) { message('booking-feedback', error.message); return; }
    state.busy = true; $('booking-submit').disabled = true; $('booking-submit').textContent = 'Saving reservation…'; renderEquipment();
    message('booking-feedback', '');
    let saved = false;
    try {
      const result = await request('/api/reservations', { method: 'POST', body: payload }); saved = true;
      await loadReservations();
      const stored = state.reservations.find(item => item.id === result.id);
      if (!stored || stored.status !== 'confirmed') throw new Error('Saved booking could not be verified in the reservation register.');
      message('booking-feedback', `Reservation #${result.id} confirmed. View Reservations to check out or cancel.`, 'success');
      try { await loadCore(); } catch (error) { message('notice', `Reservation saved; some data could not refresh. ${error.message}`); }
    } catch (error) { message('booking-feedback', saved ? `The server accepted this booking, but readback failed. Refresh before retrying to avoid a duplicate. ${error.message}` : error.message); }
    finally { state.busy = false; $('booking-submit').disabled = !state.selected || !state.members.length; $('booking-submit').textContent = 'Confirm reservation →'; renderEquipment(); }
  }
  async function mutateReservation(id, action) {
    if (state.busy) return;
    state.busy = true; renderReservations(); $('booking-submit').disabled = true;
    message('notice', `Updating reservation #${id}…`, 'info');
    let saved = false;
    try {
      const result = await request(`/api/reservations/${id}/${action}`, { method: 'POST', body: {} }); saved = true;
      await loadReservations();
      const stored = state.reservations.find(item => item.id === id);
      if (!stored || stored.status !== result.status) throw new Error('The updated state could not be verified. Refresh before retrying.');
      message('notice', `Reservation #${id} is now ${result.status.replaceAll('_', ' ')}.`, 'success');
      try { await loadCore(); } catch (error) { message('notice', `Action saved; summary refresh failed. ${error.message}`); }
    } catch (error) { message('notice', saved ? `The server accepted the action, but readback failed. Refresh before retrying. ${error.message}` : error.message); }
    finally { state.busy = false; renderReservations(); renderEquipment(); $('booking-submit').disabled = !state.selected || !state.members.length; }
  }
  function renderBars(id, items, labelKey) {
    const container = $(id); container.replaceChildren();
    if (!items.length) { container.append(el('p', 'No reservation usage yet.', 'empty')); return; }
    const maximum = Math.max(...items.map(item => Number(item.reservations)), 1);
    for (const item of items) {
      const row = el('div', undefined, 'bar-row'); const heading = el('div', undefined, 'bar-label');
      heading.append(el('span', item[labelKey]), el('strong', item.reservations));
      const track = el('div', undefined, 'bar-track'); const fill = el('div', undefined, 'bar-fill');
      fill.style.setProperty('width', `${Math.max(0, Math.min(100, Number(item.reservations) / maximum * 100))}%`);
      track.setAttribute('aria-hidden', 'true'); track.append(fill); row.append(heading, track); container.append(row);
    }
  }
  async function loadAnalytics() {
    const data = await request('/api/analytics');
    renderBars('category-usage', data.category_usage, 'category'); renderBars('top-equipment', data.top_equipment, 'equipment');
    $('overdue-count').textContent = data.overdue.length;
    const body = $('overdue-body'); body.replaceChildren();
    if (!data.overdue.length) return empty(body, 4, 'No overdue open loans. All checked-out equipment is within its return window.');
    for (const item of data.overdue) {
      const row = el('tr'); row.append(el('td', `#${item.reservation_id}`), el('td', item.equipment), el('td', item.member), el('td', utcText(item.end_at), 'time-cell overdue-time')); body.append(row);
    }
  }
  async function loadDatabase() {
    const results = await Promise.allSettled([
      request('/api/schema').then(data => {
        const container = $('schema-objects'); container.replaceChildren();
        $('schema-count').textContent = `${data.tables.length} tables · ${data.indexes.length} indexes`;
        for (const [kind, items] of [['TABLE', data.tables], ['INDEX', data.indexes]]) {
          for (const item of items) {
            const details = el('details', undefined, 'schema-object'); const summary = el('summary');
            summary.append(el('span', kind, 'schema-kind'), el('strong', item.name));
            const pre = el('pre'); pre.append(el('code', item.sql || 'Implicit SQLite index; no SQL definition.'));
            details.append(summary, pre); container.append(details);
          }
        }
        if (!container.children.length) container.append(el('p', 'No schema objects returned.', 'empty'));
      }),
      request('/api/audit').then(data => {
        const body = $('audit-body'); body.replaceChildren();
        if (!data.items.length) return empty(body, 3, 'No audit events yet. Reserve equipment to create the first event.');
        for (const item of data.items) {
          const row = el('tr'); const event = el('td'); event.append(el('strong', String(item.action).replaceAll('_', ' ')), el('span', `Event ${item.id}`, 'table-secondary'));
          row.append(event, el('td', `#${item.reservation_id}`), el('td', utcText(item.created_at), 'time-cell')); body.append(row);
        }
      })
    ]);
    const failed = results.filter(result => result.status === 'rejected');
    if (failed.length) throw new Error(failed.map(result => result.reason.message).join(' '));
  }
  async function openView(name) {
    const views = { equipment: ['Equipment workspace', 'Find the right equipment. Reserve a time. Keep every handover on record.'], reservations: ['Reservations & handovers', 'Manage bookings from confirmation to checkout and return.'], analytics: ['Usage analytics', 'Read demand and overdue loans from live database queries.'], database: ['Database inspector', 'Inspect the SQLite schema and the reservation audit trail.'] };
    if (!views[name]) name = 'equipment';
    state.view = name;
    for (const key of Object.keys(views)) {
      $(`view-${key}`).hidden = key !== name;
      $(`nav-${key}`).classList.toggle('active', key === name);
      $(`nav-${key}`).setAttribute('aria-current', key === name ? 'page' : 'false');
    }
    $('page-title').textContent = views[name][0]; $('page-description').textContent = views[name][1]; $('breadcrumb').textContent = name.toUpperCase();
    doc.title = `CampusGear · ${views[name][0]}`;
    try {
      if (name === 'reservations') await loadReservations();
      if (name === 'analytics') await loadAnalytics();
      if (name === 'database') await loadDatabase();
    }
    catch (error) { message('notice', error.message); }
  }
  async function start() {
    const times = defaultTimes();
    $('booking-start').value = times.start; $('booking-end').value = times.end;
    $('booking-member').addEventListener('change', renderMembers);
    $('booking-form').addEventListener('submit', submitBooking);
    $('reservation-filter').addEventListener('change', renderReservations);
    async function search(event) {
      if (event) event.preventDefault();
      $('search-button').disabled = true; $('search-button').textContent = 'Searching…';
      try { await loadEquipment(); } catch (error) { message('notice', error.message); }
      finally { $('search-button').disabled = false; $('search-button').textContent = 'Search'; }
    }
    $('search-form').addEventListener('submit', search);
    $('category-filter').addEventListener('change', search);
    $('refresh-button').addEventListener('click', async () => {
      $('refresh-button').disabled = true; $('refresh-button').textContent = 'Refreshing…'; message('notice', '');
      try { await loadCore(); await openView(state.view); } catch (error) { message('notice', error.message); }
      finally { $('refresh-button').disabled = false; $('refresh-button').textContent = '↻ Refresh data'; }
    });
    for (const name of ['equipment', 'reservations', 'analytics', 'database']) $(`nav-${name}`).addEventListener('click', event => { event.preventDefault(); return openView(name); });
    try { await loadCore(); } catch (error) {
      message('notice', error.message);
      if (!state.equipment.length) empty($('equipment-body'), 4, 'Equipment could not be loaded. Start the local server, then choose Refresh data.');
      if (!state.members.length) { const option = el('option', 'Members unavailable — refresh to retry'); option.value = ''; $('booking-member').replaceChildren(option); }
    }
    await openView('equipment');
  }
  return { start };
}

if (typeof module !== 'undefined') module.exports = { defaultTimes, reservationPayload, apiRequest, createWorkspace };
if (typeof document !== 'undefined') createWorkspace(document).start();
