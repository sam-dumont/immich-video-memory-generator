# Visual style guide

The app and docs use Inter, an indigo accent, restrained borders and 10 px card corners.
Fonts are served locally. The definitions are in
[theme.py](../src/immich_memories/ui/theme.py) and
[custom.css](../docs-site/src/css/custom.css); check those files when changing a token.

| Token | Light | Dark |
| --- | --- | --- |
| Primary | `#4250af` | `#6B8FE8` |
| Background | `#ffffff` | `#09090b` |
| App surface | `#fafafa` | `#111113` |
| App elevated surface | `#ffffff` | `#1a1a1e` |
| Primary text | `#000000` | `#dbdbdb` |
| Secondary text | `#6b7280` | `#d4d4d4` |

Use the existing theme variables for new components. Check both light and dark mode, including
focus, hover, disabled controls and readable status messages. Avoid heavy shadows, serif fonts,
emoji feature icons and promotional copy.
