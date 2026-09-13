---
sidebar_position: 7.5
title: HDR
---

# HDR, end to end

A memory keeps the dynamic range its sources have. HDR video (HLG or PQ from a phone) stays HDR,
and an HDR photograph is reconstructed from its gain map rather than flattened.

## Photographs

- **Apple (iPhone 12 and later).** A HEIC carries an 8-bit Display P3 base image and a grayscale
  gain map; the headroom for the scene is read from the MakerNote. The renderer rebuilds the HDR
  image as `HDR = SDR × 2^(gain × headroom)` and ships it as 10-bit PQ, BT.2020. When the
  MakerNote does not parse it falls back to `exiftool` if that is installed, and a photograph
  whose headroom cannot be read renders at the brightness of its base image instead of black.
- **Android Ultra HDR.** A JPEG with an MPF gain map and `hdrgm` XMP metadata, reconstructed with
  its per-channel gamma and offsets.
- HEIC decoding uses `pillow-heif`; FFmpeg reads only the thumbnail tiles of a HEIC.

Title text over HDR is drawn at HLG graphics white, not full white, so a caption does not glare
above the picture's own diffuse white.

## Video

The output follows the sources: `output.hdr_mode` is `auto` (HDR when any selected source is
HDR), `hdr` or `sdr`. H.264 carries no HDR, so `hdr` with an H.264 codec is refused rather than
silently flattened; use H.265 or ProRes. **HDR clips only** on the Memory page (`hdr_only`) drops
SDR video from the pool when you want a purely HDR cut.

The encoder capabilities per backend (which of NVENC, VideoToolbox, QSV and VAAPI take 10-bit)
are on [Hardware acceleration](../../deploy/hardware/overview.md).
