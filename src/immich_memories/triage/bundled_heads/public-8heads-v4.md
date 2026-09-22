# Public eight-head bundle

`public-8heads-v4.npz` is `public-6heads-v3.npz` with the `swim` head removed and
three heads appended. SHA-256: `cfa2cf067c517e783c43771b78cdb0a5b7a42a43b7629e55165c2cf82c6cc117`.

The location, people, children and activity heads (`public-v1`), the venue head
(`oi-v3`) and the shared PCA are the arrays of `public-6heads-v3.npz`, copied
byte for byte without retraining, so every `head_facts` row banked for them under
the old bundle stays valid. They were produced by
`scripts/triage_heads/export_bundle.py public` and
`scripts/triage_heads/oi_index.py sensitivity` from the Open Images public
training partition, with creator-held-out splits.

`frame_kind`, `screen` and `uncovered_person` (`public-v1`, `public-v1-strict`,
`public-v1`) were distilled by `scripts/triage_heads/picture_facts/` from a typed
picture reader's answers over 6,604 Open Images pictures by 4,951 creators, on
this bundle's own PCA, and assembled by
`scripts/triage_heads/picture_facts/ship_bundle.py`. The two binary heads carry
their operating band in their coefficients rather than a threshold the schema
cannot hold: `screen` at probability 0.999999 and `uncovered_person` at 0.829,
each subtracted as a logit from the `yes` intercept, so the ordinary argmax
answers `yes` exactly when the head's probability passed its band.

The `swim` head is not here. Measured against a typed reader over 3,564
photographs it flagged 551 where the reader saw swimwear on 22, and the one rule
that read it fired on 515 of them and was right about 5. A bank that still holds
its rows keeps them; nothing reads them.

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
