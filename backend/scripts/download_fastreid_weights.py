"""Download the FastReID Market1501 BoT R50 checkpoint used by person tracking."""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

URL = "https://github.com/JDAI-CV/fast-reid/releases/download/v0.1.1/market_bot_R50.pth"
SHA256 = "764fa8ca18117e2f52102791334c852d1c2e5e0b7c727e3f271c16893f4184aa"
TARGET = Path(__file__).resolve().parents[1] / "weights" / "market_bot_R50.pth"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    if TARGET.is_file() and sha256(TARGET) == SHA256:
        print(f"FastReID weights already verified: {TARGET}")
        return

    temp_path = None
    try:
        with urllib.request.urlopen(URL, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            with tempfile.NamedTemporaryFile(
                dir=TARGET.parent,
                prefix="market_bot_R50.",
                suffix=".tmp",
                delete=False,
            ) as output:
                temp_path = Path(output.name)
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        print(f"\rDownloading FastReID: {downloaded * 100 // total}%", end="", flush=True)
        print()
        if sha256(temp_path) != SHA256:
            raise RuntimeError("FastReID checkpoint checksum mismatch")
        os.replace(temp_path, TARGET)
        print(f"FastReID weights installed: {TARGET}")
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


if __name__ == "__main__":
    main()
