"""Print game facts as Markdown, ready to paste into guide entries.

uv run python -m pipeline.guide_facts graces "Stormhill"
uv run python -m pipeline.guide_facts groups
uv run python -m pipeline.guide_facts merchants
uv run python -m pipeline.guide_facts shop "Merchant Kale"
uv run python -m pipeline.guide_facts enemy "Godrick Soldier"
uv run python -m pipeline.guide_facts boss "Margit"
uv run python -m pipeline.guide_facts item "Crafting Kit"
"""

from __future__ import annotations

import argparse
import sys

from pipeline import guide_data as gd


def cmd_graces(args: argparse.Namespace) -> None:
    found = gd.graces(args.group)
    if not found:
        print(f"No graces in group {args.group!r}. Groups: {', '.join(gd.grace_groups())}")
        return
    for grace in found:
        print(f"- {grace.name} — [check] where it is")


def cmd_groups(_: argparse.Namespace) -> None:
    for group in gd.grace_groups():
        print(group)


def cmd_merchants(_: argparse.Namespace) -> None:
    for name in gd.merchants():
        print(name)


def cmd_shop(args: argparse.Namespace) -> None:
    items = gd.shop(args.merchant)
    if not items:
        print(f"No shop rows for {args.merchant!r}. Run `merchants` to list labels.")
        return
    print("| Item | Price | Stock |")
    print("|---|---|---|")
    for it in items:
        stock = "Unlimited" if it.stock is None else str(it.stock)
        if it.unlocked_later:
            stock += " (unlocked later)"
        print(f"| {it.name} | {it.price:,} | {stock} |")


def cmd_enemy(args: argparse.Namespace) -> None:
    variants = gd.enemy(args.name, include_unscaled=args.all)
    if not args.all:
        # Rows with no area scaling are templates or unused placements.
        variants = [v for v in variants if v.area_scaling]
    if not variants:
        print(f"No enemies matching {args.name!r}. Try --all to include template rows.")
        return
    # Merge placements with identical stats and drops.
    merged: dict[tuple, list[gd.EnemyVariant]] = {}
    for v in variants:
        key = (
            v.name,
            v.hp,
            v.runes,
            v.stance,
            tuple((d.name, d.chance, d.quantity) for d in v.drops),
        )
        merged.setdefault(key, []).append(v)
    for group in merged.values():
        v = group[0]
        ids = ", ".join(str(g.id) for g in group)
        scale = f" ({v.area_scaling})" if v.area_scaling else " (no area scaling)"
        print(f"### {v.name} — NpcParam {ids}")
        print(f"- **HP:** {v.hp:,}{scale}; base {v.base_hp:,}")
        print(f"- **Runes:** {v.runes:,}" + (" (bosses: see `boss`)" if v.runes == 0 else ""))
        print(f"- **Stance:** {v.stance}")
        crits = gd.criticals(v.id)
        print(f"- **Criticals:** {', '.join(crits) if crits else 'none found'} [check]")
        for d in v.drops:
            print(d.bullet())
        print()


def cmd_dungeon(args: argparse.Namespace) -> None:
    c = gd.area_checklist(args.area)
    print(f"# {c.area} — checklist from params")
    print()
    print("## Sites of Grace")
    for g in c.graces:
        print(f"- {g.name}")
    if not c.graces:
        print("- none found by name")
    print()
    print("## One-time pickups (corpses and chests)")
    for lot in c.pickups:
        items = ", ".join(f"{n} (x{q})" if q > 1 else n for n, q in lot.items)
        print(f"- {items} — lot {lot.lot_id}")
    if not c.pickups:
        print("- none labeled for this area")
    print()
    print("## Bosses")
    for b in c.bosses:
        print(f"- **{b.name}** — {b.runes:,} runes")
        seen = set()
        for v in b.variants:
            key = (v.name, v.hp, v.stance)
            if key in seen:
                continue
            seen.add(key)
            crits = ", ".join(gd.criticals(v.id)) or "none found"
            print(f"  - {v.name}: {v.hp:,} HP, stance {v.stance}, criticals: {crits}")
    for lot in c.boss_drops:
        items = ", ".join(f"{n} (x{q})" if q > 1 else n for n, q in lot.items)
        print(f"- Boss drop: {items} — lot {lot.lot_id}")
    if not c.bosses and not c.boss_drops:
        print("- none found")
    print()
    if gd.placements_available():
        print_placements(args.area, args.radius, args.height)
        return
    print(
        "Not in the params: gatherable plants (e.g. Glovewort), enemy placements, levers "
        "and positions. Those come from the map files or the route pass."
    )


def print_placements(area: str, radius: float, height: float) -> None:
    anchor, rows = gd.area_placements(area, radius, height)
    if anchor is None:
        print(f"## Map data\n- no grace matches {area!r}, so the map area is unknown")
        return
    open_world = anchor.map_id.startswith("m60_")
    scope = (
        f"within {radius:.0f} m and ±{height:.0f} m height of the {anchor.name} grace"
        if open_world
        else f"whole map {anchor.map_id}"
    )
    print(f"## Map data ({scope}; positions relative to the {anchor.name} grace)")

    enemies: dict[str, list[gd.MapPlacement]] = {}
    for p in rows:
        if p.kind == "enemy" and p.name not in gd.SYSTEM_NPCS:
            enemies.setdefault(p.name, []).append(p)
    print("\n### Enemies")
    for name, group in sorted(enemies.items(), key=lambda kv: -len(kv[1])):
        print(f"- {name} x{len(group)} (NpcParam {group[0].param_id})")
    if not enemies:
        print("- none")

    for kind, title in (("treasure", "Corpse and chest pickups"), ("gather", "Gatherables")):
        print(f"\n### {title}")
        items = sorted((p for p in rows if p.kind == kind), key=lambda p: (p.name, p.y))
        if kind == "gather" and open_world:
            # Open-world plants are everywhere; counts are enough.
            counts: dict[str, int] = {}
            for p in items:
                counts[p.name] = counts.get(p.name, 0) + 1
            for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                print(f"- {name} x{n}")
        else:
            for p in items:
                print(f"- {p.name} — {gd.relative_to(p, anchor)} (lot {p.param_id}, {p.part})")
        if not items:
            print("- none")
    if open_world:
        event_lots = gd.tile_event_lots(anchor.map_id)
        if event_lots:
            print(f"\n### Given by events in tile {anchor.map_id} (no position in the map data)")
            for lot in event_lots:
                items = ", ".join(f"{n} (x{q})" if q > 1 else n for n, q in lot.items)
                print(f"- {items} — {lot.label} (lot {lot.lot_id})")
    print(
        "\nLevers, doors, traps and room layout aren't extracted. Positions are straight-line "
        "from the grace, so use them to place things, not as directions."
    )


def cmd_boss(args: argparse.Namespace) -> None:
    rows = gd.boss_runes(args.name)
    if not rows:
        print(f"No boss areas matching {args.name!r}.")
    for label, runes in rows:
        print(f"- {label}: **{runes:,} runes**")


def cmd_item(args: argparse.Namespace) -> None:
    hits = gd.find_item(args.name) or gd.search_items(args.name)
    if not hits:
        print(f"No items matching {args.name!r}.")
    for kind, item_id, name in hits:
        print(f"- {name} ({kind} {item_id})")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Game facts for guide entries.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("graces", help="Graces in a group").add_argument("group")
    sub.add_parser("groups", help="List grace groups")
    sub.add_parser("merchants", help="List merchant labels")
    sub.add_parser("shop", help="A merchant's stock").add_argument("merchant")
    p = sub.add_parser("enemy", help="HP, runes, drops")
    p.add_argument("name")
    p.add_argument("--all", action="store_true", help="Include unscaled template rows")
    sub.add_parser("boss", help="Boss rune rewards").add_argument("name")
    p = sub.add_parser("dungeon", help="Checklist for one area")
    p.add_argument("area")
    p.add_argument(
        "--radius",
        type=float,
        default=gd.DEFAULT_RADIUS,
        help="Open-world areas: metres around the area's grace to include",
    )
    p.add_argument(
        "--height",
        type=float,
        default=gd.DEFAULT_MAX_HEIGHT,
        help="Open-world areas: max metres above or below the grace",
    )
    sub.add_parser("item", help="Find an item by name").add_argument("name")
    args = parser.parse_args(argv)
    handlers = {
        "graces": cmd_graces,
        "groups": cmd_groups,
        "merchants": cmd_merchants,
        "shop": cmd_shop,
        "enemy": cmd_enemy,
        "boss": cmd_boss,
        "dungeon": cmd_dungeon,
        "item": cmd_item,
    }
    sys.stdout.reconfigure(encoding="utf-8")
    handlers[args.command](args)


if __name__ == "__main__":
    main()
