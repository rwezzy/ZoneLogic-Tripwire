"""Run locally with `python run.py`; use PORT=8080 on the workshop VM."""
import os
import argparse

import uvicorn


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start the ZoneLogic monitoring dashboard")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    args = parser.parse_args()
    uvicorn.run("zonelogic.main:app", host=args.host, port=args.port)
