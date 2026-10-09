"""Copy an existing workspace once, without modifying its database or media."""
import argparse
import json
from pathlib import Path
import shutil
import sqlite3


def copy_workspace(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    database = source / "zonelogic.sqlite3"
    if not database.is_file():
        raise ValueError("Original database not found")
    if destination.exists():
        raise ValueError("Destination already exists. Nothing was overwritten; use another new destination.")
    destination.mkdir(parents=True)
    # SQLite online backup gives a coherent snapshot even if the original is open.
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as original:
        with sqlite3.connect(destination / "zonelogic.sqlite3") as target:
            original.backup(target)
    (destination / "media").mkdir()
    with sqlite3.connect(destination / "zonelogic.sqlite3") as target:
        for source_id, raw in target.execute("SELECT id,payload FROM sources").fetchall():
            payload = json.loads(raw)
            media = Path(payload.get("media_path", ""))
            if media.is_file():
                copied = destination / "media" / media.name
                shutil.copy2(media, copied)
                payload["media_path"] = str(copied)
                target.execute("UPDATE sources SET payload=? WHERE id=?", (json.dumps(payload), source_id))
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="data")
    parser.add_argument("--destination", default="data/occlusive")
    args = parser.parse_args()
    try:
        print("Copied workspace to", copy_workspace(args.source, args.destination))
        print("Original files unchanged. Start run_occlusive.py with this --data-dir.")
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(2, str(exc) + "\n")
