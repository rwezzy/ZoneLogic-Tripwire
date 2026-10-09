"""Small durable SQLite store; one connection per operation, no global cursor."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "media").mkdir(exist_ok=True)
        self.path = directory / "zonelogic.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS configurations (source_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL, evidence BLOB, evidence_type TEXT
                );
                CREATE INDEX IF NOT EXISTS incidents_source ON incidents(source_id, created_at);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def sources(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM sources ORDER BY id")]

    def save_source(self, source):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO sources VALUES (?,?)", (source["id"], json.dumps(source)))

    def configuration(self, source_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM configurations WHERE source_id=?", (source_id,)).fetchone()
            return json.loads(row[0]) if row else {"source_id": source_id, "zones": [], "rules": []}

    def has_configuration(self, source_id):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM configurations WHERE source_id=?", (source_id,)).fetchone() is not None

    def save_configuration(self, config):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO configurations VALUES (?,?)", (config["source_id"], json.dumps(config)))

    def add_incident(self, incident):
        with self.connect() as db:
            result = db.execute("INSERT OR IGNORE INTO incidents(id,source_id,created_at,status,payload) VALUES (?,?,?,?,?)", (
                incident["id"], incident["source_id"], incident["created_at"], incident["status"], json.dumps(incident)))
            return result.rowcount > 0

    @staticmethod
    def incident_row(row):
        if row is None:
            return None
        result = json.loads(row["payload"])
        result["status"] = row["status"]
        if row["has_evidence"]:
            result["evidence_url"] = f"api/incidents/{result['id']}/evidence"
        return result

    def incidents(self, source_id=None, limit=200):
        query = "SELECT *, evidence IS NOT NULL AS has_evidence FROM incidents"
        args = []
        if source_id:
            query += " WHERE source_id=?"
            args.append(source_id)
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        args.append(limit)
        with self.connect() as db:
            return [self.incident_row(r) for r in db.execute(query, args)]

    def incident(self, incident_id):
        with self.connect() as db:
            return self.incident_row(db.execute("SELECT *, evidence IS NOT NULL AS has_evidence FROM incidents WHERE id=?", (incident_id,)).fetchone())

    def set_status(self, incident_id, status):
        with self.connect() as db:
            return db.execute("UPDATE incidents SET status=? WHERE id=?", (status, incident_id)).rowcount > 0

    def save_evidence(self, incident_id, data, content_type, timestamp):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM incidents WHERE id=?", (incident_id,)).fetchone()
            if not row:
                return False
            payload = json.loads(row[0])
            payload["evidence_timestamp"] = timestamp
            db.execute("UPDATE incidents SET evidence=?,evidence_type=?,payload=? WHERE id=?",
                       (data, content_type, json.dumps(payload), incident_id))
            return True

    def evidence(self, incident_id):
        with self.connect() as db:
            row = db.execute("SELECT evidence,evidence_type FROM incidents WHERE id=?", (incident_id,)).fetchone()
            return (row[0], row[1]) if row and row[0] else None
