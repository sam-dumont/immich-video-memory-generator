# YuNet face detector

`face_detection_yunet_2023mar.onnx` is the stock OpenCV Zoo export, copied
byte-for-byte. SHA-256:
`8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` (232,589 bytes).

Source: <https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx>

Licence: MIT, per the LICENSE file in that model directory — Copyright (c) 2020
Shiqi Yu. The surrounding opencv_zoo repository is Apache-2.0; the model itself
carries the narrower MIT notice, which is the one reproduced in
`THIRD_PARTY_NOTICES`.

It is bundled rather than downloaded because it is 227 KB and the smart-zoom
crop needs it on a machine that may never reach the network — the same reason
the title fonts ship in `titles/bundled_fonts`.

`cv2.FaceDetectorYN` runs it. It replaced the Haar cascade because OpenCV 5
ships neither `cv2.CascadeClassifier` in the main wheel nor any XML in
`cv2.data.haarcascades`, and because the cascade invented faces in tree bark:
on the six face-free CC0 fixtures in `tests/e2e/fixtures/library` it reported
four, YuNet reports none.
