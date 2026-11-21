# Local failure classifier

Weights: Masamsa, **3d-print-failure-mobilevit-xxs**, published as Apache-2.0:
https://huggingface.co/Masamsa/3d-print-failure-mobilevit-xxs

Immutable revision: `5d37bb86abb584f1ff69ecb8f86ca609a3ce85ef`.
`model.onnx` SHA-256: `6416a94903810842313614260ba6d6800edb284e4c080970cc395b917891debb`
(4,104,856 bytes). Downloaded during container build; verified at build and startup.
No runtime Hugging Face or other cloud API is used. Weights are not stored in Git.

The stated base model is `apple/mobilevit-xx-small`, released under the
Apple Sample Code License. Both license texts are retained in `model-licenses/`.
Base model source: https://huggingface.co/apple/mobilevit-xx-small
Apple license source: https://github.com/apple/ml-cvnets/blob/main/LICENSE
No Obico source code or Obico model weights are included.

Labels follow pinned `config.json` and `training_meta.json`: **0=normal,
1=failure**. The README label table contradicts these two files (its code
example agrees with them). Preprocessing follows pinned
`preprocessor_config.json`: bilinear resize shortest edge to 256, center crop
256 square, divide by 255, BGR order, float32 NCHW. No mean/std normalization.

The training dataset card currently declares no dataset license. Its images
are therefore not redistributed here. Model license metadata is the publisher's
declaration, not independent verification of every training image's provenance.
The output is a model softmax score, not a calibrated safety guarantee. Published
validation metrics have not been reproduced; actual camera/printer performance
must be checked locally before relying on alerts. No automatic printer commands
should be triggered by this score.
