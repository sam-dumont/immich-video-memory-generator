# Visual Style Guide

Shared design tokens between the NiceGUI web app and the Docusaurus docs site.
Both must look like they're part of the same product.

## Colors

### Primary
| Token | Light | Dark |
|-------|-------|------|
| Primary | `#4250af` | `#6B8FE8` |
| Primary hover | `#3a47a0` | `#5A7FD8` |
| Primary tint (backgrounds) | `rgba(66, 80, 175, 0.08)` | `rgba(107, 143, 232, 0.12)` |

### Backgrounds
| Token | Light | Dark |
|-------|-------|------|
| Main background | `#ffffff` | `#000000` |
| Surface / elevated | `#f6f6f4` | `#222222` |

### Text
| Token | Light | Dark |
|-------|-------|------|
| Primary text | `#000000` | `#dbdbdb` |
| Secondary text | `#6b7280` | `#d4d4d4` |
| Muted text | `#9ca3af` | `#a1a1a1` |

### Borders
| Token | Light | Dark |
|-------|-------|------|
| Border | `#e5e7eb` | `#262626` |
| Border light | `#f3f4f6` | `#262626` |

### Status
| Token | Light | Dark |
|-------|-------|------|
| Success | `#2e7d32` | `#81c784` |
| Warning | `#b45309` | `#f57c00` |
| Error | `#c62828` | `#e57373` |
| Info | `#4250af` | `#6B8FE8` |

## Typography

**Font:** Inter (Google Fonts)
**Fallbacks:** `-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`
**No serif fonts. Ever.**

| Weight | Usage |
|--------|-------|
| 400 | Body text |
| 500 | Emphasis, labels |
| 600 | Section headers, sidebar active |
| 700 | Page titles, brand |

Font smoothing: `-webkit-font-smoothing: antialiased`

## Spacing & Shape

| Token | Value |
|-------|-------|
| Border radius (cards, inputs) | `10px` |
| Border radius (badges) | `6px` |
| Border radius (circular) | `50%` |

## Shadows

| Token | Light | Dark |
|-------|-------|------|
| Shadow | `0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)` | `none` |
| Shadow MD | `0 4px 6px rgba(0,0,0,0.05), 0 2px 4px rgba(0,0,0,0.04)` | `0 2px 8px rgba(0,0,0,0.4)` |

## Transitions

All interactive elements: `transition: all 0.15s ease`

## Dark mode

- True black background (`#000000`), not dark gray
- Surface elements use `#222222`
- Borders use `#262626`
- System preference respected by default

## What to avoid

- Serif fonts
- Warm amber/orange color schemes (that's not us)
- Heavy shadows or glassmorphism
- Emoji as icons in feature cards
- Generic value props ("GPU fast!", "enterprise-ready")

## Where tokens are defined

- **NiceGUI app:** `src/immich_memories/ui/theme.py`
- **Docusaurus:** `docs-site/src/css/custom.css`
