"""特殊区域(外来种)叠加层:让"没有获取途径的宝可梦"归零。

这是一层**可整体移除**的叠加层:
  · 数据:`pw/static/foreign_pools.json`(构建期生成)
  · 生成器:`tools/build_foreign_pools.py`
  · 合并点:`pw/world.py` 的 `_foreign_rows()`(开关 `world.FOREIGN_POOLS`)
删掉 JSON 或把开关置 False,真实分布立刻回到原样 —— 下面有测试盯着这个开关。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DATA = _ROOT / "pw" / "static" / "foreign_pools.json"
LEGENDS = _ROOT / "pw" / "static" / "legendary_ids.json"


def _real(species: str, dex) -> bool:
    """与构建脚本同一判据:排除 Mega/形态/战斗专用。"""
    if species.endswith(("mega", "gmax")):
        return False
    e = dex.species.get(species) or {}
    return not (
        e.get("isNonstandard") or e.get("battleOnly") or e.get("requiredItem")
        or e.get("forme") or (e.get("baseSpecies") and e["baseSpecies"] != species)
    )


def test_overlay_data_is_wellformed():
    assert DATA.exists(), "叠加层数据缺失,先跑 tools/build_foreign_pools.py"
    data = json.loads(DATA.read_text(encoding="utf-8"))
    assert data, "叠加层是空的(生成器可能吃掉自己的输入)"
    from pw.dex import get_dex
    from pw.world import WorldMap

    dex, world = get_dex(), WorldMap()
    legends = set(json.loads(LEGENDS.read_text(encoding="utf-8"))["ids"])
    seen: dict[str, int] = {}
    for key, rows in data.items():
        assert key in world._index, f"挂到了不存在的地点:{key}"
        for r in rows:
            sp = str(r.get("species") or "")
            assert sp in dex.species, f"不存在的物种:{sp}"
            assert sp not in legends, f"传说不该进叠加层:{sp}"
            assert 1 <= int(r.get("min") or 0) <= int(r.get("max") or 0) <= 100
            seen[sp] = seen.get(sp, 0) + 1
    assert all(v == 1 for v in seen.values()), "有物种被挂到多个地点"


def test_every_real_species_has_a_path():
    """真实形态必须都有途径(野外/叠加层/定点/进化链)。"""
    from pw import legendary
    from pw.dex import get_dex
    from pw.world import REGION_ORDER, WorldMap

    dex, world = get_dex(), WorldMap()
    wild = {
        p["species"]
        for region in REGION_ORDER
        for key in world.nodes(region)
        for p in world.wild_pools(key)
    }
    fixed = {
        str(s.get("species"))
        for region in REGION_ORDER
        for s in legendary.sites_for(region)
    }
    # 已达途径 = 野外(含叠加层) ∪ 定点 ∪ 领养家族 ∪ 化石家族 ∪ 传说
    # 直接复用生成器里的两个助手,避免两处判据漂移
    sys.path.insert(0, str(_ROOT / "tools"))
    from build_foreign_pools import _adopted, _families

    from pw.items import BAG_ITEMS, FOSSIL_COMBOS, fossil_species

    fossils = {fossil_species(k) for k in BAG_ITEMS if fossil_species(k)}
    fossils |= set(FOSSIL_COMBOS.values())
    legends = set(json.loads(LEGENDS.read_text(encoding="utf-8"))["ids"])
    reach = wild | fixed | legends
    reach |= _families(dex, _adopted(dex)) | _families(dex, fossils)
    missing = [s for s in dex.species if _real(s, dex) and s not in reach]
    assert not missing, f"仍有无途径的宝可梦:{[(s, dex.species[s].get('zh')) for s in missing[:8]]}"


def test_overlay_switch_removes_the_whole_layer():
    """开关必须真的能整层移除 —— 这是"方便移除"的保证。"""
    from pw import world as W

    assert W.FOREIGN_POOLS is True
    world = W.WorldMap()
    data = json.loads(DATA.read_text(encoding="utf-8"))
    key = next(iter(data))
    with_overlay = [p for p in world.wild_pools(key)
                    if str(p.get("method") or "") == "foreign"]
    assert with_overlay, f"{key} 没有叠加层条目"
    W.FOREIGN_POOLS = False
    W._FOREIGN = None
    try:
        without = [p for p in W.WorldMap().wild_pools(key)
                   if str(p.get("method") or "") == "foreign"]
        assert not without, "关掉开关后仍有外来种"
        # 真实分布必须一字不少
        assert [p["species"] for p in W.WorldMap().wild_pools(key)] == [
            p["species"] for p in world.wild_pools(key)
            if str(p.get("method") or "") != "foreign"
        ]
    finally:
        W.FOREIGN_POOLS = True
        W._FOREIGN = None
