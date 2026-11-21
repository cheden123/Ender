"""Offline MobileViT inference. Model provenance is in MODEL-NOTICE.md."""
from io import BytesIO
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageOps

from provision_model import MODEL_SHA256, verify_model


class Classifier:
    def __init__(self):
        path = Path(__file__).parent / "models" / "model.onnx"
        verify_model(path, MODEL_SHA256)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        self.session = ort.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )

    def score(self, jpeg: bytes) -> float:
        with Image.open(BytesIO(jpeg)) as source:
            # Bound decompressed input before converting/copying it.
            if source.width * source.height > 16_000_000:
                raise ValueError("Camera image exceeds 16 megapixels")
            image = ImageOps.exif_transpose(source).convert("RGB")
        width, height = image.size
        # Match MobileViTImageProcessor: bilinear shortest-edge resize,
        # center crop, rescale to [0,1], RGB -> BGR, batch/CHW layout.
        if width <= height:
            size = (256, int(height * 256 / width))
        else:
            size = (int(width * 256 / height), 256)
        image = image.resize(size, Image.Resampling.BILINEAR)
        left, top = (size[0] - 256) // 2, (size[1] - 256) // 2
        image = image.crop((left, top, left + 256, top + 256))
        pixels = np.asarray(image).astype(np.float32) / np.float32(255)
        pixels = np.ascontiguousarray(pixels[:, :, ::-1].transpose(2, 0, 1)[None])
        logits = np.asarray(self.session.run(["logits"], {"pixel_values": pixels})[0])
        if logits.shape != (1, 2) or not np.isfinite(logits).all():
            raise ValueError("Classifier returned invalid logits")
        probabilities = np.exp(logits[0] - logits[0].max())
        # config.json AND training_meta.json specify failure=1. The model
        # card's table accidentally reverses these labels; do not use it.
        return float(probabilities[1] / probabilities.sum())
