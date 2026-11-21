"""Focused contracts; optional real-model smoke: python -m unittest test_vision."""
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from provision_model import verify_model
from vision import Classifier


class VisionTests(unittest.TestCase):
    def test_integrity_rejects_corrupt_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.onnx"
            path.write_bytes(b"broken")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                verify_model(path)
            verify_model(path, hashlib.sha256(b"broken").hexdigest())

    def test_failure_label_and_preprocessing(self):
        with patch("vision.verify_model"), patch("vision.ort.InferenceSession") as session:
            session.return_value.run.return_value = [np.array([[0., 2.]], dtype=np.float32)]
            stream = BytesIO()
            Image.new("RGB", (640, 480), (255, 0, 0)).save(stream, format="PNG")
            probability = Classifier().score(stream.getvalue())
            self.assertAlmostEqual(probability, 0.880797, places=5)
            tensor = session.return_value.run.call_args.args[1]["pixel_values"]
            self.assertEqual(tensor.shape, (1, 3, 256, 256))
            self.assertEqual(tensor.dtype, np.float32)
            np.testing.assert_array_equal(tensor[0, :, 0, 0], [0., 0., 1.])

    def test_invalid_images_are_rejected(self):
        with patch("vision.verify_model"), patch("vision.ort.InferenceSession"):
            classifier = Classifier()
            with self.assertRaises(OSError):
                classifier.score(b"not an image")


if __name__ == "__main__":
    unittest.main()
