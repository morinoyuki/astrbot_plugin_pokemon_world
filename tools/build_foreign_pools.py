"""把"目前没有任何获取途径"的宝可梦挂到现有地点的**特殊区域**池里。

背景:图鉴里有一批宝可梦(伽勒尔/帕底亚与部分地区形态为主)在本作既不在真实
野外分布、也不是神兽定点/化石/领养/进化链 —— 玩家永远拿不到。

这一层是**叠加层**,不是重建:
  · 现有 `locations.json` 的真实分布**一个字节都不改**
  · 产物只有 `pw/static/foreign_pools.json`,由 `WorldMap.wild_pools()` 合并
  · 想移除:删掉那个 JSON + `pw/world.py` 里 `_merge_foreign()` 一处调用
    (或把 `world.FOREIGN_POOLS` 置 False),现有分布立刻回到原样

运行期不联网 ✅;判定所用名单来自仓库内的数据文件。
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pw.dex import get_dex
from pw.items import BAG_ITEMS, FOSSIL_COMBOS, fossil_species
from pw.world import REGION_ORDER, WorldMap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "pw", "static", "foreign_pools.json")
LEGENDS = os.path.join(ROOT, "pw", "static", "legendary_ids.json")

# 属性 → 主题关键词(按地点 key 匹配,挑不到就退化为"该属性已有分布的地点")
THEME = {
    "Water": ("sea", "lake", "bay", "harbor", "river", "water", "beach"),
    "Fire": ("volcano", "cave", "mountain", "ember", "path"),
    "Electric": ("power", "plant", "cave", "lab"),
    "Ice": ("ice", "snow", "frost", "cave"),
    "Rock": ("cave", "mountain", "rock", "tunnel", "mine"),
    "Ground": ("desert", "cave", "tunnel", "ruins"),
    "Grass": ("forest", "wood", "garden", "park", "meadow"),
    "Bug": ("forest", "wood", "garden"),
    "Poison": ("marsh", "swamp", "cave", "sewer"),
    "Flying": ("mountain", "bridge", "sky", "route"),
    "Dragon": ("cave", "mountain", "tower", "ruins"),
    "Ghost": ("tower", "cemetery", "cave", "ruins"),
    "Psychic": ("lake", "tower", "ruins", "cave"),
    "Fighting": ("dojo", "mountain", "cave"),
    "Dark": ("cave", "forest", "tower"),
    "Steel": ("cave", "factory", "tunnel", "plant"),
    "Fairy": ("forest", "flower", "garden", "lake"),
    "Normal": ("route", "field", "park", "path"),
}


def _real(species: str, dex) -> bool:
    """过滤掉 Mega/形态/战斗专用等**不可独立获得**的条目。"""
    if species.endswith(("mega", "megax", "megay", "megaz", "gmax")):
        return False
    e = dex.species.get(species) or {}
    return not (
        e.get("isNonstandard") or e.get("battleOnly") or e.get("requiredItem")
        or e.get("forme") or (e.get("baseSpecies") and e["baseSpecies"] != species)
    )


def _adopted(dex) -> set[str]:
    """`/领养` 覆盖的区域(`ADOPT_TRIOS` 里是中文名,必须归一化成 key)。"""
    with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as f:
        src = f.read()
    block = src.split("ADOPT_TRIOS", 1)[1].split("}", 1)[0]
    out: set[str] = set()
    for name in re.findall(r'"([^"]+)"', block):
        r = dex.resolve_species(name)
        if r:
            out.add(r[0])
    return out


def _families(dex, seeds: set[str]) -> set[str]:
    """把种子扩成整条进化家族(前后都走)。"""
    out = set(seeds)
    for _ in range(3):
        for sp in list(out):
            out |= set((dex.species.get(sp) or {}).get("evos") or [])
            pre = (dex.species.get(sp) or {}).get("prevo")
            if pre:
                out.add(str(pre))
    return out


def main() -> int:
    dex = get_dex()
    world = WorldMap()

    wild: set[str] = set()
    themes: dict[str, list[str]] = {}
    loc_levels: dict[str, tuple[int, int]] = {}
    for region in REGION_ORDER:
        for key in world.nodes(region):
            pools = world.wild_pools(key)
            # 必须排除上一次生成的叠加层,否则第二次运行会看到"缺 0 只"并把
            # JSON 覆写成空 —— 生成器吃掉自己的输入(实测踩过)
            real_pools = [p for p in pools if str(p.get("method") or "") != "foreign"]
            if not real_pools:
                continue
            wild.update(str(p["species"]) for p in real_pools)
            lv = ([int(p.get("min") or 5) for p in real_pools]
                  + [int(p.get("max") or 30) for p in real_pools])
            loc_levels[key] = (max(2, min(lv)), min(70, max(lv)))
            e = dex.species.get(real_pools[0]["species"]) or {}
            themes.setdefault(str((e.get("types") or ["Normal"])[0]), []).append(key)

    from pw import legendary

    fixed = {
        str(s.get("species"))
        for region in REGION_ORDER
        for s in legendary.sites_for(region)
    }
    with open(LEGENDS, encoding="utf-8") as f:
        legends = set(json.load(f).get("ids") or [])

    fossils = {fossil_species(k) for k in BAG_ITEMS if fossil_species(k)}
    fossils |= set(FOSSIL_COMBOS.values())

    # 已达途径 = 真实野外 ∪ 神兽定点 ∪ 领养家族 ∪ 化石家族 ∪ **所有传说**
    # (传说只走定点:没定点的宁可暂时拿不到,也不能混进草丛 —— 有回归测试盯着)
    reach = set(wild) | fixed | legends
    reach |= _families(dex, _adopted(dex)) | _families(dex, fossils)

    missing = sorted(s for s in dex.species if _real(s, dex) and s not in reach)

    out: dict[str, list[dict]] = {}
    rr: dict[str, int] = {}                 # 每个属性轮流挑地点,别全堆在一个地方
    for sp in missing:
        e = dex.species[sp]
        t = str((e.get("types") or ["Normal"])[0])
        cands = [k for k in themes.get(t, []) if any(w in k for w in THEME.get(t, ()))]
        cands = cands or themes.get(t) or []
        keys = list(loc_levels)
        if cands:
            pick = cands[rr.get(t, 0) % len(cands)]
            rr[t] = rr.get(t, 0) + 1
        else:
            pick = keys[rr.get("_", 0) % len(keys)] if keys else ""
            rr["_"] = rr.get("_", 0) + 1
        if not pick:
            continue
        lo, hi = loc_levels.get(pick, (5, 30))
        out.setdefault(pick, []).append({
            "species": sp, "min": lo, "max": hi, "rarity": "rare",
            "note": "外来种(特殊区域)",
        })

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)

    # ── 自检 ──
    seen: dict[str, int] = {}
    for rows in out.values():
        for r in rows:
            seen[r["species"]] = seen.get(r["species"], 0) + 1
    dup = [k for k, v in seen.items() if v > 1]
    assert not dup, f"有宝可梦被挂到多个地点:{dup[:5]}"
    assert len(seen) == len(missing), f"覆盖不全:{len(seen)}/{len(missing)}"
    leaked = [s for s in seen if s in legends]
    assert not leaked, f"传说混进叠加层:{leaked[:5]}"
    print(f"缺途径 {len(missing)} 只 → 挂到 {len(out)} 个地点的特殊区域")
    print(f"传说(只走定点){len(legends)} 只未纳入;领养家族 {len(_adopted(dex))} 只已排除")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
