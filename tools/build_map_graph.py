#!/usr/bin/env python
"""Build `pw/static/maps.json` from the PokeAPI-derived `locations.json`.

The game travels between *nodes* (towns, routes, caves, ...) grouped per
region.  This script turns the flat encounter-location table into a per-region
travel graph:

  * a canonical town progression provides the backbone of each region,
  * routes/areas are interleaved between towns by route number / encounter level,
  * consecutive nodes are chained and towns get extra links to the nearest route
    on each side, which keeps every region a single connected component.

Run:  python tools/build_map_graph.py
"""

from __future__ import annotations

import itertools
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "pw" / "static" / "locations.json"
OUT = ROOT / "pw" / "static" / "maps.json"

REGION_ORDER: list[str] = [
    "kanto",
    "johto",
    "hoenn",
    "sinnoh",
    "unova",
    "kalos",
    "alola",
    "galar",
    "paldea",
]
REGION_GEN = {r: i + 1 for i, r in enumerate(REGION_ORDER)}

# Canonical town/city progression per region (real game order).  Slugs missing
# from locations.json are skipped; nodes are never invented.
CANONICAL_TOWNS: dict[str, list[str]] = {
    "kanto": [
        "pallet-town", "viridian-city", "pewter-city", "cerulean-city",
        "vermilion-city", "lavender-town", "celadon-city", "saffron-city",
        "fuchsia-city", "cinnabar-island", "indigo-plateau",
    ],
    "johto": [
        "new-bark-town", "cherrygrove-city", "violet-city", "azalea-town",
        "goldenrod-city", "ecruteak-city", "olivine-city", "cianwood-city",
        "mahogany-town", "blackthorn-city", "indigo-plateau",
    ],
    "hoenn": [
        "littleroot-town", "oldale-town", "petalburg-city", "rustboro-city",
        "dewford-town", "slateport-city", "mauville-city", "verdanturf-town",
        "fallarbor-town", "lavaridge-town", "fortree-city", "lilycove-city",
        "mossdeep-city", "sootopolis-city", "ever-grande-city", "pacifidlog-town",
    ],
    "sinnoh": [
        "twinleaf-town", "sandgem-town", "jubilife-city", "oreburgh-city",
        "floaroma-town", "hearthome-city", "eterna-city", "solaceon-town",
        "veilstone-city", "pastoria-city", "celestic-town", "canalave-city",
        "snowpoint-city", "sunyshore-city", "sinnoh-pokemon-league",
    ],
    "unova": [
        "nuvema-town", "accumula-town", "striaton-city", "nacrene-city",
        "castelia-city", "nimbasa-city", "driftveil-city", "mistralton-city",
        "icirrus-city", "opelucid-city", "lacunosa-town", "undella-town",
        "humilau-city", "aspertia-city", "virbank-city",
    ],
    "kalos": [
        "vaniville-town", "aquacorde-town", "santalune-city", "lumiose-city",
        "camphrier-town", "cyllage-city", "ambrette-town", "geosenge-town",
        "shalour-city", "coumarine-city", "laverre-city", "dendemille-town",
        "anistar-city", "couriway-town", "snowbelle-city", "kiloude-city",
    ],
    "alola": [
        "iki-town", "hauoli-city", "heahea-city", "konikoni-city",
        "paniola-town", "malie-city", "tapu-village", "seafolk-village",
        "exeggutor-island",
    ],
    "galar": [
        "postwick", "wedgehurst", "motostoke", "turffield", "hulbury",
        "hammerlocke", "stow-on-side", "circhester", "spikemuth", "wyndon",
    ],
    "paldea": ["paldea-mesagoza", "paldea-cortondo", "paldea-artazon", "paldea-levincia", "paldea-cascarrafa", "paldea-medali", "paldea-montenevera", "paldea-alfornada", "paldea-glaseado-mountain", "paldea-area-zero", "paldea-pokemon-league"],
}

TOWN_RE = re.compile(r"(city|town|village|plateau|league|ranch|resort|safari)")
ROUTE_NUM_RE = re.compile(r"(\d+)")

# 主线之外的可选/二周目区域:七之岛、对战区、DLC 岛、幻影地点等。
# 它们不该按“遭遇等级”插在城镇之间 —— 七之岛的野怪等级只有 17~25,
# 结果全被塞到深灰市前,玩家在主线上就会发现路线里冒出一串海岛。
# 这些节点统一排到最后一个城镇之后、联盟之前(只影响游学顺序,不影响连通)。
SIDE_AREA_MARKERS: tuple[str, ...] = (
    "-island", "-islands", "-islet", "isle-", "sevii",
    "water-labyrinth", "altering-cave", "memorial-pillar", "tanoby", "chamber",
    "navel-rock", "faraway-island", "outcast-island", "birth-island",
    "resort", "trainer-tower", "ruin-valley", "green-path", "pattern-bush",
    "cerulean-cave", "power-plant", "sevault",
    "treasure-beach", "kindle-road", "bond-bridge", "cape-brink", "berry-forest",
    "water-path", "lost-cave", "mt-ember",
    "mirage", "battle-", "sky-pillar", "crescent-isle", "trackless-forest",
    "nameless-cavern", "soaring", "pathless-plain", "fabled-cave", "gnarled-den",
    "terra-cave", "marine-cave", "desert-ruins", "ancient-tomb",
    "newmoon", "distortion-world", "stark-mountain", "sendoff-spring",
    "hall-of-origin", "flower-paradise",
    "ultra-space", "poke-pelago", "aether-paradise", "resolution-cave",
    "mahalo-trail", "ruins-of-", "altar-of-the", "team-rockets-castle",
    "max-lair", "crown-shrine", "energy-plant", "lakeside-cave",
    "roaring-sea-caves", "tunnel-to-the-top", "isle-of-armor",
    "rock-peak-ruins", "iron-ruins", "iceberg-ruins", "decision-ruins",
    "sea-spirits-den", "friend-safari", "pokemon-village",
)


def is_side_area(identifier: str) -> bool:
    """是否是“主线之外”的支线区域(七之岛/对战设施/二周目地图)。"""
    name = identifier
    for region in REGION_ORDER:
        if name.startswith(f"{region}-"):
            name = name[len(region) + 1:]
            break
    return any(marker in name for marker in SIDE_AREA_MARKERS)


def kind_of(identifier: str, region: str = "") -> str:
    """判定节点类型:优先用"真实城镇名单",其次按命名约定。

    仅靠命名会漏掉一整类城镇(如伽勒尔的 postwick/wedgehurst/... 全都不含
    city/town 字样),导致该地区没有宝可梦中心与商店、城镇列表为空。
    """
    if identifier in CANONICAL_TOWNS.get(region, []):
        return "town"
    if TOWN_RE.search(identifier):
        return "town"
    if "route" in identifier:
        return "route"
    return "area"


# 上游 locations.json 里混着"不是地点"的伪条目:
#   roaming-{kanto,…} —— 游走宝可梦的抽象容器
#   unknown-all-*/unknown-dungeon —— Pokéwalker/事件占位符
#   {region}-pokemart / {region}-pokecenter —— PokeAPI 里通用的商店/中心容器
#     (不是真实地点,出现在地图上会挤占"你在这里"的位置)
# 它们都带着野池,若不剔除,玩家会看到能"前往 Roaming Sinnoh"这种事。
PSEUDO_LOCATION_RE = re.compile(r"^(roaming-|unknown-)|.*-(pokemart|pokecenter)$")


def is_pseudo_location(identifier: str) -> bool:
    return bool(PSEUDO_LOCATION_RE.match(identifier))


def pools_non_empty(loc: dict) -> bool:
    return any(bool(v) for v in loc.get("pools", {}).values())


def avg_level(loc: dict) -> float:
    levels: list[float] = []
    for pool in loc.get("pools", {}).values():
        for entry in pool:
            _species, lo, hi, _method, _rarity = entry
            levels.append((float(lo) + float(hi)) / 2.0)
    if not levels:
        return 0.0
    return sum(levels) / len(levels)


def route_number(identifier: str) -> int:
    match = ROUTE_NUM_RE.search(identifier)
    return int(match.group(1)) if match else 0


def clamp_tier(rank: int, total: int) -> int:
    value = math.ceil(rank * 8 / total)
    return max(1, min(8, value))


def region_level_range(ids: list[str], locs: dict) -> tuple[float, float]:
    values = [avg_level(locs[i]) for i in ids]
    if not values:
        return 0.0, 1.0
    lo, hi = min(values), max(values)
    return lo, hi if hi > lo else (lo, lo + 1.0)


def build_region(
    region: str, locs: dict, region_zh: str = ""
) -> tuple[dict, list[str], list[str]]:
    """Return (region_entry, skipped_towns, node_ids)."""
    skipped: list[str] = []
    nodes: dict[str, str] = {}  # id -> kind
    for ident, loc in locs.items():
        if loc.get("region") != region or is_pseudo_location(ident):
            continue
        kind = kind_of(ident, region)
        if (kind == "town" or pools_non_empty(loc)
                # 帕底亚城镇本身没有野生池(礼物/交换行才刚收进来),
                # 但它们是道馆/商店所在 —— 只对帕底亚放开,别动其他地区的节点集合
                or (region == "paldea" and ident in CANONICAL_TOWNS.get(region, ()))):
            nodes[ident] = kind

    canonical = [t for t in CANONICAL_TOWNS.get(region, []) if t in nodes]
    skipped.extend(t for t in CANONICAL_TOWNS.get(region, []) if t not in nodes)

    if not nodes:
        return (
            {
                "zh": "",
                "gen": REGION_GEN[region],
                "order": REGION_GEN[region],
                "gateway": "",
                "nodes": {},
            },
            skipped,
            [],
        )

    anchors = canonical
    non_anchor = [i for i in nodes if i not in anchors]

    routes = sorted(
        (i for i in non_anchor if nodes[i] == "route"),
        key=lambda i: (route_number(i), i),
    )
    areas = sorted(
        (i for i in non_anchor if nodes[i] != "route"),
        key=lambda i: (avg_level(locs[i]), i),
    )

    # Assign every non-anchor node to an anchor slot (0 .. len(anchors)-1).
    assigned: dict[int, list[str]] = {i: [] for i in range(len(anchors))}
    area_sort: dict[str, tuple] = {}   # 主线地区 → 它挂靠的路线(决定插队位置)
    side_areas = [i for i in areas if is_side_area(i)]
    main_areas = [i for i in areas if i not in set(side_areas)]
    if anchors:
        last = len(anchors) - 1
        if routes:
            # 路线按**位次**均分到各城镇段。按号码值取比例会让前几号路线
            # 全挤在第一段(2~5 号道路挤在深灰市前),位次分配能把它们摊开。
            top = len(routes) - 1
            for rank, ident in enumerate(routes):
                assigned[round(rank * last / top) if top else 0].append(ident)
            route_slot = {r: slot for slot, group in assigned.items() for r in group}
            # 主线地区(洞窟/森林/大楼)按遭遇等级挂到“等级最接近的路线”身后:
            # 月见山跟着 3~4 号道路、宝可梦塔跟着玉虹一带、冠军之路排最后。
            levels = [(avg_level(locs[r]), r) for r in routes]
            for ident in main_areas:
                lv = avg_level(locs[ident])
                near = min(levels, key=lambda t: (abs(t[0] - lv), t[1]))[1]
                assigned[route_slot[near]].append(ident)
                area_sort[ident] = (route_number(near), 0.0, lv, ident)
        else:
            # 有城镇但完全没有路线数据的地区(如阿罗拉):按等级位次均分
            top = len(main_areas) - 1
            for rank, ident in enumerate(main_areas):
                assigned[round(rank * last / top) if top else 0].append(ident)
    else:
        # Degenerate region without canonical anchors: fall back to a plain sort.
        leftovers = sorted(non_anchor, key=lambda i: (route_number(i), avg_level(locs[i]), i))
        anchors = leftovers
        assigned = {i: [] for i in range(len(anchors))}
        side_areas = []

    def _slot_key(i: str) -> tuple:
        """同一城镇段内的排序:路线按号码,地区紧跟挂靠的路线。"""
        if nodes[i] == "route":
            return (route_number(i), 0, 0.0, i)
        return area_sort.get(i, (0, 1.0, avg_level(locs[i]), i))

    ordered: list[str] = []
    for idx, town in enumerate(anchors):
        ordered.append(town)
        group = assigned.get(idx, [])
        group.sort(key=_slot_key)
        ordered.extend(group)
    # 支线区域统一排到主线城镇之后、联盟之前
    ordered.extend(sorted(side_areas, key=lambda i: (avg_level(locs[i]), i)))

    # Guarantee uniqueness in case of overlap.
    seen: set[str] = set()
    ordered = [i for i in ordered if not (i in seen or seen.add(i))]
    for ident in nodes:
        if ident not in seen:
            ordered.append(ident)

    # 合成联盟节点:上游 locations.json 普遍没有"宝可梦联盟"这一地点,
    # 于是补一个虚拟节点挂在最后一个城镇之后,作为挑战四天王/冠军的入口。
    synthetic: dict[str, str] = {}
    league_id = f"{region}-pokemon-league"
    if anchors and not any(
        i.endswith("-pokemon-league") or i == "indigo-plateau" for i in ordered
    ):
        nodes[league_id] = "league"
        synthetic[league_id] = f"{region_zh or region}宝可梦联盟"
        ordered.append(league_id)

    order_val: dict[str, int] = {ident: (n + 1) * 10 for n, ident in enumerate(ordered)}
    total = len(ordered)
    rank = {ident: n + 1 for n, ident in enumerate(ordered)}

    node_out: dict[str, dict] = {}
    for ident in ordered:
        loc = locs.get(ident) or {}
        kind = nodes[ident]
        zh = synthetic.get(ident) or loc.get("zh") or loc.get("name") or ident
        node_out[ident] = {
            "zh": zh,
            "kind": kind,
            "order": order_val[ident],
            "tier": clamp_tier(rank[ident], total),
            "next": [],
            "hub": kind in ("town", "league"),
        }

    edges: set[tuple[str, str]] = set()

    def link(a: str, b: str) -> None:
        if a == b:
            return
        edges.add((a, b))
        edges.add((b, a))

    for a, b in itertools.pairwise(ordered):
        link(a, b)

    # 合成联盟节点默认只与最后一个城镇相连(联盟通常在城镇外)
    if league_id in node_out and anchors:
        for other in reversed(ordered):
            if nodes.get(other) == "town":
                link(league_id, other)
                break

    # Towns link to nearest route on each side so they are not pass-through dead ends.
    for pos, ident in enumerate(ordered):
        if nodes[ident] != "town":
            continue
        for step in (range(pos - 1, -1, -1), range(pos + 1, len(ordered))):
            for other in step:
                if nodes[ordered[other]] == "route":
                    link(ident, ordered[other])
                    break

    for src, dst in edges:
        node_out[src]["next"].append(dst)
    for node in node_out.values():
        node["next"].sort(key=lambda i: order_val[i])

    gateway = ""
    for ident in ordered:
        if ident.endswith("-pokemon-league") or ident == "indigo-plateau":
            gateway = ident
            break
    if not gateway:
        gateway = ordered[-1]

    entry = {
        "zh": locs[ordered[0]].get("_region_zh", ""),
        "gen": REGION_GEN[region],
        "order": REGION_GEN[region],
        "gateway": gateway,
        "nodes": node_out,
    }
    return entry, skipped, ordered


def components(node_ids: list[str], nodes: dict[str, dict]) -> int:
    if not node_ids:
        return 0
    adj = {i: set(nodes[i]["next"]) for i in node_ids}
    remaining = set(node_ids)
    count = 0
    while remaining:
        count += 1
        stack = [remaining.pop()]
        while stack:
            cur = stack.pop()
            for nxt in adj[cur]:
                if nxt in remaining:
                    remaining.discard(nxt)
                    stack.append(nxt)
    return count


def main() -> int:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    regions_meta = data["regions"]
    locs = data["locations"]

    out_regions: dict[str, dict] = {}
    report: list[str] = []
    failures: list[str] = []
    skipped_report: dict[str, list[str]] = {}

    for region in REGION_ORDER:
        zh_meta = regions_meta.get(region, {}).get("zh", region)
        entry, skipped, node_ids = build_region(region, locs, zh_meta)
        zh = zh_meta
        entry["zh"] = zh
        if not entry["nodes"]:
            # 上游 locations.json 完全没有这个地区的记录(如帕底亚) → 不输出空地区
            skipped_report[region] = skipped
            report.append(f"{region}: SKIPPED (no locations in source data)")
            continue
        out_regions[region] = entry
        skipped_report[region] = skipped

        nodes = entry["nodes"]
        bad_tier = [i for i in node_ids if not 1 <= nodes[i]["tier"] <= 8]

        # symmetry check
        asym: list[str] = [
            f"{ident}->{nxt}"
            for ident in node_ids
            for nxt in nodes[ident]["next"]
            if nxt not in nodes or ident not in nodes[nxt]["next"]
        ]

        comps = components(node_ids, nodes)
        report.append(
            f"{region}: nodes={len(node_ids)} components={comps} "
            f"bad_tier={len(bad_tier)} asymmetric={len(asym)} gateway={entry['gateway']!r}"
        )
        if asym:
            failures.append(f"{region}: asymmetric edges: {asym[:5]}")
        if comps > 1:
            failures.append(f"{region}: {comps} connected components")
        if bad_tier:
            failures.append(f"{region}: tier out of range for {bad_tier[:5]}")
        if skipped:
            report.append(f"    skipped canonical towns: {skipped}")

    out = {
        "meta": {"source": "locations.json", "region_count": len(out_regions)},
        "regions": out_regions,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print("=== build_map_graph report ===")
    for line in report:
        print(line)
    print(f"regions={len(out_regions)} total_nodes={sum(len(r['nodes']) for r in out_regions.values())}")
    print(f"wrote {OUT}")

    if failures:
        print("SELF-CHECK FAILED:")
        for fail in failures:
            print("  -", fail)
        return 1
    print("self-check: OK (symmetry, connectivity, tier range)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
