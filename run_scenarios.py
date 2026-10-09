"""Start the optional scenario playground without modifying the original app."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--data-dir", default=os.getenv("ZONELOGIC_SCENARIO_DATA_DIR", "data/scenarios"))
    parser.add_argument("--disable-scenarios", action="store_true", help="Run only the original workspace using the separate playground database")
    args = parser.parse_args()
    directory = Path(args.data_dir).resolve()
    original_directory = Path(os.getenv("ZONELOGIC_DATA_DIR", "data")).resolve()
    if directory == original_directory:
        parser.error("Use a separate data directory for the optional playground; the original database is protected.")
    if args.port == 8090:
        parser.error("Port 8090 is reserved here for your working app. Choose 8091 or another port.")
    from zonelogic_scenarios.app import create_optional_app
    app = create_optional_app(directory, enabled=not args.disable_scenarios)
    print(f"Optional playground: http://{args.host}:{args.port}; separate storage: {directory}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
