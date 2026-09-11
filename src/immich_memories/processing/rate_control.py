"""One quality dial, honoured by every encoder family.

`crf` is the portable contract the config exposes, and until now only two of the
five encoder families implemented it: libx264/libx265 got `-crf`, VideoToolbox
got a `-q:v` line that was never checked against an output, and VAAPI, QSV and
NVENC got nothing at all — so the driver's default rate control decided quality,
which is neither the requested one nor the same one twice.

Every mapping below is anchored on a measurement rather than a guess, and each
anchor names the hardware it came from. The numbers come from 20 s of 1080p60
film, SSIM computed against that same source.

Constant-quality modes are used throughout, never bitrate targets: the point of
the dial is that a still frame and a fast pan cost what they need to.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# --- VAAPI -----------------------------------------------------------------
# Measured on the owner's Synology (J4125, Gemini Lake), SDR 8-bit source, with
# software on the same box as the reference:
#
#   libx264   -crf 18                17.4 MB   SSIM 0.99011
#   h264_vaapi -rc_mode CQP -qp 20   38.2 MB   SSIM 0.98946   <- closest
#   h264_vaapi -rc_mode CQP -qp 22   19.8 MB   SSIM 0.98468
#   h264_vaapi -rc_mode CQP -qp 24   14.7 MB   SSIM 0.98079
#   h264_vaapi -rc_mode CQP -qp 26   10.4 MB   SSIM 0.97561
#
# So CRF 18 is QP 20 on that chip: a +2 offset. The slope is 1:1 because CRF and
# VAAPI's QP are the same 0-51 quantiser scale. Note what the table also says:
# reaching software quality costs about 2.2x the bits, and at equal size VAAPI
# is clearly worse. That is the hardware tax, not a bug to tune away.
_VAAPI_QP_OFFSET = 2

# --- QSV -------------------------------------------------------------------
# QSV exposes ICQ through `-global_quality`, on the same 0-51 quantiser scale.
# Not separately measured: the owner's Intel box reports QSV and VAAPI over the
# same iHD driver and the pipeline picks VAAPI first, so the VAAPI offset is
# carried over rather than invented.
_QSV_QUALITY_OFFSET = 2

# --- NVENC -----------------------------------------------------------------
# PROVISIONAL: no NVIDIA box has been measured yet. NVENC's constqp QP is the
# same 0-51 scale, and NVENC is generally a little less efficient than x264/x265
# at a given QP, so it starts at the VAAPI offset. Re-anchor this constant from
# the sweep in docs/deploy/hardware/overview.md when a card is available; it is
# the only number in this module that is not a measurement.
_NVENC_QP_OFFSET = 2

# --- VideoToolbox ----------------------------------------------------------
# VideoToolbox implements no CRF, so the old mapping invented a line:
# `111 - 2*crf`, which put CRF 18 at q 75 and the default CRF 12 at q 87. The
# same 20 s clip, HDR 10-bit:
#
#   hevc_videotoolbox -q:v 75   37.3 Mbps      <- what CRF 18 used to ask for
#   hevc_videotoolbox -q:v 65   12.8 Mbps
#   libx265 -crf 18              4.6 Mbps   SSIM 0.9917
#   libx265 -crf 22              2.2 Mbps   SSIM 0.9877
#   libx265 -crf 24              1.6 Mbps   SSIM 0.9845
#
# Eight times the bits of the software encode it was supposed to match, which is
# where the owner's 645 MB two-minute films came from.
#
# Those two VideoToolbox points fix its curve at bitrate ~ e^(0.107*q), and the
# three libx265 points fix that curve at bitrate ~ e^(-0.176*crf). Anchoring on
# bits alone would be wrong — a hardware encoder needs more of them for the same
# picture — so the target is the tax the VAAPI sweep actually measured: 2.2x the
# software bitrate at matched quality. 2.2 x 4.6 Mbps = 10.1 Mbps, which the
# VideoToolbox curve reaches at q 63. Dividing the two slopes puts one CRF step
# at 1.6 q steps.
#
# The 2.2x is measured on VAAPI H.264, not on VideoToolbox HEVC, so this mapping
# is bitrate-derived with a quality assumption carried across. An SSIM sweep of
# hevc_videotoolbox against the same source would replace the assumption with a
# number; the constants below are the only two things it would change.
_VT_QUALITY_AT_CRF_18 = 63
_VT_QUALITY_PER_CRF_STEP = 1.6


def videotoolbox_quality_from_crf(crf: int) -> int:
    """Translate the portable CRF contract to VideoToolbox's 1-100 scale."""
    quality = _VT_QUALITY_AT_CRF_18 - _VT_QUALITY_PER_CRF_STEP * (crf - 18)
    return max(1, min(100, round(quality)))


def _quantiser(crf: int, offset: int) -> str:
    """Clamp a CRF-derived quantiser to the 0-51 scale every family shares."""
    return str(max(0, min(51, crf + offset)))


def quality_args(encoder: str, crf: int) -> list[str]:
    """Constant-quality arguments that make `crf` mean the same thing everywhere.

    Returns the empty list only for encoders that have no quality dial at all
    (ProRes picks a profile instead), and logs anything unrecognised rather than
    letting a backend fall through to its driver default in silence.
    """
    if encoder in {"libx264", "libx265"}:
        return ["-crf", str(crf)]
    if encoder.endswith("_videotoolbox"):
        return ["-q:v", str(videotoolbox_quality_from_crf(crf))]
    if encoder.endswith("_vaapi"):
        # `-qp` alone is ignored unless CQP is selected; the default rc_mode is
        # the driver's choice, which is how VAAPI came to ignore the dial.
        return ["-rc_mode", "CQP", "-qp", _quantiser(crf, _VAAPI_QP_OFFSET)]
    if encoder.endswith("_qsv"):
        return ["-global_quality", _quantiser(crf, _QSV_QUALITY_OFFSET)]
    if encoder.endswith("_nvenc"):
        return ["-rc", "constqp", "-qp", _quantiser(crf, _NVENC_QP_OFFSET)]
    if encoder.startswith("prores"):
        return []
    logger.warning(
        "No rate-control mapping for %s; the driver's default decides quality, "
        "not the configured CRF %d",
        encoder,
        crf,
    )
    return []
