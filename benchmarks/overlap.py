"""Deterministic legal bookings; same COUNT overlap SQL before/after index.

Run from project root: python benchmarks/overlap.py --repetitions 31
Timings are warm-cache query latency, not HTTP latency or load throughput.
"""
import argparse
from contextlib import closing
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import platform
import random
import sqlite3
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app

QUERY = """SELECT COUNT(*) FROM reservations WHERE equipment_id=?
 AND status IN ('confirmed','checked_out') AND start_at < ? AND end_at > ?"""
INDEX_SQL = 'CREATE INDEX idx_reservations_overlap ON reservations(equipment_id,status,start_at,end_at)'
SEED = 5003


def fingerprint(db):
    digest = hashlib.sha256()
    for row in db.execute('SELECT * FROM reservations ORDER BY id'):
        digest.update(json.dumps(tuple(row), ensure_ascii=False).encode('utf-8'))
    return digest.hexdigest()


def measure(db, args, repetitions):
    for _ in range(5):
        db.execute(QUERY, args).fetchone()
    samples = []
    count = None
    for _ in range(repetitions):
        start = time.perf_counter_ns()
        count = db.execute(QUERY, args).fetchone()[0]
        samples.append((time.perf_counter_ns() - start) / 1_000_000)
    plan = [r[3] for r in db.execute('EXPLAIN QUERY PLAN ' + QUERY, args)]
    return count, samples, plan


def run_case(size, path, repetitions=31):
    if size < 1000 or repetitions < 1:
        raise ValueError('Use at least 1000 reservations and one repetition')
    path = Path(path)
    if path.exists():
        raise FileExistsError('Refusing to overwrite existing benchmark database: ' + str(path))
    app.create_database(path)
    rng = random.Random(SEED)
    base = datetime(2030, 1, 1, tzinfo=timezone.utc)
    with closing(app.connect(path)) as db:
        with db:
            db.executemany('INSERT INTO equipment(id,asset_tag,name,category_id,location,status) VALUES (?,?,?,?,?,?)',
                           [(i, f'BENCH-{i:04d}', f'Synthetic Equipment {i}', (i-1)%4+1, 'Benchmark store', 'available') for i in range(9,1001)])
            # All benchmark resources are usable; never modify the app demo DB.
            db.execute("UPDATE equipment SET status='available'")
            batch = []
            for i in range(size):
                start = base + timedelta(hours=2*(i//1000))
                end = start + timedelta(hours=1)
                batch.append((rng.randint(1,6), i%1000+1, start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                              end.strftime('%Y-%m-%dT%H:%M:%SZ'), f'Synthetic study {i}', '2030-01-01T00:00:00Z'))
            db.executemany('INSERT INTO reservations(member_id,equipment_id,start_at,end_at,purpose,created_at) VALUES (?,?,?,?,?,?)', batch)
        probe = base + timedelta(hours=2*((size//1000-1)//2), minutes=15)
        args = (42, (probe+timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M:%SZ'), probe.strftime('%Y-%m-%dT%H:%M:%SZ'))
        before = fingerprint(db)
        db.execute('DROP INDEX idx_reservations_overlap')
        db.commit()
        count_no, samples_no, plan_no = measure(db, args, repetitions)
        db.execute(INDEX_SQL)
        db.commit()
        count_yes, samples_yes, plan_yes = measure(db, args, repetitions)
        after = fingerprint(db)
        actual_rows = db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0]
        assert actual_rows == size and count_no == count_yes == 1 and before == after
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    return dict(rows=actual_rows, seed=SEED, repetitions=repetitions, query=QUERY, parameters=args,
                count_without_index=count_no, count_with_index=count_yes,
                fingerprint_before=before, fingerprint_after=after,
                median_without_index_ms=statistics.median(samples_no), median_with_index_ms=statistics.median(samples_yes),
                samples_without_index_ms=samples_no, samples_with_index_ms=samples_yes,
                plan_without_index=plan_no, plan_with_index=plan_yes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repetitions', type=int, default=31)
    parser.add_argument('--db-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'evidence')
    args = parser.parse_args()
    args.db_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = [run_case(size, args.db_dir / f'benchmark-{size}.db', args.repetitions) for size in (1000,10000,100000)]
    report = dict(python=platform.python_version(), sqlite=sqlite3.sqlite_version, platform=platform.platform(),
                  measured_at_utc=app.utc_now(), methodology='5 warmups; no-index then index; median ms; same file/data/query/parameters; 1000 equipment; legal hourly slots separated by 1 hour; no throughput claim', cases=results)
    (args.output_dir / 'benchmark-results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    fields = ['rows','repetitions','count_without_index','count_with_index','median_without_index_ms','median_with_index_ms']
    with (args.output_dir / 'benchmark-results.csv').open('w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(results)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
