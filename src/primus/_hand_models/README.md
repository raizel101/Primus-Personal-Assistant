# OpenCV Zoo hand inference

`mp_palmdet.py` and `mp_handpose.py` are OpenCV Zoo inference wrappers from commit `25f423d0e04c31a17254620e58febd7386da523b`:

- https://github.com/opencv/opencv_zoo/tree/25f423d0e04c31a17254620e58febd7386da523b/models/palm_detection_mediapipe
- https://github.com/opencv/opencv_zoo/tree/25f423d0e04c31a17254620e58febd7386da523b/models/handpose_estimation_mediapipe

The optional setup downloads them together with the repository license and each model's license. The original model SHA-256 values are pinned and verified by `scripts/gesture-setup.py`. Inference uses OpenCV DNN on the CPU with local ONNX files; the MediaPipe Python runtime is not installed.

PRIMUS adapts the palm wrapper's NMS call to pass x/y/width/height boxes as OpenCV requires. The installer applies the same small patch. The hand wrapper remains unmodified.
