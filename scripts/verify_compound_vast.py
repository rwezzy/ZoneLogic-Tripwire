"""Read-only audit of saved compound rules against actual imported VAST frames."""
import argparse
import json
from pathlib import Path
import sqlite3
from occlusive.compound import CompoundEngine


def verify(directory):
    database = Path(directory).resolve() / "zonelogic.sqlite3"
    reports = []
    if not database.is_file():
        return {"verified_positive_and_negative": False, "reason": "No local Occlusive Logic database or imported VAST clips are available", "sources": []}
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        sources = [json.loads(raw) for (raw,) in db.execute("SELECT payload FROM sources")]
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "compound_configurations" not in tables:
            return {"verified_positive_and_negative": False, "reason": "Save a compound rule in Occlusive Logic first", "sources": []}
        for source in sources:
            if source.get("kind") != "vast":
                continue
            config = db.execute("SELECT payload FROM configurations WHERE source_id=?", (source["id"],)).fetchone()
            compound = db.execute("SELECT payload FROM compound_configurations WHERE source_id=?", (source["id"],)).fetchone()
            if not config or not compound:
                continue
            rules = json.loads(compound[0])["rules"]
            engine = CompoundEngine(json.loads(config[0])["zones"], rules)
            samples = {rule["id"]: {"rule_id": rule["id"], "name": rule["name"], "positive": None, "negative": None, "event_times": [],
                "plain_and": rule["operator"] == "AND" and not rule["a"].get("negate") and not rule["b"].get("negate")} for rule in rules}
            for frame in source["frames"]:
                events = engine.process(frame)
                for value in engine.values:
                    key = "positive" if value["result"] is True else "negative" if value["result"] is False else None
                    item = samples[value["rule_id"]]
                    if key and item[key] is None:
                        item[key] = {"timestamp": frame["timestamp"], "a": value["a"], "b": value["b"], "result": value["result"], "detections": frame["detections"]}
                for event in events:
                    samples[event["rule_id"]]["event_times"].append(event["timestamp"])
            reports.append({"source_id": source["id"], "source_name": source["name"], "kind": "vast", "provenance": source["provenance"],
                "video_available": Path(source.get("media_path", "")).is_file(), "frames": len(source["frames"]),
                "actual_labels": sorted({d["class_name"] for frame in source["frames"] for d in frame["detections"]}), "rules": list(samples.values())})
    verified = any(source["video_available"] and rule["plain_and"] and rule["positive"] and rule["negative"] and rule["event_times"] for source in reports for rule in source["rules"])
    return {"verified_positive_and_negative": bool(verified), "verification_scope": "Imported VAST detection-frame audit. Visually review the cited times in the app; this does not establish detector accuracy or collision risk.", "sources": reports}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data/occlusive")
    parser.add_argument("--output", default="artifacts/submission/compound-vast-verification.json")
    args = parser.parse_args()
    try:
        report = verify(args.data_dir)
        output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("VAST positive/negative frame audit:", "PASS" if report["verified_positive_and_negative"] else "NOT VERIFIED")
        print("Report:", output.resolve())
        for source in report["sources"]:
            print(source["source_id"], source["actual_labels"])
            for rule in source["rules"]:
                print(rule["name"], "positive:", (rule["positive"] or {}).get("timestamp"), "negative:", (rule["negative"] or {}).get("timestamp"), "events:", rule["event_times"])
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(2, str(exc) + "\n")
