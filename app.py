"""CampusGear local-only, standard-library SQLite application."""
from pathlib import Path
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit, unquote
import argparse
import mimetypes
import json
import re
import sqlite3

ROOT = Path(__file__).resolve().parent


def connect(path):
    db = sqlite3.connect(str(path), timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    db.execute('PRAGMA busy_timeout = 15000')
    return db


def create_database(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = connect(path)
    try:
        db.execute('PRAGMA journal_mode = WAL')
        db.executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))
        with db:
            db.executemany('INSERT OR IGNORE INTO departments(id,name) VALUES (?,?)',
                           [(1, 'Computing'), (2, 'Design'), (3, 'Engineering')])
            db.executemany('INSERT OR IGNORE INTO categories(id,name) VALUES (?,?)',
                           [(1, 'Cameras'), (2, 'Audio'), (3, 'Computing'), (4, 'Presentation')])
            db.executemany('INSERT OR IGNORE INTO members(id,name,email,department_id) VALUES (?,?,?,?)',
                           [(i, f'Demo Member {i}', f'member{i}@campusgear.example', (i - 1) % 3 + 1) for i in range(1, 7)])
            rows = [(1, 'CG-CAM-001', 'Mirrorless Camera', 1, 'Library · Desk A', 'available'),
                    (2, 'CG-CAM-002', 'Video Camera', 1, 'Media Lab', 'available'),
                    (3, 'CG-AUD-001', 'Portable Audio Recorder', 2, 'Media Lab', 'available'),
                    (4, 'CG-AUD-002', 'Wireless Microphone', 2, 'Library · Desk A', 'available'),
                    (5, 'CG-COM-001', 'Laptop Kit', 3, 'IT Help Desk', 'available'),
                    (6, 'CG-PRE-001', 'Portable Projector', 4, 'Library · Desk B', 'available'),
                    (7, 'CG-CAM-003', 'Camera Under Service', 1, 'Workshop', 'maintenance'),
                    (8, 'CG-COM-002', 'Retired Tablet', 3, 'Storage', 'retired')]
            db.executemany('INSERT OR IGNORE INTO equipment(id,asset_tag,name,category_id,location,status) VALUES (?,?,?,?,?,?)', rows)
    finally:
        db.close()


class APIError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def utc_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def read_api(db, path, query):
    def rows(sql, args=()):
        return [dict(row) for row in db.execute(sql, args)]

    if path == '/api/dashboard':
        return dict(equipment_count=db.execute('SELECT COUNT(*) FROM equipment').fetchone()[0],
                    active_reservations=db.execute("SELECT COUNT(*) FROM reservations WHERE status IN ('confirmed','checked_out')").fetchone()[0],
                    open_loans=db.execute('SELECT COUNT(*) FROM loans WHERE returned_at IS NULL').fetchone()[0],
                    total_members=db.execute('SELECT COUNT(*) FROM members').fetchone()[0])
    if path == '/api/equipment':
        sql = 'SELECT e.*,c.name category_name FROM equipment e JOIN categories c ON c.id=e.category_id WHERE 1=1'
        args = []
        if query.get('q', [''])[0]:
            sql += ' AND (e.name LIKE ? OR e.asset_tag LIKE ? OR e.location LIKE ?)'
            args += ['%' + query['q'][0] + '%'] * 3
        if query.get('category_id', [''])[0]:
            sql += ' AND e.category_id=?'
            args.append(query['category_id'][0])
        return {'items': rows(sql + ' ORDER BY e.id', args)}
    if path == '/api/members':
        return {'items': rows('SELECT m.*,d.name department_name FROM members m JOIN departments d ON d.id=m.department_id ORDER BY m.id')}
    if path == '/api/categories':
        return {'items': rows('SELECT id,name FROM categories ORDER BY id')}
    if path == '/api/reservations':
        return {'items': rows('''SELECT r.id,r.member_id,m.name member_name,r.equipment_id,e.name equipment_name,
          e.asset_tag,r.start_at,r.end_at,r.purpose,r.status FROM reservations r
          JOIN members m ON m.id=r.member_id JOIN equipment e ON e.id=r.equipment_id ORDER BY r.id DESC''')}
    if path == '/api/audit':
        return {'items': rows('SELECT id,reservation_id,action,created_at FROM audit_log ORDER BY id DESC LIMIT 100')}
    if path == '/api/schema':
        return {kind: rows("SELECT name,sql FROM sqlite_master WHERE type=? AND name NOT LIKE 'sqlite_%' ORDER BY name", (typ,))
                for kind, typ in [('tables', 'table'), ('indexes', 'index')]}
    if path == '/api/analytics':
        return {'category_usage': rows('''SELECT c.name category,COUNT(r.id) reservations FROM categories c
                  LEFT JOIN equipment e ON e.category_id=c.id LEFT JOIN reservations r ON r.equipment_id=e.id
                  GROUP BY c.id ORDER BY reservations DESC,c.id'''),
                'top_equipment': rows('''SELECT e.name equipment,COUNT(r.id) reservations FROM equipment e
                  LEFT JOIN reservations r ON r.equipment_id=e.id GROUP BY e.id ORDER BY reservations DESC,e.id LIMIT 5'''),
                'overdue': rows('''SELECT r.id reservation_id,e.name equipment,m.name member,r.end_at FROM reservations r
                  JOIN equipment e ON e.id=r.equipment_id JOIN members m ON m.id=r.member_id
                  JOIN loans l ON l.reservation_id=r.id WHERE r.status='checked_out' AND l.returned_at IS NULL
                  AND r.end_at < ? ORDER BY r.end_at''', (utc_now(),))}
    raise APIError(404, 'API endpoint not found')


def validate_booking(data):
    fields = {'member_id', 'equipment_id', 'start_at', 'end_at', 'purpose'}
    if set(data) != fields:
        raise APIError(400, 'Provide exactly member_id, equipment_id, start_at, end_at and purpose')
    for field in ('member_id', 'equipment_id'):
        if type(data[field]) is not int or not 0 < data[field] < 2**63:
            raise APIError(400, field + ' must be a positive integer')
    for field in ('start_at', 'end_at'):
        value = data[field]
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value):
            raise APIError(400, field + ' must be canonical UTC YYYY-MM-DDTHH:MM:SSZ')
        try:
            datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
        except ValueError:
            raise APIError(400, field + ' is not a real datetime') from None
    if data['start_at'] >= data['end_at']:
        raise APIError(400, 'start_at must precede end_at')
    if not isinstance(data['purpose'], str) or not data['purpose'].strip() or len(data['purpose']) > 500:
        raise APIError(400, 'purpose must contain 1–500 characters')


def transition(db, identity, action, data):
    if data:
        raise APIError(400, 'Action body must be an empty JSON object')
    with db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM reservations WHERE id=?', (identity,)).fetchone()
        if not row:
            raise APIError(404, 'Reservation not found')
        expected = 'checked_out' if action == 'return' else 'confirmed'
        if row['status'] != expected:
            raise APIError(409, 'Invalid transition from ' + row['status'] + ' via ' + action)
        target = {'cancel': 'cancelled', 'checkout': 'checked_out', 'return': 'returned'}[action]
        if action == 'return':
            updated = db.execute('UPDATE loans SET returned_at=? WHERE reservation_id=? AND returned_at IS NULL', (utc_now(), identity))
            if updated.rowcount != 1:
                raise APIError(409, 'No open loan found')
        if action == 'checkout':
            equipment = db.execute('SELECT status FROM equipment WHERE id=?', (row['equipment_id'],)).fetchone()
            if equipment['status'] != 'available':
                raise APIError(409, 'Equipment is not available')
            if db.execute("SELECT 1 FROM reservations WHERE equipment_id=? AND status='checked_out'", (row['equipment_id'],)).fetchone():
                raise APIError(409, 'Equipment already has an open loan')
            db.execute('INSERT INTO loans(reservation_id,checked_out_at) VALUES (?,?)', (identity, utc_now()))
        db.execute('UPDATE reservations SET status=? WHERE id=?', (target, identity))
        db.execute('INSERT INTO audit_log(reservation_id,action) VALUES (?,?)', (identity, target))
    return 200, {'id': identity, 'status': target}


def write_api(db, path, data):
    match = re.fullmatch(r'/api/reservations/([1-9][0-9]{0,17})/(cancel|checkout|return)', path)
    if match:
        return transition(db, int(match[1]), match[2], data)
    if path != '/api/reservations':
        raise APIError(404, 'API endpoint not found')
    validate_booking(data)
    # Serialize the availability check and write across independent connections.
    with db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT 1 FROM members WHERE id=?', (data['member_id'],)).fetchone():
            raise APIError(404, 'Member not found')
        equipment = db.execute('SELECT status FROM equipment WHERE id=?', (data['equipment_id'],)).fetchone()
        if not equipment:
            raise APIError(404, 'Equipment not found')
        if equipment['status'] != 'available':
            raise APIError(409, 'Equipment is not available')
        cursor = db.execute('''INSERT INTO reservations(member_id,equipment_id,start_at,end_at,purpose)
                               VALUES (?,?,?,?,?)''', tuple(data[f] for f in ('member_id','equipment_id','start_at','end_at','purpose')))
        identity = cursor.lastrowid
        db.execute('INSERT INTO audit_log(reservation_id,action) VALUES (?,?)', (identity, 'created'))
    return 201, {'id': identity, 'status': 'confirmed'}


class Handler(BaseHTTPRequestHandler):
    def protect_local(self):
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if self.server.server_port == 80:
            allowed.update(('127.0.0.1', 'localhost'))
        hosts = self.headers.get_all('Host', [])
        if len(hosts) != 1 or hosts[0].lower() not in allowed:
            raise APIError(400, 'Local Host required')
        origin = self.headers.get('Origin')
        if origin is not None and origin.lower() != 'http://' + hosts[0].lower():
            raise APIError(400, 'Same-origin local requests required')
        if self.headers.get('Transfer-Encoding'):
            raise APIError(400, 'Transfer-Encoding is not supported')

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            try:
                length = int(self.headers.get('Content-Length', '-1'))
            except ValueError:
                raise APIError(400, 'Invalid Content-Length') from None
            # Consume bounded bodies before rejecting, avoiding TCP reset on Windows.
            raw = self.rfile.read(length) if 0 <= length <= 65536 else b''
            self.protect_local()
            if self.headers.get_content_type() != 'application/json':
                raise APIError(400, 'Content-Type must be application/json')
            if not 0 <= length <= 16384:
                raise APIError(400, 'JSON body must be at most 16384 bytes')
            try:
                data = json.loads(raw)
            except (ValueError, UnicodeDecodeError, RecursionError):
                raise APIError(400, 'Invalid JSON body') from None
            if not isinstance(data, dict):
                raise APIError(400, 'JSON body must be an object')
            with closing(connect(self.server.db_path)) as db:
                status, payload = write_api(db, urlsplit(self.path).path, data)
            self.send_json(status, payload)
        except APIError as exc:
            self.send_json(exc.status, {'error': str(exc)})
        except sqlite3.IntegrityError as exc:
            self.send_json(409, {'error': str(exc)})

    def serve_static(self, path):
        relative = 'index.html' if path == '/' else unquote(path).lstrip('/')
        root = self.server.static_root.resolve()
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file() or target.suffix not in {'.html','.css','.js','.svg','.png','.ico'}:
            raise APIError(404, 'Static file not found')
        body = target.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self.protect_local()
            url = urlsplit(self.path)
            if not url.path.startswith('/api/'):
                self.serve_static(url.path)
                return
            with closing(connect(self.server.db_path)) as db:
                payload = read_api(db, url.path, parse_qs(url.query))
            self.send_json(200, payload)
        except APIError as exc:
            self.send_json(exc.status, {'error': str(exc)})


def make_server(path, port):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.db_path = Path(path)
    server.static_root = ROOT / 'static'
    return server


def main():
    parser = argparse.ArgumentParser(description='CampusGear synthetic-data local prototype; no login. UTC timestamps only.')
    parser.add_argument('--port', type=int, default=8765, help='Loopback HTTP port (default: 8765)')
    parser.add_argument('--db', type=Path, default=ROOT / 'data' / 'campusgear.db', help='SQLite database path')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be 1–65535')
    create_database(args.db)
    with make_server(args.db, args.port) as server:
        print(f'CampusGear: http://127.0.0.1:{args.port} | synthetic data, no authentication', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
