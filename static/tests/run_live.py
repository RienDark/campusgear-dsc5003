"""Exercise actual frontend JS against backend in an isolated scratch database."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import app  # noqa: E402


def main():
    scratch = Path(os.environ.get('TMPDIR') or (Path.home() / 'AppData/Local/hermes/cache/scratch'))
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='campusgear-frontend-', dir=scratch) as folder:
        database = Path(folder) / 'frontend.db'
        app.create_database(database)
        server = app.make_server(database, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            result = subprocess.run(['node', str(ROOT / 'static/tests/live.cjs'),
                                     f'http://127.0.0.1:{server.server_port}'], check=False)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
        return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
