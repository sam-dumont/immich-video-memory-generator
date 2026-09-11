"""One quality target, calibrated per encoder against measured SSIM and the eye.

`crf` is the portable dial the config exposes, and it means one specific thing:
**the libx265 CRF that expresses the requested quality**. libx265 is the
reference because it is the only encoder present on every machine, so a number
on its scale is the one number that always has a meaning. Every other family is
calibrated to reproduce *that picture*, not to copy that number.

Each family is pinned by two measured anchors — its setting at the `high` target
and at the `balanced` target — and interpolated between them. There is
deliberately no shared offset: the five families need five different slopes
(+1.00, +0.67, +0.33, +0.67 and -1.67 per reference CRF step), so any single
constant would silently give each encoder a different picture. That is what the
previous mapping did, and before it three of the five families got no
rate-control flag at all and the driver decided.

**How the targets were set.** SSIM fixes the rough level, but the balanced anchor
was confirmed by eye, on gradients, against a deliberately lower set: the owner
compared them and kept the one where the gradients still dither rather than
breaking into blocks. That criterion matters more than the score here, because
SSIM barely punishes banding, and banding on sky, walls and skin is the first
thing a viewer notices in a memory film. Where a family is known to band its
anchor takes the safer side of the SSIM target, and if any family bands on real
content its anchor should move up even when its SSIM looks fine.

Measurements: 20 s of 1080p60 from the owner's library, SSIM against that source,
on the hardware named beside each anchor. One caveat carried deliberately: the
VideoToolbox source is itself a q75 VideoToolbox encode, so re-encoding through
the same encoder at very high quality is near-idempotent and inflates the top of
that column — `q 87` scoring 0.99959 is an artefact, not evidence that q 87 buys
anything. The middle of the range, which is all these presets use, is unaffected.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The two reference points the whole table is pinned to, on libx265's CRF scale.
#
#   high      CRF 18   SSIM 0.99169   4.6 Mbps   "the same picture as the source"
#   balanced  CRF 24   SSIM 0.98451   1.6 Mbps   approved by eye on gradients
#
# `high` used to mean CRF 12. SSIM is already past 0.999 by CRF 18, so CRF 12
# bought nothing visible while asking VideoToolbox for 76 Mbps — which is where
# 645 MB two-minute films came from. It was never a quality choice.
_HIGH_REFERENCE_CRF = 18
_BALANCED_REFERENCE_CRF = 24

# family -> (setting at CRF 18, setting at CRF 24), both measured.
#
#   libx265        the reference itself.
#   libx264        CRF 18 scores 0.99011 on the same film, landing on the high
#                  target directly; balanced is four steps down, the same span
#                  NVENC needs, both being AVC.
#   h264_vaapi     J4125 / Gemini Lake, `-rc_mode CQP`: QP 20 -> 0.98946,
#                  QP 22 -> 0.98470, QP 24 -> 0.98080. Matching libx264 costs
#                  about 2.2x the bits.
#   h264_nvenc     T1000 / Turing, driver 570.144, `-rc constqp`: QP 20 ->
#                  0.98944, QP 22 -> 0.98731, QP 24 -> 0.98433. Matching libx264
#                  costs only 1.2x the bits; Turing is simply a better encoder.
#                  That its balanced anchor shares a number with libx264's is a
#                  coincidence of two scales, not a constant they share.
#   videotoolbox   Apple Silicon: q 65 -> 0.99060 at 13.2 Mbps, q 55 -> 0.98366
#                  at 6.5. About 2.9x the bits of libx265 for the same picture,
#                  the steepest tax of the three, and still worth taking —
#                  libx265 `-preset medium` runs at 2.6x realtime here against
#                  VideoToolbox's 8.2x.
_CALIBRATION: dict[str, tuple[int, int]] = {
    "libx265": (18, 24),
    "libx264": (18, 22),
    "vaapi": (20, 22),
    "qsv": (20, 22),
    "nvenc": (20, 24),
    "videotoolbox": (65, 55),
}

# QSV is the one family without its own sweep: the owner's Intel box drives QSV
# and VAAPI through the same iHD driver on the same silicon, and the pipeline
# picks VAAPI first, so VAAPI's anchors are carried over rather than invented.
_DERIVED_FROM_VAAPI = "qsv"

_QUANTISER_RANGE = (0, 51)
_VIDEOTOOLBOX_RANGE = (1, 100)


def _calibrated(family: str, crf: int) -> int:
    """The family's own setting for a point on the reference CRF scale."""
    at_high, at_balanced = _CALIBRATION[family]
    slope = (at_balanced - at_high) / (_BALANCED_REFERENCE_CRF - _HIGH_REFERENCE_CRF)
    value = round(at_high + slope * (crf - _HIGH_REFERENCE_CRF))
    low, high = _VIDEOTOOLBOX_RANGE if family == "videotoolbox" else _QUANTISER_RANGE
    return max(low, min(high, value))


def quality_args(encoder: str, crf: int) -> list[str]:
    """Constant-quality arguments that make `crf` mean one picture everywhere.

    Returns the empty list only for encoders with no quality dial at all (ProRes
    picks a profile instead), and logs anything unrecognised rather than letting
    a backend fall through to its driver default in silence.
    """
    if encoder in ("libx265", "libx264"):
        return ["-crf", str(_calibrated(encoder, crf))]
    if encoder.endswith("_videotoolbox"):
        return ["-q:v", str(_calibrated("videotoolbox", crf))]
    if encoder.endswith("_vaapi"):
        # `-qp` alone is ignored unless CQP is selected; the default rc_mode is
        # the driver's choice, which is how VAAPI came to ignore the dial.
        return ["-rc_mode", "CQP", "-qp", str(_calibrated("vaapi", crf))]
    if encoder.endswith("_qsv"):
        return ["-global_quality", str(_calibrated(_DERIVED_FROM_VAAPI, crf))]
    if encoder.endswith("_nvenc"):
        return ["-rc", "constqp", "-qp", str(_calibrated("nvenc", crf))]
    if encoder.startswith("prores"):
        return []
    logger.warning(
        "No rate-control mapping for %s; the driver's default decides quality, "
        "not the configured CRF %d",
        encoder,
        crf,
    )
    return []
