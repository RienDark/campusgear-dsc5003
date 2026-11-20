"""The benchmark measures real SQLite plans and identical data."""
from contextlib import closing
import importlib
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BenchmarkTests(unittest.TestCase):
    def test_index_comparison_keeps_dataset_and_query_results_identical(self):
        self.assertTrue((ROOT / 'benchmarks' / 'overlap.py').exists(), 'benchmark implementation missing')
        module = importlib.import_module('benchmarks.overlap')
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as folder:
            result = module.run_case(1000, Path(folder) / 'benchmark.db', repetitions=5)
            self.assertEqual(result['rows'], 1000)
            self.assertEqual(result['count_without_index'], 1)
            self.assertEqual(result['count_with_index'], 1)
            self.assertEqual(result['fingerprint_before'], result['fingerprint_after'])
            self.assertTrue(any('SCAN' in detail for detail in result['plan_without_index']))
            self.assertTrue(any('idx_reservations_overlap' in detail for detail in result['plan_with_index']))
            self.assertGreater(result['median_without_index_ms'], 0)
            self.assertGreater(result['median_with_index_ms'], 0)
            self.assertEqual(len(result['samples_with_index_ms']), 5)
            app = importlib.import_module('app')
            with closing(app.connect(Path(folder) / 'benchmark.db')) as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0], 1000)
                conflicts = db.execute('''SELECT COUNT(*) FROM reservations a JOIN reservations b
                  ON a.id<b.id AND a.equipment_id=b.equipment_id AND a.start_at<b.end_at AND a.end_at>b.start_at
                  WHERE a.status IN ('confirmed','checked_out') AND b.status IN ('confirmed','checked_out')''').fetchone()[0]
                self.assertEqual(conflicts, 0)
