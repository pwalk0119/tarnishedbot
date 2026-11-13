# Limgrave guide

The guide is TarnishedBot's knowledge base for walkthrough questions. Each topic is one
Markdown file under `entries/`. The search index (ChromaDB, coming next) splits entries at
`##` headings, so each section should make sense on its own.

## Workflow

1. Pick a `todo` entry from [coverage.md](coverage.md).
2. Copy the template for its type from `templates/` into `entries/<type>/<slug>.md`
   (slug = lowercase title with dashes, e.g. `church-of-elleh.md`).
3. Fill it in. Pull exact numbers with the facts tool instead of typing them from memory:
   ```
   uv run python -m pipeline.guide_facts graces "Stormhill"
   uv run python -m pipeline.guide_facts shop "Merchant Kale"
   uv run python -m pipeline.guide_facts enemy "Godrick Soldier"
   uv run python -m pipeline.guide_facts boss "Margit"
   uv run python -m pipeline.guide_facts item "Crafting Kit"
   ```
4. Mark anything you're unsure of with `[check]`.
5. Run the checker. It validates every entry and updates the statuses in coverage.md:
   ```
   uv run python -m pipeline.guide_check --update-coverage
   ```
6. After reviewing an entry (facts confirmed, no `[check]` left), set `status: verified`.
   Only verified entries go into the bot's search index by default.

## Header (front matter)

```yaml
---
title: Church of Elleh          # must match the name in coverage.md
type: landmark                  # region | dungeon | landmark | npc | field_boss | boss | enemy
region: Limgrave                # Limgrave | Stormhill | Weeping Peninsula | Stormveil Castle | Stranded Graveyard
aliases: [Elleh]                # other names players might use (optional)
tags: [merchant, smithing]      # free-form keywords (optional)
related: [Kale, Gatefront Ruins]  # titles of related entries (optional)
status: draft                   # draft | verified
source: original                # original | fandom:<page URL>
---
```

`source: original` means written in our own words. Text pasted by hand from the Fandom wiki
(CC BY-SA) must say `source: fandom:<URL of the page>` so the bot can credit it. Never copy
text from Fextralife or other sites whose terms forbid reuse.

## Sections

Each type has required `##` sections (in any order; extra sections are fine). Write "None."
when a section doesn't apply.

| Type | Required sections |
|---|---|
| region | Overview, Getting There, Sites of Grace, Points of Interest, Bosses, NPCs, Tips |
| dungeon | Overview, Location, Sites of Grace, Walkthrough, Enemies, Boss, Items, Tips |
| landmark | Overview, Location, Sites of Grace, What's Here, Items, Tips |
| npc | Overview, Location, Services, Questline, Rewards, Tips |
| field_boss, boss | Overview, Location, Stats, Strategy, Rewards, Tips |
| enemy | Overview, Where Found, Stats, Drops, How to Fight |

## Conventions the checker understands

- **Sites of Grace:** one bullet per grace, name first: `- Stormhill Shack — on the road
  north of ...`. Names are checked against the game data.
- **Items:** one bullet per item with the name in bold: `- **Crafting Kit** — bought from
  Kalé`. Names are checked against the game data (a mismatch is a warning, since community
  names vary). Region entries have no Items section: pickups belong to the landmark or
  dungeon entry where they're found.
- **Drops and stacks:** the quantity goes right after the item name, before the chance:
  `- **Bolt** (x5) — 10% drop chance`, not `- **Bolt** — 10% drop chance (x5)`.
- **Scope:** entries cover Limgrave, Stormhill, the Weeping Peninsula, Stormveil Castle and
  the Stranded Graveyard. When a questline continues elsewhere (Varré, Blaidd, Ranni), cover
  the Limgrave steps in full and summarize the rest under a "Later steps (outside Limgrave)"
  line, marked out of scope.
- **`[check]`:** marks a fact to confirm. Verified entries can't contain it.
- **HTML comments** (`<!-- ... -->`) are writing hints from the templates. They're ignored
  by the search index and can stay or go.

## Game data notes

- Numbers come from the pinned regulation 1.17.3 data in `data/processed/`.
- Enemy HP from the facts tool already includes area scaling (e.g. Limgrave ×1.141,
  Stormveil Castle ×1.656), which is what you see in game.
- **Stance:** the facts tool's `enemy` command shows each enemy's stance bar
  (NpcParam `superArmorDurability`). The chat's `calculate_stance_damage` tool gives a weapon's
  stance damage per attack and the hits needed to break an enemy.
- **Criticals:** the facts tool's `enemy` command lists which critical hits work on an
  enemy (backstab, riposte after a parry, critical after a stance break), read from
  ThrowParam. The mapping from ThrowParam groups to critical types is inferred (see
  `CRITICAL_GROUPS` in `pipeline/guide_data.py`), so confirm it in game before marking an
  entry verified.
- Item names are community labels from Smithbox's Paramdex, not the game's own text. They
  match in-game names almost always, but double-check odd ones.
- Game text (item descriptions, dialogue) is FromSoftware's: paraphrase it, don't paste it.
