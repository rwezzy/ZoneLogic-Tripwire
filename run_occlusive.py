"""Launch the separate Occlusive Logic workspace with compound Boolean rules."""
import argparse
import os
import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8092")))
    parser.add_argument("--data-dir", default=os.getenv("OCCLUSIVE_DATA_DIR", "data/occlusive"))
    args = parser.parse_args()
    from occlusive.app import create_app
    uvicorn.run(create_app(args.data_dir), host=args.host, port=args.port)
