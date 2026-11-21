# Inference checks (2026-11-22)

Actual pinned ONNX inference ran on Windows Python 3.11 with the versions in
`requirements-vision.txt`. `python -m unittest test_vision -v`: 3 passed.
Container build and Raspberry Pi performance were not tested on this host.

Official `transformers==4.57.3` MobileViTImageProcessor, loading the pinned
`preprocessor_config.json` locally, produced exactly the same failure score
as `Classifier.score` on the Makerbot image below (absolute difference 0.0).
Transformers is a verification-only dependency, not needed at runtime.

Two independently sourced real failure photos:

| Source | License / creator | Failure score |
|---|---|---:|
| https://commons.wikimedia.org/wiki/File:Makerbot_printing_problems.jpg | CC BY-SA 3.0, SuperBlobMonster | 0.630989 |
| https://commons.wikimedia.org/wiki/File:Spaghetti_monster.jpg | CC0, A7N8X | 0.408306 |

Photos were downloaded only into local test scratch space and are not shipped.
The second image is a real false negative at threshold 0.5. This is an important
limitation; neither temporal voting nor a higher threshold fixes such a miss.

A small balanced smoke set used the first five examples of each label in the
public HF dataset viewer's **test** split, pinned dataset revision
`ba225faf621796f827e2b660274a85407c0180e6`. Viewer image bytes were processed
in memory and not saved or redistributed (the dataset declares no license).
One connection reset required a second attempt. All ten examples completed
across these two attempts:

| Test row | Publisher label | Failure score |
|---:|---|---:|
| 0 | normal | 0.254399 |
| 1 | normal | 0.255496 |
| 2 | normal | 0.325676 |
| 3 | normal | 0.203308 |
| 4 | normal | 0.312028 |
| 6 | failure | 0.680938 |
| 7 | failure | 0.891673 |
| 13 | failure | 0.582709 |
| 16 | failure | 0.705976 |
| 20 | failure | 0.886313 |

Threshold 0.5 separates this tiny publisher test sample. Threshold 0.8 misses
three of its five failures and both Commons photos. Use 0.5 as an initial
alert-only setting with temporal confirmation, then tune using actual camera
data. This sample is too small, and too close to the publisher's training
domain, to establish general accuracy, calibration, or Ender-specific behavior.
