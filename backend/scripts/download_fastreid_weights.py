"""Download and validate the official FastReID Market1501 BoT R50 weights."""

from pathlib import Path
from urllib.request import urlretrieve

import torch

from app.config import FASTREID_WEIGHTS_PATH

URL = (
    "https://github.com/JDAI-CV/fast-reid/releases/download/"
    "v0.1.1/market_bot_R50.pth"
)


def main():
    destination = Path(FASTREID_WEIGHTS_PATH)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")

    if destination.is_file():
        try:
            checkpoint = torch.load(
                destination,
                map_location="cpu",
                weights_only=False,
            )
            if "model" in checkpoint:
                print(f"FastReID weights already ready: {destination}")
                return
        except Exception:
            pass

    print(f"Downloading official FastReID weights to {destination}")
    urlretrieve(URL, temporary)
    checkpoint = torch.load(temporary, map_location="cpu", weights_only=False)
    if "model" not in checkpoint:
        raise RuntimeError("Downloaded file is not a FastReID checkpoint")
    temporary.replace(destination)
    print("FastReID weights ready")


if __name__ == "__main__":
    main()
