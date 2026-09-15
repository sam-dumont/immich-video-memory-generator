---
sidebar_position: 7.5
title: HDR
---

# HDR, end to end

A memory keeps the dynamic range its sources have. HDR video (HLG or PQ from a phone) stays HDR,
and an HDR photograph is reconstructed from its gain map rather than flattened.

## Photographs

- **Apple (iPhone 12 and later).** A HEIC carries an 8-bit Display P3 base image and a grayscale
  gain map; the headroom for the scene comes from two MakerNote tags read together, `0x0021` and
  `0x0030`. The renderer interpolates linearly between 1.0 and that headroom,
  `HDR = SDR × (1 + (headroom - 1) × gain)`, and ships it as 10-bit PQ, BT.2020. That is Apple's
  own shape, and it never darkens a pixel; the exponential `2^(gain × headroom)` this page used to
  print is the ISO 21496-1 form, which belongs to Ultra HDR and lifted mid-tones about twice as far
  as CoreImage does. It takes both tags because `0x0021` alone gave 2.01x on a photograph whose
  real headroom was 5.955x. Checked against CoreImage's own `kCIImageExpandToHDR` on 11 photographs
  across headrooms 3.50 to 6.91: median error 1 to 2 %, 10 % on the worst that converged. When the
  MakerNote does not parse it falls back to `exiftool` if that is installed
  (`brew install exiftool`, or `apt install libimage-exiftool-perl`), and a photograph whose
  headroom cannot be read renders at the brightness of its base image instead of black.
- **Android Ultra HDR.** A JPEG with an MPF gain map and `hdrgm` XMP metadata, reconstructed with
  its per-channel gamma and offsets.
- HEIC decoding uses `pillow-heif`; FFmpeg reads only the thumbnail tiles of a HEIC.

Title text over HDR is drawn at HLG graphics white, not full white, so a caption does not glare
above the picture's own diffuse white.

## Video

The output follows the sources: `output.hdr_mode` is `auto` (HDR when any selected source is
HDR), `hdr` or `sdr`. HDR output is H.265 and only H.265: `encoding_plan.py` refuses `hdr` with
H.264, which carries no HDR at all, and refuses it with ProRes too, so a ProRes render is SDR.
Either way it is refused rather than silently flattened. **HDR clips only** on the Memory page (`hdr_only`) drops
SDR video from the pool when you want a purely HDR cut.

The encoder capabilities per backend (which of NVENC, VideoToolbox, QSV and VAAPI take 10-bit)
are on [Hardware acceleration](../../deploy/hardware.md).
