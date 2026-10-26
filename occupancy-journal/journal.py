"""Read-only HA experiment journal. No service calls, MQTT writes, or credentials on disk."""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import html
import os
import re
from pathlib import Path
import signal
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

DATA = Path(os.environ.get("JOURNAL_DATA", "/data"))
DB = DATA / "journal.sqlite3"
FORECAST = ""
FORECAST_ADDON = ""
HA = "http://supervisor/core/api/states"
RESIDENTS = {}
FORECAST_MEMBERS = []
EXTRA_ENTITIES = set()
ATTRS = {
    "friendly_name", "source", "device_trackers", "gps_accuracy", "battery_level",
    "source_type", "current_temperature", "current_humidity", "temperature",
    "target_temp_low", "target_temp_high", "hvac_action", "preset_mode",
    "fan_mode", "unit_of_measurement", "options", "supported_features",
    "direction", "distance", "next_event", "reason", "status",
}
STOP = threading.Event()
RUNTIME = {"last_cycle": None, "errors": {}}
LOCK = threading.Lock()
RETENTION = 90


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    db = sqlite3.connect(DB, timeout=15)
    db.execute("PRAGMA busy_timeout=15000")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize():
    DATA.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY, captured_at TEXT NOT NULL, kind TEXT NOT NULL, digest TEXT NOT NULL, payload BLOB NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS records_time ON records(captured_at)")
        db.execute("CREATE INDEX IF NOT EXISTS records_kind_id ON records(kind,id)")
        db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    os.chmod(DB, 0o600)


def encode(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest(), gzip.compress(raw, compresslevel=3, mtime=0)


def append(db, kind, payload, captured_at, deduplicate=False):
    digest, compressed = encode(payload)
    if deduplicate:
        last = db.execute("SELECT digest FROM records WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
        if last and last[0] == digest:
            return False
    db.execute("INSERT INTO records(captured_at,kind,digest,payload) VALUES(?,?,?,?)", (captured_at, kind, digest, compressed))
    return True


def select_states(states):
    """Explicit scope and attribute allowlist: never store GPS coordinates or user IDs."""
    out = {}
    for state in states:
        entity = state["entity_id"]
        wanted = (entity.startswith(("person.", "device_tracker.", "sensor.velair_"))
                  or entity in {"select.velair_mode", "switch.velair_automatic_scheduling",
                                "sensor.forecast_outdoor_temperature"} or entity in EXTRA_ENTITIES)
        if wanted:
            out[entity] = {key: state.get(key) for key in ("state", "last_changed", "last_updated", "last_reported")}
            out[entity]["attributes"] = {k: v for k, v in state.get("attributes", {}).items() if k in ATTRS}
    return out


def get_json(url, authenticated=False):
    headers = {"Accept": "application/json"}
    if authenticated:
        headers["Authorization"] = "Bearer " + os.environ["SUPERVISOR_TOKEN"]
    # GET only; do not accept URLs from HTTP clients or add-on options.
    with urlopen(Request(url, headers=headers, method="GET"), timeout=20) as response:
        return json.load(response)


def sample():
    captured = now()
    errors = {}
    results = []
    for kind, url, auth in [("forecast", FORECAST + "/api/forecast", False),
                            ("observations", HA, True),
                            ("forecast_status", FORECAST + "/api/status", False)]:
        try:
            value = get_json(url, auth)
            if kind == "observations":
                value = select_states(value)
            elif kind == "forecast_status":
                # Persist training/collection context without copying arbitrary error strings.
                value = {k: value.get(k) for k in (
                    "status", "model_version", "source", "history", "days_until_training", "usable_presence_days",
                    "horizons_shipping", "people", "served_by", "model_kind", "best_baseline",
                    "eta_models", "worker", "loaded_at", "last_collect", "last_predict",
                    "last_train", "last_train_seconds", "next_train", "train_cadence",
                    "training_started_at", "training_in_progress", "code")} | {
                        "mqtt_connected": value.get("mqtt", {}).get("connected"),
                        "listener_connected": value.get("listener", {}).get("connected")}
            results.append((kind, value, now()))
        except Exception as exc:
            # Exception strings can include URLs/headers. Record only a class and HTTP status.
            errors[kind] = {"error": type(exc).__name__, "http_status": getattr(exc, "code", None)}
    with connect() as db:
        for kind, value, received in results:
            append(db, kind, value, received, deduplicate=(kind == "forecast"))
        append(db, "collection_health", {"errors": errors}, now(), deduplicate=True)
        db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('last_cycle',?)", (captured,))
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=RETENTION)).isoformat(timespec="seconds")
        db.execute("DELETE FROM records WHERE captured_at < ?", (cutoff,))
    with LOCK:
        RUNTIME.update(last_cycle=captured, errors=errors)


def worker():
    previous_errors = None
    heartbeat = 0.0
    while not STOP.is_set():
        begin = time.monotonic()
        try:
            sample()
            with LOCK:
                errors = RUNTIME["errors"].copy()
            if errors != previous_errors or begin - heartbeat > 3600:
                print(json.dumps({"at": now(), "event": "collection", "errors": errors}), flush=True)
                previous_errors, heartbeat = errors, begin
        except Exception as exc:
            print(json.dumps({"at": now(), "event": "journal_failure", "type": type(exc).__name__}), flush=True)
        STOP.wait(max(1, 60 - (time.monotonic() - begin)))


def status():
    with connect() as db:
        totals = {kind: count for kind, count in db.execute("SELECT kind,COUNT(*) FROM records GROUP BY kind")}
        span = db.execute("SELECT MIN(captured_at),MAX(captured_at) FROM records").fetchone()
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat(timespec="seconds")
        recent = db.execute("SELECT COUNT(*) FROM records WHERE kind='observations' AND captured_at>=?", (cutoff,)).fetchone()[0]
        observed_span = db.execute("SELECT MIN(captured_at),MAX(captured_at),COUNT(DISTINCT substr(captured_at,1,10)) FROM records WHERE kind='observations'").fetchone()
        latest = {}
        for kind in ("forecast", "observations", "forecast_status", "collection_health"):
            row = db.execute("SELECT captured_at,payload FROM records WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
            latest[kind] = None if row is None else {"captured_at": row[0], "value": json.loads(gzip.decompress(row[1]))}
    with LOCK:
        runtime = dict(RUNTIME)
    observations = (latest.get("observations") or {}).get("value") or {}
    tracker_checks = {}
    for name, entity in RESIDENTS.items():
        person = observations.get(entity) or {}
        trackers = person.get("attributes", {}).get("device_trackers") or []
        states = {t: observations[t]["state"] for t in trackers
                  if (observations.get(t) or {}).get("state") not in
                  (None, "unknown", "unavailable", "")}
        tracker_checks[name] = {
            "available": len(states),
            "disagree": len({s == "home" for s in states.values()}) > 1,
            "entity_present": entity in observations,
        }
    return {"version": "1.0.5", "retention_days": RETENTION, "sample_seconds": 60,
            "counts": totals, "first_record": span[0], "last_record": span[1],
            "observation_history": {"first": observed_span[0], "last": observed_span[1],
                                    "calendar_days": observed_span[2], "samples_last_24h": recent,
                                    "expected_samples_24h": 1440},
            "tracker_checks": tracker_checks,
            "bytes": sum(p.stat().st_size for p in DATA.glob("journal.sqlite3*")),
            "runtime": runtime, "latest": latest, "residents": RESIDENTS,
            "forecast_members": FORECAST_MEMBERS,
            "forecast_panel": "/" + FORECAST_ADDON if FORECAST_ADDON else None,
            "missing_inputs": [name for name in RESIDENTS if name not in FORECAST_MEMBERS]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, payload, content_type="application/json"):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            with LOCK:
                last = RUNTIME["last_cycle"]
                errors = dict(RUNTIME["errors"])
            alive = last is not None and (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(last)).total_seconds() < 180
            return self.send(200 if alive else 503, {"worker_alive": alive, "last_cycle": last, "source_errors": errors})
        # Home Assistant Supervisor is the only external web entry. No LAN port is published.
        if self.client_address[0] not in {"172.30.32.2", "127.0.0.1", "::1"}:
            return self.send(403, {"error": "Use the Home Assistant sidebar"})
        if path in ("", "/"):
            return self.send(200, Path("/app/index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/status":
            return self.send(200, status())
        if path in ("/export.jsonl", "/export-view"):
            preview = path == "/export-view"
            query = parse_qs(urlsplit(self.path).query)
            try:
                days = max(1, min(RETENTION, int(query.get("days", ["7"])[0])))
            except ValueError:
                return self.send(400, {"error": "days must be an integer"})
            cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat(timespec="seconds")
            self.send_response(200)
            # Display plain text inside HA ingress; forced attachment navigation
            # can be blocked by browsers when initiated from the embedded panel.
            self.send_header("Content-Type", "text/html; charset=utf-8" if preview else "text/plain; charset=utf-8")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                if preview:
                    self.wfile.write(b'<!doctype html><html lang="en"><meta charset="utf-8"><title>Occupancy history export</title><body><a href="./">Return to history</a><p>JSONL records below. Select and copy the records to save as a .jsonl file.</p><pre style="white-space:pre-wrap;overflow-wrap:anywhere">')
                with connect() as db:
                    for identifier, captured, kind, compressed in db.execute("SELECT id,captured_at,kind,payload FROM records WHERE captured_at>=? ORDER BY id", (cutoff,)):
                        record = {"id": identifier, "captured_at": captured, "kind": kind, "payload": json.loads(gzip.decompress(compressed))}
                        line = json.dumps(record, separators=(",", ":"))
                        self.wfile.write((html.escape(line) if preview else line).encode() + b"\n")
                if preview:
                    self.wfile.write(b'</pre></body></html>')
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        return self.send(404, {"error": "Not found"})


def main():
    global RETENTION, FORECAST, FORECAST_ADDON, RESIDENTS, FORECAST_MEMBERS, EXTRA_ENTITIES
    os.umask(0o077)
    options = json.loads((DATA / "options.json").read_text()) if (DATA / "options.json").exists() else {}
    RETENTION = max(30, min(365, int(options.get("retention_days", 90))))
    FORECAST_ADDON = options.get("forecast_addon", "")
    if FORECAST_ADDON and not re.fullmatch(r"[a-z0-9_]+", FORECAST_ADDON):
        raise ValueError("forecast_addon must be a Supervisor add-on slug")
    FORECAST = "http://" + FORECAST_ADDON.replace("_", "-") + ":8099" if FORECAST_ADDON else ""
    RESIDENTS = {row["name"]: row["entity_id"] for row in options.get("residents", [])}
    FORECAST_MEMBERS = [row["name"] for row in options.get("residents", []) if row.get("forecast", False)]
    EXTRA_ENTITIES = set(options.get("extra_entities", []))
    initialize()
    thread = threading.Thread(target=worker, name="journal-collector", daemon=True)
    thread.start()
    server = ThreadingHTTPServer(("0.0.0.0", 8099), Handler)
    server.timeout = 1
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: STOP.set())
    while not STOP.is_set():
        server.handle_request()
    server.server_close()
    thread.join(timeout=45)
    with connect() as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


if __name__ == "__main__":
    main()
