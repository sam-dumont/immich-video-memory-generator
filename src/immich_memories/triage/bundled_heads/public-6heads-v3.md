# Public six-head bundle

`public-6heads-v3.npz` is the existing public bundle, copied without retraining.
SHA-256: `3e410734db06fe97900301ea4b9c5db569b065c4bc9ee1c2bb027ef58c3eda7a`.

The location, people, children and activity heads (`public-v1`) and shared PCA
were produced by `scripts/triage_heads/export_bundle.py public`, using the
Open Images public training partition. The venue and swim heads (`oi-v3`)
were appended by `scripts/triage_heads/oi_index.py sensitivity`, using public
Open Images sensitivity and negative examples with creator-held-out splits.
This bundle contains no owner photographs, owner-trained heads, captions,
identities or review grades. Its arrays contain only model coefficients.

The source photographs came from the CVDF Open Images mirror. Their source
manifests recorded CC BY 2.0 licenses and creator attribution. Open Images
source attribution is available at https://storage.googleapis.com/openimages/web/download_v7.html.
The photographs are not redistributed with this bundle. The repository's
existing license applies to its training and serving code; this notice does
not relicense the source photographs.

The encoder is the pinned DINOv2-small ONNX export documented in
`immich_memories.triage.encoder`. The bundle requires encoder key
`3d8a4df21037f4fb663fddc98d906a49506e10126ad89df04f5e5cdf8e821d81`.
These heads provide context for editorial decisions. Their labels are not
standalone audience exclusions.
