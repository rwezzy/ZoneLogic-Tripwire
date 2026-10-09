"""Optional event meanings and haptic requests, kept separate from core rules."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


class RuleOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meaning: Literal["none", "recycling", "trash", "path", "crosswalk", "cane"] = "none"
    vibrate: StrictBool = False
    pattern: list[StrictInt] = Field(default_factory=lambda: [180, 100, 180], min_length=1, max_length=9)

    @model_validator(mode="after")
    def bounded_pattern(self):
        if any(v < 0 or v > 500 for v in self.pattern) or sum(self.pattern) > 2000:
            raise ValueError("Use a vibration pattern of at most 2 seconds, with intervals from 0 to 500 milliseconds")
        if self.vibrate and not any(self.pattern[::2]):
            raise ValueError("A vibration request needs at least one positive pulse")
        if self.meaning == "none":
            self.vibrate = False
        return self


class SourceOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=200)
    rules: dict[str, RuleOptions] = Field(default_factory=dict, max_length=100)


def rule_signature(rule, configuration):
    zone = next((zone for zone in configuration["zones"] if zone["id"] == rule["zone_id"]), None)
    payload = {"rule": rule, "zone": zone}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class OptionStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS scenario_options (source_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS scenario_annotations (incident_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            """)

    def has_options(self, source_id):
        with self.store.connect() as db:
            return db.execute("SELECT 1 FROM scenario_options WHERE source_id=?", (source_id,)).fetchone() is not None

    def read(self, source_id, configuration):
        with self.store.connect() as db:
            row = db.execute("SELECT payload FROM scenario_options WHERE source_id=?", (source_id,)).fetchone()
        if not row:
            return {}
        saved = json.loads(row[0])
        rules = {r["id"]: r for r in configuration["rules"]}
        # Changing a core rule invalidates old optional actions. They must be
        # explicitly saved against the new rule, rather than silently re-used.
        result = {}
        for rule_id, item in saved.items():
            if rule_id in rules and item.get("signature") == rule_signature(rules[rule_id], configuration):
                try:
                    result[rule_id] = RuleOptions.model_validate(item["options"]).model_dump()
                except (ValueError, KeyError, TypeError):
                    continue
        return result

    def save(self, source_id, values, configuration):
        rules = {r["id"]: r for r in configuration["rules"]}
        if any(rule_id not in rules for rule_id in values):
            raise ValueError("Save the core rule first; optional actions must refer to a rule in the selected source")
        payload = {rule_id: {"signature": rule_signature(rules[rule_id], configuration), "options": RuleOptions.model_validate(value).model_dump()}
                   for rule_id, value in values.items()}
        with self.store.connect() as db:
            db.execute("INSERT OR REPLACE INTO scenario_options VALUES (?,?)", (source_id, json.dumps(payload)))

    def save_annotation(self, incident_id, annotation):
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO scenario_annotations VALUES (?,?)", (incident_id, json.dumps(annotation)))

    def annotate_existing(self, incident):
        with self.store.connect() as db:
            row = db.execute("SELECT payload FROM scenario_annotations WHERE incident_id=?", (incident["id"],)).fetchone()
        if row:
            return {**incident, "scenario": json.loads(row[0])}
        return incident


def annotation(event, options):
    selected = RuleOptions.model_validate(options or {})
    if selected.meaning == "none":
        return None
    label = event.get("class_name", "object").capitalize()
    verb = {"enter": "entered", "dwell": "occupied", "absent": "was absent from"}.get(event.get("condition"), "was observed in")
    review = " — review placement" if event.get("severity") in {"warning", "critical"} else ""
    meanings = {
        "recycling": (f"{label} {verb} the recycling zone{review}", "Class-based sorting log using your chosen destination. Material and actual disposal are not verified."),
        "trash": (f"{label} {verb} the trash zone{review}", "Class-based sorting log using your chosen destination. Local disposal policy and actual disposal are not verified."),
        "path": (f"{label} {verb} the protected image region", "Possible obstruction for review. Physical wheelchair clearance is not measured."),
        "crosswalk": (f"{label} {verb} the crossing image region", "Attention cue only. This is not a judgment that crossing is safe or unsafe."),
        "cane": (f"{label} {verb} the forward image region", "Cane-camera concept demonstration. No distance, collision prediction, live camera, or cane hardware connection."),
    }
    headline, notice = meanings[selected.meaning]
    return {"meaning": selected.meaning, "headline": headline, "notice": notice,
            "vibration_requested": selected.vibrate, "pattern": selected.pattern,
            "physical_delivery_confirmed": False}
