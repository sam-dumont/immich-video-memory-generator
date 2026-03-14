# LLM Title Generation

Instead of generic "TWO WEEKS IN SPAIN, SUMMER 2025" template titles, the app feeds your trip's raw GPS data to a local LLM and gets back something like "Sous les falaises de la Saxe" or "Odyssée à travers l'Ombrie et les Marches". It works in any language and classifies your trip pattern too.

## What the LLM gets

After the analysis phase completes, the LLM receives daily GPS clusters: how many photos you took at each location, each day. Something like:

```
07-22: La Thuile(136), Courmayeur(100), Ville Sur Sarre(3)
07-23: Ville Sur Sarre(146)
07-24: Ville Sur Sarre(89)
07-25: Saint-Rhémy(176), Ville Sur Sarre(1)
```

From that raw data, it figures out the travel pattern (base camp? road trip? hiking trail?) and generates a title + subtitle in your locale. No pre-processing, no clustering algorithm telling it what to think: just the raw photo distribution and the model's own reasoning.

## What it produces

- **Title** and optional **subtitle** in your configured language
- **Trip type**: `base_camp`, `multi_base`, `road_trip`, or `hiking_trail`
- **Map mode** recommendation for the animated map intro
- A one-line **reason** explaining why it picked that classification

You see everything in Step 3 of the UI and can edit before rendering.

## Configuration

The title LLM can be different from the vision model (and probably should be):

```yaml
# Vision model (content analysis, clip scoring)
llm:
  provider: openai-compatible
  base_url: http://localhost:9999/v1
  model: Qwen2.5-VL-7B-Instruct-4bit

# Text model for titles (optional, falls back to llm if not set)
title_llm:
  provider: openai-compatible
  base_url: http://localhost:9999/v1
  model: Qwen3.5-9B-MLX-4bit
```

Title language comes from `title_screens.locale` in your config (`fr`, `en`, `it`, `de`, etc.).

## Model recommendations

**Qwen3.5-9B-MLX-4bit with thinking disabled** is what you want. 5.5GB, 7-17 seconds per title on Apple Silicon, 100% JSON reliability, and genuinely creative multilingual output.

One catch: you MUST disable thinking mode in the omlx admin panel (`/admin`). Set `chat_template_kwargs` to `{"enable_thinking": false}` for the Qwen3.5 model. With thinking enabled, the model burns 2000-8000 tokens on chain-of-thought before it even starts the JSON, and most requests time out.

| Model | Size | Speed | Quality | Reliability | Notes |
|-------|------|-------|---------|-------------|-------|
| **Qwen3.5-9B (no think)** | 5.5GB | 7-17s | Great | 100% | Best overall |
| Qwen3.5-4B (no think) | 2.9GB | ~10s | Good | ~90% | Lighter alternative |
| Qwen2.5-VL-7B | 4.5GB | 4-5s | OK | T=0.1 only | Vision model doing text: works but generic |
| Qwen3.5-9B (thinking ON) | 5.5GB | 300s+ | Great | ~30% | Don't. Disable thinking. |

## Trip classification

The LLM looks at which locations repeat across days:

| Pattern | Classification | Map mode | Real example |
|---------|---------------|----------|-------------|
| Same spot every night, excursions during the day | `base_camp` | `excursions` | Val d'Aoste: Ville Sur Sarre as base, hikes to Cogne and La Thuile |
| 2-3 spots, each for multiple consecutive days | `multi_base` | `overnight_stops` | Cyprus: 5 nights in Nicosia, 5 nights in Geroskipou |
| Different town each day, big distances | `road_trip` | `overnight_stops` | Italy 2022: 14 days from Umbria through Marche to Alsace |
| Daily moves but short distances, progressive | `hiking_trail` | `overnight_stops` | Saxon Switzerland: Hohnstein to Bad Schandau to Königstein |

## Editing the prompt

The prompt lives in `src/immich_memories/prompts/title_generation.md`, not in Python code. You can tweak it without touching the source: change the examples, adjust the rules, add banned words. Changes take effect on next generation.

## Fallback

No LLM configured? The app falls back to the template-based title system (the "TWO WEEKS IN X" style). You can also just type your own title in the Step 3 text field.
