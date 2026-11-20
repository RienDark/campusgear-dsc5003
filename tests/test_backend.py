"""Real SQLite/HTTP contract tests; no service or dependency required."""
import importlib
from contextlib import closing
import os
import http.client
import json
import threading
import subprocess
import sys
import socket
import time
import pathlib
import sqlite3
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class InitializationTests(unittest.TestCase):
    def test_database_initializes_with_synthetic_data_and_foreign_keys(self):
        self.assertTrue((ROOT / 'app.py').exists(), 'database implementation missing')
        app = importlib.import_module('app')
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as folder:
            path = pathlib.Path(folder) / 'demo.db'
            app.create_database(path)
            app.create_database(path)
            with closing(app.connect(path)) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({'departments', 'members', 'categories', 'equipment', 'reservations', 'loans', 'audit_log'} <= tables)
                self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
                self.assertGreaterEqual(db.execute('SELECT COUNT(*) FROM equipment').fetchone()[0], 6)
                self.assertEqual(db.execute('SELECT name FROM members WHERE id=1').fetchone()[0], 'Demo Member 1')
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("INSERT INTO members(name,email,department_id) VALUES ('x','x@example.test',999)")


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.app = importlib.import_module('app')
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.path = pathlib.Path(self.tmp.name) / 'test.db'
        self.app.create_database(self.path)
        self.db = self.app.connect(self.path)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def insert(self, start='2030-01-01T10:00:00Z', end='2030-01-01T11:00:00Z', equipment=1):
        return self.db.execute('INSERT INTO reservations(member_id,equipment_id,start_at,end_at,purpose) VALUES (1,?,?,?,?)',
                               (equipment, start, end, 'Study')).lastrowid


class DatabaseTests(DatabaseCase):
    def test_database_prevents_two_checked_out_bookings_on_same_equipment(self):
        first = self.insert()
        second = self.insert('2030-01-01T12:00:00Z', '2030-01-01T13:00:00Z')
        self.db.execute("UPDATE reservations SET status='checked_out' WHERE id=?", (first,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE reservations SET status='checked_out' WHERE id=?", (second,))

    def test_database_trigger_rejects_invalid_status_transition(self):
        identity = self.insert()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'transition'):
            self.db.execute("UPDATE reservations SET status='returned' WHERE id=?", (identity,))
        self.db.execute("UPDATE reservations SET status='cancelled' WHERE id=?", (identity,))
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'transition'):
            self.db.execute("UPDATE reservations SET status='confirmed' WHERE id=?", (identity,))

    def test_database_trigger_rejects_overlap_update_preserves_half_open_boundary(self):
        first = self.insert()
        adjacent = self.insert('2030-01-01T11:00:00Z', '2030-01-01T12:00:00Z')
        self.assertNotEqual(first, adjacent)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'overlap'):
            self.db.execute('UPDATE reservations SET start_at=? WHERE id=?', ('2030-01-01T10:59:59Z', adjacent))
        self.db.execute("UPDATE reservations SET status='cancelled' WHERE id=?", (first,))
        replacement = self.insert()
        self.assertGreater(replacement, adjacent)

    def test_database_trigger_rejects_overlap_insert(self):
        self.insert()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'overlap'):
            self.insert('2030-01-01T10:30:00Z', '2030-01-01T12:00:00Z')


class HTTPTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        self.server = None
        if hasattr(self.app, 'make_server'):
            self.server = self.app.make_server(self.path, 0)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()
        super().tearDown()

    def request(self, method, path, data=None, headers=None, raw=None):
        self.assertIsNotNone(self.server, 'HTTP server implementation missing')
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        body = raw if raw is not None else (json.dumps(data) if data is not None else None)
        request_headers = {'Content-Type': 'application/json'}
        if headers:
            request_headers.update(headers)
        conn.request(method, path, body=body, headers=request_headers)
        response = conn.getresponse()
        payload = response.read()
        result = (response.status, json.loads(payload) if response.getheader('Content-Type', '').startswith('application/json') else payload)
        conn.close()
        return result

    def booking(self, **changes):
        data = dict(member_id=1, equipment_id=1, start_at='2030-01-01T10:00:00Z', end_at='2030-01-01T11:00:00Z', purpose='Study')
        data.update(changes)
        return self.request('POST', '/api/reservations', data)

    def test_cli_starts_real_process_and_initializes_requested_database(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        path = pathlib.Path(self.tmp.name) / 'cli.db'
        process = subprocess.Popen([sys.executable, str(ROOT / 'app.py'), '--db', str(path), '--port', str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while True:
                try:
                    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
                    conn.request('GET', '/api/dashboard')
                    response = conn.getresponse()
                    data = json.loads(response.read())
                    conn.close()
                    break
                except OSError:
                    if time.monotonic() >= deadline or process.poll() is not None:
                        self.fail('CLI server failed to start')
                    time.sleep(0.05)
            self.assertEqual(response.status, 200)
            self.assertEqual(data['equipment_count'], 8)
            self.assertTrue(path.exists())
        finally:
            process.terminate()
            process.communicate(timeout=10)

    def test_static_frontend_served_without_exposing_source(self):
        static = pathlib.Path(self.tmp.name) / 'static'
        static.mkdir()
        (static / 'index.html').write_text('<h1>Test fixture</h1>', encoding='utf-8')
        (static / 'app.js').write_text('/* fixture */', encoding='utf-8')
        self.server.static_root = static
        self.assertEqual(self.request('GET', '/'), (200, b'<h1>Test fixture</h1>'))
        self.assertEqual(self.request('GET', '/app.js'), (200, b'/* fixture */'))
        for path in ['/app.py','/schema.sql','/../schema.sql','/%2e%2e/schema.sql','/data/test.db']:
            self.assertEqual(self.request('GET', path)[0], 404)

    def test_cli_help_exposes_reproducible_db_and_port_flags(self):
        result = subprocess.run([sys.executable, str(ROOT / 'app.py'), '--help'], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn('--db', result.stdout)
        self.assertIn('--port', result.stdout)

    def test_concurrent_connections_only_one_booking_commits(self):
        barrier = threading.Barrier(2)
        outcomes = []
        def reserve():
            barrier.wait(timeout=5)
            outcomes.append(self.booking()[0])
        workers = [threading.Thread(target=reserve) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
            self.assertFalse(worker.is_alive())
        self.assertEqual(sorted(outcomes), [201,409])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0], 1)

    def test_audit_failure_rolls_back_reservation_loan_and_status(self):
        self.db.executescript("CREATE TRIGGER fail_audit BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT,'audit unavailable'); END;")
        self.assertEqual(self.booking()[0], 409)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0], 0)
        self.db.execute('DROP TRIGGER fail_audit')
        self.db.commit()
        self.assertEqual(self.booking()[0], 201)
        self.db.executescript("CREATE TRIGGER fail_audit BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT,'audit unavailable'); END;")
        self.assertEqual(self.request('POST', '/api/reservations/1/checkout', {})[0], 409)
        self.assertEqual(self.db.execute('SELECT status FROM reservations WHERE id=1').fetchone()[0], 'confirmed')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM loans').fetchone()[0], 0)

    def test_return_audit_failure_rolls_back_loan_timestamp(self):
        self.assertEqual(self.booking()[0], 201)
        self.assertEqual(self.request('POST', '/api/reservations/1/checkout', {})[0], 200)
        self.db.executescript("CREATE TRIGGER fail_audit BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT,'audit unavailable'); END;")
        self.assertEqual(self.request('POST', '/api/reservations/1/return', {})[0], 409)
        self.assertEqual(self.db.execute('SELECT status FROM reservations').fetchone()[0], 'checked_out')
        self.assertIsNone(self.db.execute('SELECT returned_at FROM loans').fetchone()[0])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0], 2)

    def test_concurrent_checkout_of_nonoverlapping_bookings_only_one_loan_opens(self):
        self.assertEqual(self.booking()[0], 201)
        self.assertEqual(self.booking(start_at='2030-01-01T12:00:00Z', end_at='2030-01-01T13:00:00Z')[0], 201)
        barrier = threading.Barrier(2)
        outcomes = []
        def checkout(identity):
            barrier.wait(timeout=5)
            outcomes.append(self.request('POST', f'/api/reservations/{identity}/checkout', {})[0])
        workers = [threading.Thread(target=checkout, args=(i,)) for i in (1,2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
            self.assertFalse(worker.is_alive())
        self.assertEqual(sorted(outcomes), [200,409])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM loans').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0], 3)

    def test_cancelled_reservation_cannot_checkout_or_return_and_actions_require_empty_body(self):
        self.assertEqual(self.booking()[0], 201)
        self.assertEqual(self.request('POST', '/api/reservations/1/cancel', {'status':'returned'})[0], 400)
        self.assertEqual(self.request('POST', '/api/reservations/1/cancel', {})[0], 200)
        for action in ('checkout','return','cancel'):
            self.assertEqual(self.request('POST', '/api/reservations/1/' + action, {})[0], 409)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM loans').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0], 2)

    def test_deeply_nested_json_returns_validation_error_not_connection_drop(self):
        self.assertEqual(self.request('POST', '/api/reservations', raw='['*7000 + '0' + ']'*7000)[0], 400)

    def test_local_host_origin_json_size_and_route_protection(self):
        self.assertEqual(self.request('GET', '/api/dashboard', headers={'Host':'evil.example'})[0], 400)
        self.assertEqual(self.request('POST', '/api/reservations', {}, headers={'Origin':'https://evil.example'})[0], 400)
        local = f'http://127.0.0.1:{self.server.server_port}'
        self.assertEqual(self.request('GET', '/api/dashboard', headers={'Origin':local})[0], 200)
        self.assertEqual(self.request('POST', '/api/reservations', {}, headers={'Content-Type':'text/plain'})[0], 400)
        self.assertEqual(self.request('POST', '/api/reservations', raw='x'*16385)[0], 400)
        self.assertEqual(self.request('POST', '/api/reservations', raw='{')[0], 400)
        self.assertEqual(self.request('POST', '/api/reservations', [1,2])[0], 400)
        self.assertEqual(self.request('GET', '/api/missing')[0], 404)
        self.assertEqual(self.request('POST', '/api/missing', {})[0], 404)
        self.assertEqual(self.request('GET', '/api/equipment?q=%27%20OR%201%3D1--')[1], {'items':[]})
        self.assertEqual(self.server.server_address[0], '127.0.0.1')

    def test_return_closes_loan_atomically_and_updates_overdue_analytics(self):
        self.assertEqual(self.booking(start_at='2020-01-01T10:00:00Z', end_at='2020-01-01T11:00:00Z')[0], 201)
        self.assertEqual(self.request('POST', '/api/reservations/1/return', {})[0], 409)
        self.assertEqual(self.request('POST', '/api/reservations/1/checkout', {})[0], 200)
        self.assertEqual(self.request('GET', '/api/analytics')[1]['overdue'][0]['reservation_id'], 1)
        self.assertEqual(self.request('POST', '/api/reservations/1/return', {}), (200, {'id':1, 'status':'returned'}))
        self.assertEqual(self.request('POST', '/api/reservations/1/return', {})[0], 409)
        self.assertEqual(self.request('POST', '/api/reservations/1/checkout', {})[0], 409)
        self.assertIsNotNone(self.db.execute('SELECT returned_at FROM loans').fetchone()[0])
        self.assertEqual(self.request('GET', '/api/analytics')[1]['overdue'], [])
        self.assertEqual(self.request('GET', '/api/dashboard')[1]['open_loans'], 0)
        self.assertEqual([r[0] for r in self.db.execute('SELECT action FROM audit_log ORDER BY id')], ['created','checked_out','returned'])

    def test_checkout_creates_one_loan_prevents_duplicate_and_nonoverlap_double_checkout(self):
        self.assertEqual(self.booking()[0], 201)
        self.assertEqual(self.booking(start_at='2030-01-01T12:00:00Z', end_at='2030-01-01T13:00:00Z')[0], 201)
        self.assertEqual(self.request('POST', '/api/reservations/1/checkout', {}), (200, {'id': 1, 'status': 'checked_out'}))
        self.assertEqual(self.request('POST', '/api/reservations/1/checkout', {})[0], 409)
        self.assertEqual(self.request('POST', '/api/reservations/2/checkout', {})[0], 409)
        self.assertEqual(self.request('POST', '/api/reservations/1/cancel', {})[0], 409)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM loans WHERE returned_at IS NULL').fetchone()[0], 1)
        self.assertEqual([r[0] for r in self.db.execute('SELECT action FROM audit_log ORDER BY id')], ['created','created','checked_out'])
        self.assertEqual(self.booking(equipment_id=2)[0], 201)
        self.db.execute("UPDATE equipment SET status='maintenance' WHERE id=2")
        self.db.commit()
        self.assertEqual(self.request('POST', '/api/reservations/3/checkout', {})[0], 409)

    def test_cancel_releases_slot_and_rejects_repeated_cancel(self):
        self.assertEqual(self.booking()[0], 201)
        self.assertEqual(self.request('POST', '/api/reservations/1/cancel', {}), (200, {'id': 1, 'status': 'cancelled'}))
        self.assertEqual(self.request('POST', '/api/reservations/1/cancel', {})[0], 409)
        self.assertEqual(self.request('POST', '/api/reservations/999/cancel', {})[0], 404)
        self.assertEqual(self.booking()[0], 201)
        self.assertEqual([r[0] for r in self.db.execute('SELECT action FROM audit_log ORDER BY id')], ['created','cancelled','created'])

    def test_create_booking_records_atomic_audit_and_rejects_conflicts(self):
        status, booking = self.booking(purpose="Study '); DROP TABLE equipment;--")
        self.assertEqual(status, 201)
        self.assertEqual(booking, {'id': 1, 'status': 'confirmed'})
        items = self.request('GET', '/api/reservations')[1]['items']
        self.assertEqual(set(items[0]), {'id','member_id','member_name','equipment_id','equipment_name','asset_tag','start_at','end_at','purpose','status'})
        self.assertIn('DROP TABLE', items[0]['purpose'])
        self.assertEqual(self.request('GET', '/api/audit')[1]['items'][0]['action'], 'created')
        self.assertEqual(self.booking()[0], 409)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0], 1)
        self.assertEqual(self.booking(start_at='2030-01-01T11:00:00Z', end_at='2030-01-01T12:00:00Z')[0], 201)
        self.assertEqual(self.booking(equipment_id=7)[0], 409)
        self.assertEqual(self.booking(member_id=999)[0], 404)
        self.assertEqual(self.booking(equipment_id=999)[0], 404)
        for changes in [dict(start_at='2030-02-30T10:00:00Z'), dict(start_at='2030-01-01T10:00:00'),
                        dict(start_at='2030-01-01T10:00:00+00:00'), dict(start_at='2030-1-01T10:00:00Z'),
                        dict(start_at='٢٠٣٠-01-01T10:00:00Z'),
                        dict(end_at='2030-01-01T10:00:00Z'), dict(member_id=True), dict(equipment_id='1 OR 1=1'),
                        dict(purpose='  '), dict(purpose='a'*501), dict(purpose=' '*501+'x'), dict(unknown=1)]:
            with self.subTest(changes=changes):
                self.assertEqual(self.booking(**changes)[0], 400)
        self.assertEqual(self.request('POST', '/api/reservations', {})[0], 400)

    def test_read_only_api_contract(self):
        code, dashboard = self.request('GET', '/api/dashboard')
        self.assertEqual(code, 200)
        self.assertEqual(dashboard, dict(equipment_count=8, active_reservations=0, open_loans=0, total_members=6))
        expected = {'equipment': {'id','asset_tag','name','category_id','category_name','location','status'},
                    'members': {'id','name','email','department_id','department_name'},
                    'categories': {'id','name'}}
        for route, fields in expected.items():
            status, payload = self.request('GET', '/api/' + route)
            self.assertEqual(status, 200)
            self.assertEqual(set(payload['items'][0]), fields)
        self.assertEqual(self.request('GET', '/api/reservations')[1], {'items': []})
        self.assertEqual(self.request('GET', '/api/audit')[1], {'items': []})
        self.assertEqual(set(self.request('GET', '/api/analytics')[1]), {'category_usage','top_equipment','overdue'})
        schema = self.request('GET', '/api/schema')[1]
        self.assertEqual(len(schema['tables']), 7)
        self.assertTrue(any(i['name'] == 'idx_reservations_overlap' for i in schema['indexes']))
        self.assertEqual(self.request('GET', '/api/equipment?category_id=2')[1]['items'][0]['category_id'], 2)
        self.assertEqual(self.request('GET', '/api/equipment?q=Mirrorless')[1]['items'][0]['id'], 1)


if __name__ == '__main__':
    unittest.main()
