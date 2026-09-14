---
sidebar_position: 7.5
title: HDR
---

# HDR, end to end

HDR output requires H.265. With `output.hdr_mode: auto`, the app preserves detected HDR
when the output codec supports it; SDR output is tone-mapped. Photographs with gain maps
are reconstructed before final assembly.

## Photographs

- **Apple HEIC.** When a gain map is present, the app reads both HDR headroom and gain tags
  from the MakerNote, linearizes the base image and gain map, and applies the scene's
  brightness boost. If the MakerNote cannot be read, it tries `exiftool` when installed.
  Missing headroom metadata leaves the base brightness unchanged.
- **Android Ultra HDR.** A JPEG with an MPF gain map and `hdrgm` XMP metadata, reconstructed with
  its per-channel gamma and offsets.
- HEIC decoding uses `pillow-heif` to read the full image and auxiliary gain map.

Photo intermediates are 10-bit PQ/BT.2020, including SDR photos encoded at SDR reference
white. Without FFmpeg's `zscale` filter the photo renderer falls back to SDR. Required
transfer conversions during final assembly need `zscale` and fail if it is unavailable.

Title text over HDR is drawn at HLG graphics white, not full white, so a caption does not glare
above the picture's own diffuse white.

## Video

`output.hdr_mode` accepts `auto`, `hdr` and `sdr`. H.265 with `auto` keeps detected HDR;
`hdr` forces HDR output and `sdr` requests tone mapping. This app rejects forced HDR with
H.264 or ProRes. Use H.265 when preserving HDR matters.

**HDR clips only** on the Memory page (`hdr_only`) filters SDR video from the source pool.
It does not guarantee that every included photograph contains HDR data.

The encoder capabilities per backend (which of NVENC, VideoToolbox, QSV and VAAPI take 10-bit)
are on [Hardware acceleration](../../deploy/hardware/overview.md).
