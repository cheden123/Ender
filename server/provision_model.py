"""Download a pinned public model at BUILD time; runtime never downloads."""
import hashlib
from pathlib import Path
import shutil
import tempfile
import urllib.request

MODEL_REVISION = "5d37bb86abb584f1ff69ecb8f86ca609a3ce85ef"
MODEL_SHA256 = "6416a94903810842313614260ba6d6800edb284e4c080970cc395b917891debb"
MODEL_URL = (
    "https://huggingface.co/Masamsa/3d-print-failure-mobilevit-xxs/resolve/"
    + MODEL_REVISION + "/model.onnx"
)


def verify_model(path: Path, expected: str = MODEL_SHA256) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError("Model SHA256 mismatch; rebuild the image")


def provision() -> None:
    destination = Path(__file__).parent / "models" / "model.onnx"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        verify_model(destination)
        return
    # A failed/interrupted download must never leave a usable partial model.
    with tempfile.TemporaryDirectory(dir=destination.parent) as scratch:
        pending = Path(scratch) / "model.onnx"
        with urllib.request.urlopen(MODEL_URL, timeout=120) as response:
            with pending.open("wb") as target:
                shutil.copyfileobj(response, target)
        verify_model(pending)
        pending.replace(destination)


if __name__ == "__main__":
    provision()
