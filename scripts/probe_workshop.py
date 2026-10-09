"""Probe real workshop VSS data without printing credentials or signed URLs.

Run from the repository root: python -m scripts.probe_workshop
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from zonelogic.vast import VastClient, VastError


async def probe(args: argparse.Namespace) -> int:
    async with VastClient() as client:
        if not client.configured:
            print("VAST is not configured. On your team's workshop VM, export its /config/<team>.config,")
            print("or set VSS_URL (or INGRESS_URL), VSS_USERNAME and VSS_PASSWORD in the server environment.")
            print("Workshop USERNAME/PASSWORD aliases are accepted. Do not paste secrets into source files.")
            return 2
        print("VSS credentials configured. Checking authentication, retrieval and stored detections...")
        try:
            choices = [{"source": args.source}] if args.source else await client.list_videos(location=args.location or None, limit=args.limit)
            if not choices:
                print("No indexed segments matched this location. Use --location '' to browse all locations,")
                print("or use a location returned by your team's VSS metadata filter catalog.")
                return 2
            print(f"Retrieved {len(choices)} segment candidate(s).")
            for choice in choices:
                try:
                    clip = await client.import_clip(choice["source"])
                except VastError as exc:
                    if exc.status_code in (404, 422) and not args.source:
                        print(f"Skipping unavailable or incompatible sidecar: {exc}")
                        continue
                    raise
                video = await client.fetch_clip(choice["source"])
                output = Path(args.output).resolve()
                output.mkdir(parents=True, exist_ok=True)
                stem = clip["id"]  # SHA256-derived, never an upstream filename/path.
                video_path = output / f"{stem}.mp4"
                document_path = output / f"{stem}.json"
                document = {key: clip[key] for key in ("width", "height", "duration", "frames", "provenance")}
                video_path.write_bytes(video)
                document_path.write_text(json.dumps(document, indent=2, allow_nan=False), encoding="utf-8")
                classes = sorted({d["class_name"] for f in clip["frames"] for d in f["detections"]})
                times = [f["timestamp"] for f in clip["frames"]]
                print(f"PASS: {clip['width']} x {clip['height']}, {clip['duration']:.3f}s, {len(times)} detection frames.")
                print(f"Segment-local timing: {times[0]:.4f}s to {times[-1]:.4f}s.")
                print("Classes: " + (", ".join(classes) if classes else "none detected (valid empty frames)"))
                print(f"Downloaded video: {len(video):,} bytes.")
                print(f"Video saved: {video_path}")
                print(f"Detection document saved: {document_path}")
                print("Import both files through the app's video + detections upload to replay this real segment.")
                return 0
            print("No candidate had a usable stored sidecar. Select another location or enable stored YOLO frames for a segment.")
            return 2
        except VastError as exc:
            print(f"VAST probe failed: {exc}")
            return 2
        except OSError:
            print("The probe retrieved data but could not save it. Choose a writable --output directory.")
            return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location", default="warehouse3", help="Existing VSS location value (default: warehouse3; empty string lists all)")
    parser.add_argument("--limit", type=int, default=10, help="Maximum segment candidates to inspect (1-100)")
    parser.add_argument("--source", help="Optional exact s3:// segment source, bypassing browse")
    parser.add_argument("--output", default="data/probe", help="Output directory for the real MP4 and normalized JSON")
    return asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
