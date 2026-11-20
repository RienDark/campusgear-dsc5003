'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = path.join(__dirname, '..', 'app.js');
const app = fs.existsSync(source) ? require(source) : {};
const { Document, descendants } = require('./dom.cjs');
function fixture() {
  const htmlPath = path.join(__dirname, '..', 'index.html');
  assert.ok(fs.existsSync(htmlPath), 'Operating interface HTML must exist');
  const document = new Document(fs.readFileSync(htmlPath, 'utf8'));
  const calls = [];
  const responses = {
    '/api/dashboard': { equipment_count: 2, active_reservations: 0, open_loans: 0, total_members: 1 },
    '/api/equipment': { items: [{ id: 2, name: '<img src=x onerror=alert(1)>', asset_tag: 'CAM-02', category_name: 'Cameras', category_id: 1, status: 'available', location: 'Media lab' }] },
    '/api/categories': { items: [{ id: 1, name: 'Cameras' }] },
    '/api/members': { items: [{ id: 1, name: 'Member 1', department_name: 'Design', email: 'member1@example.test' }] },
    '/api/reservations': { items: [] }
  };
  const request = async (url, options) => { calls.push({ url, options }); const base = url.split('?')[0]; if (!responses[base]) throw new Error('Unexpected API ' + url); return structuredClone(responses[base]); };
  return { document, calls, responses, request };
}

test('workspace loads real API values, safely renders text and selects equipment for booking', async () => {
  const f = fixture();
  assert.equal(typeof app.createWorkspace, 'function', 'Workspace controller must exist');
  const workspace = app.createWorkspace(f.document, f.request);
  await workspace.start();
  assert.equal(f.document.getElementById('metric-equipment').textContent, '2');
  assert.match(f.document.getElementById('equipment-body').textContent, /<img src=x/);
  assert.equal(descendants(f.document.getElementById('equipment-body')).filter(el => el.tagName === 'IMG').length, 0);
  const reserve = descendants(f.document.getElementById('equipment-body')).find(el => el.tagName === 'BUTTON');
  await reserve.emit('click');
  assert.equal(f.document.getElementById('booking-equipment').value, '2');
  assert.match(f.document.getElementById('selected-equipment').textContent, /CAM-02/);
});

test('booking submit disables during write, verifies reservation readback and keeps conflicts visible', async () => {
  const f = fixture();
  const record = { id: 9, equipment_id: 2, equipment_name: 'Camera', asset_tag: 'CAM-02', member_id: 1, member_name: 'Member 1', start_at: '2026-11-21T10:00:00Z', end_at: '2026-11-21T11:00:00Z', purpose: 'Test session', status: 'confirmed' };
  let conflict = false;
  const request = async (url, options) => {
    if (options?.method === 'POST') {
      assert.equal(f.document.getElementById('booking-submit').disabled, true);
      assert.equal(options.body.start_at, record.start_at);
      if (conflict) throw new Error('Conflicting reservation');
      f.responses['/api/reservations'].items = [record];
      return { id: 9, status: 'confirmed' };
    }
    return f.request(url, options);
  };
  const workspace = app.createWorkspace(f.document, request); await workspace.start();
  await descendants(f.document.getElementById('equipment-body')).find(el => el.tagName === 'BUTTON').emit('click');
  f.document.getElementById('booking-start').value = '2026-11-21T10:00';
  f.document.getElementById('booking-end').value = '2026-11-21T11:00';
  f.document.getElementById('booking-purpose').value = 'Test session';
  await f.document.getElementById('booking-form').emit('submit');
  assert.match(f.document.getElementById('booking-feedback').textContent, /#9.*confirmed/i);
  assert.match(f.document.getElementById('reservations-body').textContent, /Camera/);
  conflict = true;
  await f.document.getElementById('booking-form').emit('submit');
  assert.match(f.document.getElementById('booking-feedback').textContent, /Conflicting reservation/);
  assert.equal(f.document.getElementById('booking-submit').disabled, false);
});

test('navigation loads register and lifecycle actions verify returned state', async () => {
  const f = fixture();
  const record = { id: 4, equipment_name: 'Camera', asset_tag: 'CAM-02', member_name: 'Member 1', start_at: '2026-11-21T10:00:00Z', end_at: '2026-11-21T11:00:00Z', purpose: 'Session', status: 'confirmed' };
  f.responses['/api/reservations'].items = [record];
  const writes = [];
  const request = async (url, options) => {
    if (options?.method === 'POST') { writes.push(url); record.status = url.endsWith('checkout') ? 'checked_out' : url.endsWith('return') ? 'returned' : 'cancelled'; return { id: 4, status: record.status }; }
    return f.request(url, options);
  };
  const workspace = app.createWorkspace(f.document, request); await workspace.start();
  await f.document.getElementById('nav-reservations').emit('click');
  assert.equal(f.document.getElementById('view-reservations').hidden, false);
  let actions = descendants(f.document.getElementById('reservations-body')).filter(el => el.tagName === 'BUTTON');
  assert.deepEqual(actions.map(el => el.textContent), ['Check out', 'Cancel']);
  await actions[0].emit('click');
  actions = descendants(f.document.getElementById('reservations-body')).filter(el => el.tagName === 'BUTTON');
  assert.deepEqual(actions.map(el => el.textContent), ['Return']);
  await actions[0].emit('click');
  assert.match(f.document.getElementById('notice').textContent, /#4.*returned/i);
  assert.deepEqual(writes, ['/api/reservations/4/checkout', '/api/reservations/4/return']);
  assert.equal(descendants(f.document.getElementById('reservations-body')).filter(el => el.tagName === 'BUTTON').length, 0);
  await f.document.getElementById('nav-equipment').emit('click');
  assert.equal(descendants(f.document.getElementById('equipment-body')).find(el => el.tagName === 'BUTTON').disabled, false);
});

test('search and category controls encode query values and refreshing reloads current view', async () => {
  const f = fixture(); const workspace = app.createWorkspace(f.document, f.request); await workspace.start();
  f.document.getElementById('equipment-search').value = 'camera & light';
  f.document.getElementById('category-filter').value = '1';
  await f.document.getElementById('search-form').emit('submit');
  assert.ok(f.calls.some(call => call.url === '/api/equipment?q=camera+%26+light&category_id=1'));
  const count = f.calls.length;
  await f.document.getElementById('refresh-button').emit('click');
  assert.ok(f.calls.length > count);
});

test('analytics renders API usage and overdue values without invented metrics', async () => {
  const f = fixture(); f.responses['/api/analytics'] = { category_usage: [{ category: 'Cameras', reservations: 3 }], top_equipment: [{ equipment: 'Camera', reservations: 2 }], overdue: [{ reservation_id: 4, equipment: 'Camera', member: 'Member 1', end_at: '2026-11-19T10:00:00Z' }] };
  const workspace = app.createWorkspace(f.document, f.request); await workspace.start();
  await f.document.getElementById('nav-analytics').emit('click');
  assert.match(f.document.getElementById('category-usage').textContent, /Cameras3/);
  assert.match(f.document.getElementById('top-equipment').textContent, /Camera2/);
  assert.equal(f.document.getElementById('overdue-count').textContent, '1');
  assert.match(f.document.getElementById('overdue-body').textContent, /2026-11-19 10:00:00 UTC/);
});

test('database inspector shows expandable SQL and actual audit events safely', async () => {
  const f = fixture();
  f.responses['/api/schema'] = { tables: [{ name: 'members', sql: 'CREATE TABLE members(id INTEGER)' }], indexes: [{ name: 'ix_member', sql: 'CREATE INDEX ix_member ON members(id)' }] };
  f.responses['/api/audit'] = { items: [{ id: 8, reservation_id: 4, action: 'returned', created_at: '2026-11-20T10:00:00Z' }] };
  const workspace = app.createWorkspace(f.document, f.request); await workspace.start();
  await f.document.getElementById('nav-database').emit('click');
  assert.match(f.document.getElementById('schema-objects').textContent, /CREATE TABLE members/);
  assert.equal(descendants(f.document.getElementById('schema-objects')).filter(el => el.tagName === 'DETAILS').length, 2);
  assert.match(f.document.getElementById('audit-body').textContent, /returned/);
  assert.match(f.document.getElementById('audit-body').textContent, /#4/);
});

test('refresh clears a selected asset that is no longer available', async () => {
  const f = fixture(); const workspace = app.createWorkspace(f.document, f.request); await workspace.start();
  await descendants(f.document.getElementById('equipment-body')).find(el => el.tagName === 'BUTTON').emit('click');
  f.responses['/api/equipment'].items[0].status = 'maintenance';
  await f.document.getElementById('refresh-button').emit('click');
  assert.equal(f.document.getElementById('booking-equipment').value, '');
  assert.equal(f.document.getElementById('booking-submit').disabled, true);
  assert.match(f.document.getElementById('booking-feedback').textContent, /no longer available/i);
});

test('offline startup shows a visible connection error and actionable equipment state', async () => {
  const f = fixture(); const workspace = app.createWorkspace(f.document, async () => { throw new Error('Cannot connect to local server'); });
  await workspace.start();
  assert.match(f.document.getElementById('notice').textContent, /Cannot connect/);
  assert.equal(f.document.getElementById('notice').hidden, false);
  assert.match(f.document.getElementById('equipment-body').textContent, /Refresh data/i);
  assert.equal(f.document.getElementById('metric-equipment').textContent, '');
});

test('UTC form defaults are future canonical UTC without local-time conversion', () => {
  assert.equal(typeof app.defaultTimes, 'function', 'UTC default helper must exist');
  const times = app.defaultTimes(new Date('2026-11-20T09:23:45Z'));
  assert.equal(times.start, '2026-11-20T10:00');
  assert.equal(times.end, '2026-11-20T11:00');
});

test('reservation payload treats datetime-local as explicitly UTC and rejects reversed ranges', () => {
  assert.equal(typeof app.reservationPayload, 'function', 'Payload validator must exist');
  const values = { member_id: '1', equipment_id: '2', start_at: '2026-11-20T11:00', end_at: '2026-11-20T12:00', purpose: '  Study session  ' };
  assert.deepEqual(app.reservationPayload(values), { member_id: 1, equipment_id: 2, start_at: '2026-11-20T11:00:00Z', end_at: '2026-11-20T12:00:00Z', purpose: 'Study session' });
  assert.throws(() => app.reservationPayload({ ...values, end_at: values.start_at }), /after/i);
  assert.throws(() => app.reservationPayload({ ...values, start_at: '2026-02-30T11:00' }), /valid/i);
  assert.throws(() => app.reservationPayload({ ...values, member_id: '' }), /member/i);
  assert.throws(() => app.reservationPayload({ ...values, purpose: '  ' }), /purpose/i);
});

test('API adapter sends explicit JSON and surfaces backend conflicts and connection failures', async () => {
  assert.equal(typeof app.apiRequest, 'function', 'API adapter must exist');
  let sent;
  const result = await app.apiRequest('/api/reservations/7/cancel', { method: 'POST', body: {} }, async (url, options) => {
    sent = { url, options };
    return { ok: true, json: async () => ({ id: 7, status: 'cancelled' }) };
  });
  assert.equal(result.status, 'cancelled');
  assert.equal(sent.options.body, '{}');
  assert.equal(sent.options.headers['Content-Type'], 'application/json');
  await assert.rejects(app.apiRequest('/api/reservations', {}, async () => ({ ok: false, status: 409, json: async () => ({ error: 'Overlapping booking' }) })), /Overlapping booking/);
  await assert.rejects(app.apiRequest('/api/equipment', {}, async () => { throw new TypeError('Failed to fetch'); }), /connect/i);
});
