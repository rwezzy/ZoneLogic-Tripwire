"""Create a credential-free ZIP for transferring this app to the workshop VM."""
from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/ZoneLogic-workshop.zip"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = [root / name for name in ("README.md", "requirements.txt", "requirements-dev.txt", "run.py", "Dockerfile", ".dockerignore", ".gitignore", ".env.example")]
    for directory, patterns in (("zonelogic", ["*.py", "*.html", "*.css", "*.js"]), ("scripts", ["*.py", "*.cjs"]), ("tests", ["*.py"])):
        for pattern in patterns:
            files.extend((root / directory).rglob(pattern))
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for file in sorted(set(files)):
            if file.is_file() and "__pycache__" not in file.parts:
                archive.write(file, "ZoneLogic/" + file.relative_to(root).as_posix())
    print(f"Created {output} ({output.stat().st_size:,} bytes). No media, credentials, incident data, or downloaded references included.")


if __name__ == "__main__":
    main()
