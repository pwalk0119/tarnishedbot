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
        key = (v.name, v.hp, v.runes, tuple((d.name, d.chance, d.quantity) for d in v.drops))
        merged.setdefault(key, []).append(v)
    for group in merged.values():
        v = group[0]
        ids = ", ".join(str(g.id) for g in group)
        scale = f" ({v.area_scaling})" if v.area_scaling else " (no area scaling)"
        print(f"### {v.name} — NpcParam {ids}")
        print(f"- **HP:** {v.hp:,}{scale}; base {v.base_hp:,}")
        print(f"- **Runes:** {v.runes:,}" + (" (bosses: see `boss`)" if v.runes == 0 else ""))
        for d in v.drops:
            qty = f" ×{d.quantity}" if d.quantity > 1 else ""
            print(f"- **{d.name}**{qty} — {d.chance:g}% drop chance")
        print()


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
    sub.add_parser("item", help="Find an item by name").add_argument("name")
    args = parser.parse_args(argv)
    handlers = {
        "graces": cmd_graces,
        "groups": cmd_groups,
        "merchants": cmd_merchants,
        "shop": cmd_shop,
        "enemy": cmd_enemy,
        "boss": cmd_boss,
        "item": cmd_item,
    }
    sys.stdout.reconfigure(encoding="utf-8")
    handlers[args.command](args)


if __name__ == "__main__":
    main()
