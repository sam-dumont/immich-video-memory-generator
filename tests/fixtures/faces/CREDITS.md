# Where the face fixture comes from

The e2e fixture library next door deliberately shows no recognisable face, so
the face detector needs its own picture. This one is a CC0 1.0 (public domain
dedication) stock photograph from StockSnap.io, found through the Openverse
index — the same provenance as `tests/e2e/fixtures/library`.

| File | Title | Photographer | Source | Licence |
| --- | --- | --- | --- | --- |
| `picnic-friends.jpg` | Friends Picnic | Helena Lopes | https://stocksnap.io/photo/friends-picnic-3IVBNKC2JH | CC0 1.0 |

Four faces, 960x652, re-encoded at JPEG quality 80 to keep it the size of the
other fixtures. YuNet finds all four; at the shipped score threshold of 0.9 it
keeps three of them.
