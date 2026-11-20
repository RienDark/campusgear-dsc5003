# CampusGear

A local equipment reservation and loan application for a database systems project.
All identities and equipment are synthetic. This is a no-authentication teaching prototype, not a deployed campus service.

## Demonstration

- Source: https://github.com/RienDark/campusgear-dsc5003
- Video (6 min 41 sec, English AI-generated narration): https://github.com/RienDark/campusgear-dsc5003/releases/download/v1.0.0/CampusGear-demo.mp4
- Release page: https://github.com/RienDark/campusgear-dsc5003/releases/tag/v1.0.0
- English subtitles: video/CampusGear-demo.en.srt

The video records real browser operations against the application. No human group membership or individual student contributions are claimed by this repository.

## Run

Python 3.11+ recommended. Application runtime has no third-party dependencies.
Tested on Python 3.11.16 and 3.14.7; intended compatibility starts at 3.10, but 3.10 was not exercised.

```sh
python app.py --port 8765 --db data/campusgear.db
```

Open http://127.0.0.1:8765 in a browser. Stop with Ctrl+C.
The application creates the SQLite database and synthetic reference data automatically. Reuse that path to preserve bookings, or choose a new database file for a clean demonstration.

On Windows, START_WINDOWS.cmd is an optional launcher. Keep its terminal open while using the application.

## Workflow

1. Search the catalogue by equipment name, asset tag or location, optionally filtering by category.
2. Choose an available item and synthetic member. Enter an explicit UTC interval and purpose.
3. Create a confirmed reservation. An overlapping active reservation on the same equipment returns a conflict.
4. Cancel a confirmed reservation, or check it out and then return it.
5. Inspect live analytics, table/index definitions and the audit trail.

Intervals are half-open [start,end), so adjacent bookings are legal. Equipment condition is separate from time-slot availability. Early checkout and historical bookings are permitted for demonstration. A member selector is not authentication.

## Design

Browser HTML/CSS/JavaScript → Python ThreadingHTTPServer JSON API → per-request sqlite3 connection → WAL-mode SQLite file.

Seven relations: departments, members, categories, equipment, reservations, loans and audit_log. See docs/er-model.svg.

- Foreign keys are enabled for every connection; unique and CHECK constraints protect structural rules.
- INSERT/UPDATE triggers reject active interval overlap; another trigger validates reservation state transitions.
- BEGIN IMMEDIATE encloses supported mutation/loan/audit changes atomically.
- A partial unique index permits at most one checked-out reservation per item.
- The API validates real canonical UTC timestamps. Direct SQL administration is trusted and can bypass some application invariants.
- Values use bound SQL parameters. Dynamic UI text uses textContent.
- The server listens only on 127.0.0.1, validates local Host/Origin, bounds JSON bodies, and restricts served files to static/.

## Verify

```sh
python -m unittest discover -s tests -v
node static/tests/frontend.test.cjs
python static/tests/run_live.py
```

The backend suite contains 21 tests; the frontend behaviour suite contains 11. These counts are distinct from manual or browser checks and do not imply exhaustive coverage. Node is only needed for the optional frontend tests.

Tests cover independent concurrent requests, conflicting INSERT/UPDATE, adjacent intervals, lifecycle rejection, loan/audit rollback, malformed timestamps/JSON, foreign keys, injection strings and path protection. The workflow is exercised against isolated databases and loopback servers.

## Reproduce index experiment

```sh
python benchmarks/overlap.py --repetitions 31 --db-dir data/benchmark-run --output-dir evidence/reproduced
```

Use a new database directory on each run. The deterministic generator uses seed 5003 and 1,000 equipment records. It measures the same COUNT overlap query and parameters on the same data before/after creating the target covering index. Five warmups precede 31 samples per condition. Counts and row fingerprints are checked.

| Reservations | No target index median ms | Indexed median ms | Count both |
|---:|---:|---:|---:|
| 1,000 | 0.0421 | 0.0052 | 1 |
| 10,000 | 0.4073 | 0.0058 | 1 |
| 100,000 | 64.6942 | 0.0598 | 1 |

Raw samples and actual query plans are in evidence/benchmark-results.json. Plans changed from SCAN reservations to a covering index search. The fixed measurement order, one machine, synthetic workload and cache effects limit generalization. These are query microbenchmarks, not HTTP throughput, production capacity or end-to-end speedup claims. The sharp rise at 100k is retained as observed; its cause was not isolated with hardware counters.

## API

GET /api/dashboard, /api/equipment, /api/members, /api/categories, /api/reservations, /api/analytics, /api/schema and /api/audit.

POST /api/reservations accepts exactly member_id, equipment_id, start_at, end_at and purpose. Lifecycle POST /api/reservations/<id>/cancel|checkout|return accepts an empty JSON object. Creation returns 201; actions return 200; validation/missing/conflict responses use 400/404/409.

## Limitations and attribution

No authentication/authorization, SSO, public deployment, production HTTP server, HTTPS, pagination, migrations, notifications or user study. Do not expose this service to the public internet or enter real personal data.

AI assistance was used for code, tests, benchmark, documentation and synthetic narration. Group roster, undergraduate majors and real individual contributions are deliberately excluded from this public repository and must be confirmed separately. This repository does not claim independent student authorship.

Primary implementation references:
- https://www.sqlite.org/foreignkeys.html
- https://www.sqlite.org/isolation.html
- https://www.sqlite.org/queryplanner.html
- https://sqlite.org/lang_createtrigger.html
- https://sqlite.org/partialindex.html
- https://docs.python.org/3/library/http.server.html
