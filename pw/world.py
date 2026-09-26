"""地图:地区 / 节点 / 相邻关系 / 通行规则 / 道馆 / 服务设施。

数据来自 `pw/static/maps.json`(由 tools/build_map_graph.py 生成)与
`pw/static/gyms.json`(由 tools/build_gym_data.py 生成,真实游戏数据)。
若 maps.json 缺失,会在运行时用 locations.json 兜底生成一个最小连通图,
保证插件仍可用。
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

from astrbot.api import logger

from .dex import get_dex
from .items import resolve_item
from .util import clamp

_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# 地区顺序(解锁链):通关上一地区冠军后解锁下一地区
REGION_ORDER = [
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
REGION_GEN = {
    "kanto": 1,
    "johto": 2,
    "hoenn": 3,
    "sinnoh": 4,
    "unova": 5,
    "kalos": 6,
    "alola": 7,
    "galar": 8,
    "paldea": 9,
}
TIER_ZH = {
    1: "安全",
    2: "稍有挑战",
    3: "需要 1 枚徽章",
    4: "需要 2 枚徽章",
    5: "需要 3 枚徽章",
    6: "需要 4 枚徽章",
    7: "需要 5 枚徽章",
    8: "需要 6~7 枚徽章",
}
FLY_COST = 500
TRAVEL_STEPS = 120


def _load_json(name: str) -> dict:
    path = os.path.join(_STATIC, name)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning("宝可梦世界: 读取 %s 失败: %s", name, e)
        return {}
    return data if isinstance(data, dict) else {}


def _pools(loc: dict) -> dict:
    return {
        vg: arr
        for vg, arr in (loc.get("pools") or {}).items()
        if isinstance(arr, list) and arr
    }


@lru_cache(maxsize=1)
def _fallback_map() -> dict:
    """maps.json 缺失时的兜底:每个地区一条线性链(按标识排序)。"""
    from .util import read_json

    locs = (read_json(os.path.join(_STATIC, "locations.json")) or {}).get(
        "locations"
    ) or {}
    regions: dict[str, dict] = {}
    for key, loc in locs.items():
        region = str(loc.get("region") or "")
        if region not in REGION_ORDER or (not _pools(loc) and "route" not in key):
            continue
        regions.setdefault(region, {"nodes": {}})
        kind = (
            "town"
            if any(
                t in key
                for t in ("city", "town", "village", "plateau", "league", "ranch")
            )
            else ("route" if "route" in key else "area")
        )
        regions[region]["nodes"][key] = {
            "zh": loc.get("zh") or loc.get("name") or key,
            "kind": kind,
            "order": 0,
            "tier": 1,
            "next": [],
            "hub": kind == "town",
        }
    for region, data in regions.items():
        nodes = data["nodes"]
        keys = sorted(nodes)
        for i, k in enumerate(keys):
            nodes[k]["order"] = i * 10
            nodes[k]["tier"] = clamp(i // max(1, len(keys) // 8) + 1, 1, 8)
            nb = []
            if i > 0:
                nb.append(keys[i - 1])
            if i + 1 < len(keys):
                nb.append(keys[i + 1])
            nodes[k]["next"] = nb
        data["zh"] = region
        data["gen"] = REGION_GEN.get(region, 1)
        data["order"] = REGION_ORDER.index(region) + 1
        data["gateway"] = keys[-1] if keys else ""
    return {"regions": regions}


class WorldMap:
    """地图只读视图(线程安全、懒加载、单例)。"""

    _instance: WorldMap | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._map = _load_json("maps.json") or _fallback_map()
        self._gyms = _load_json("gyms.json")
        # 只保留真正有节点的地区(上游 locations.json 可能整片缺失,如帕底亚)
        self.regions: dict[str, dict] = {
            r: d
            for r, d in (self._map.get("regions") or {}).items()
            if (d or {}).get("nodes")
        }
        self._index: dict[str, str] = {}
        for region, data in self.regions.items():
            for key in (data.get("nodes") or {}):
                self._index[key] = region

    # ── 基础查询 ──
    def has_region(self, region: str) -> bool:
        return region in self.regions

    def region_zh(self, region: str) -> str:
        return (self.regions.get(region) or {}).get("zh") or region

    def resolve_region(self, query: str) -> str:
        q = str(query or "").strip().lower()
        if not q:
            return ""
        if q in self.regions:
            return q
        for r, data in self.regions.items():
            if q in (str(data.get("zh") or ""), r.lower(), str(data.get("name") or "").lower()):
                return r
        for r, data in self.regions.items():
            if q and (q in str(data.get("zh") or "") or q in r):
                return r
        return ""

    def region_of(self, node: str) -> str:
        return self._index.get(str(node or ""), "")

    def node(self, key: str) -> dict:
        region = self.region_of(key)
        if not region:
            return {}
        return (self.regions[region].get("nodes") or {}).get(key) or {}

    def node_zh(self, key: str) -> str:
        return self.node(key).get("zh") or key

    def nodes(self, region: str) -> dict[str, dict]:
        return (self.regions.get(region) or {}).get("nodes") or {}

    def regions_with_data(self) -> list[str]:
        return [r for r in REGION_ORDER if r in self.regions]

    def start_location(self, region: str) -> str:
        nodes = self.nodes(region)
        if not nodes:
            # 兜底:取任意地区首节点
            for r in self.regions_with_data():
                nodes = self.nodes(r)
                if nodes:
                    break
        if not nodes:
            return ""
        towns = [
            k
            for k, v in nodes.items()
            if v.get("kind") == "town"
        ]
        pool = towns or list(nodes)
        return min(pool, key=lambda k: int(nodes[k].get("order", 0)))

    def neighbors(self, key: str) -> list[str]:
        return [n for n in (self.node(key).get("next") or []) if n in self._index]

    def is_hub(self, key: str) -> bool:
        return bool(self.node(key).get("hub"))

    def tier(self, key: str) -> int:
        return int(self.node(key).get("tier", 1) or 1)

    def kind(self, key: str) -> str:
        return str(self.node(key).get("kind") or "area")

    def tier_label(self, key: str) -> str:
        return TIER_ZH.get(self.tier(key), "未知")

    def gateway(self, region: str) -> str:
        return (self.regions.get(region) or {}).get("gateway") or ""

    def is_gateway(self, key: str) -> bool:
        return bool(key) and self.gateway(self.region_of(key)) == key

    def list_towns(self, region: str) -> list[str]:
        nodes = self.nodes(region)
        return sorted(
            (k for k, v in nodes.items() if v.get("kind") == "town"),
            key=lambda k: int(nodes[k].get("order", 0)),
        )

    # ── 野生分布 ──
    def wild_pools(self, key: str, methods: set[str] | None = None) -> list[dict]:
        """返回 [{species, min, max, method, rarity}],按当前地区默认版本组。"""
        dex = get_dex()
        region = self.region_of(key)
        vg = dex.location_default_vg(key) if region else ""
        rows = dex.location_pools(key, vg, methods=methods, include_special=True)
        return [
            {
                "species": r.get("species") or "",
                "min": int(r.get("min", 1) or 1),
                "max": int(r.get("max", r.get("min", 1)) or 1),
                "method": str(r.get("method") or ""),
                "rarity": int(r.get("chance", 1) or 1),
            }
            for r in rows
            if r.get("species")
        ]

    def encounter_methods(self, key: str) -> list[str]:
        return sorted({p["method"] for p in self.wild_pools(key) if p["method"]})

    # ── 道馆 / 联赛 ──
    def region_gyms(self, region: str) -> dict:
        return (self._gyms.get("regions") or {}).get(region) or {}

    def gyms(self, region: str) -> list[dict]:
        return list(self.region_gyms(region).get("gyms") or [])

    def gym_at(self, region: str, location: str) -> dict:
        for g in self.gyms(region):
            if g.get("location") == location:
                return g
        return {}

    def elite4(self, region: str) -> list[dict]:
        return list(self.region_gyms(region).get("elite4") or [])

    def champion(self, region: str) -> dict:
        return dict(self.region_gyms(region).get("champion") or {})

    def gym_data_ok(self, region: str) -> bool:
        g = self.region_gyms(region)
        return bool(g.get("gyms")) and bool(g.get("elite4") or g.get("champion"))

    def next_gym(self, region: str, badges: list[str]) -> dict:
        done = {int(b.split(":")[1]) for b in badges if str(b).startswith(f"{region}:")}
        for g in self.gyms(region):
            if int(g.get("order", 0)) not in done:
                return g
        return {}

    # ── 通行规则 ──
    def travel_check(
        self,
        trainer,
        target: str,
        *,
        locked_until: dict | None = None,
        by_fly: bool = False,
    ) -> tuple[bool, str]:
        """返回 (是否可前往, 说明)。团规优先于一切。"""
        if not target or target not in self._index:
            return False, "❌ 没有这个地点。"
        region = self.region_of(target)
        cur = trainer.location
        if target == cur:
            return False, "你已经在这里了。"
        # 地区解锁
        unlocked = trainer.data.get("unlocked_regions") or [trainer.region]
        if region not in unlocked:
            prev = REGION_ORDER[REGION_ORDER.index(region) - 1] if region in REGION_ORDER else ""
            return (
                False,
                f"🚧 {self.region_zh(region)}尚未开放 —— 需要先成为{self.region_zh(prev)}冠军。",
            )
        # 事件封锁
        lock = (locked_until or {}).get(target)
        if lock is not None and not by_fly:
            return False, f"🚫 这里暂时无法进入(事件封锁中,第 {lock} 天解除)。"
        # 徽章门槛(危险度)
        if not by_fly:
            allowed = trainer.badge_count(region) + 1
            if self.tier(target) > allowed:
                return (
                    False,
                    f"⚠️ {self.node_zh(target)}危险度过高({self.tier_label(target)})"
                    f" —— 先拿下{self.region_zh(region)}更多徽章"
                    f"(当前 {trainer.badge_count(region)} 枚)。",
                )
        # 跨地区:只能从枢纽(联赛/港口)出发,且需要冠军旗标
        cur_region = self.region_of(cur)
        has_pass = trainer.flag(f"champion:{cur_region}") or trainer.flag(
            f"port:{cur_region}"
        )
        if region != cur_region and not has_pass:
            return False, "🚢 跨地区需要先通关当前地区(或从港口乘船)。"
        if by_fly:
            if not self.can_fly(trainer):
                return False, "🚁 你还没有飞行许可(同一地区集齐 3 枚徽章后开放)。"
            if target not in trainer.data.get("visited", []):
                return False, "🚁 只能飞到去过的城镇。"
            if not self.is_hub(target):
                return False, "🚁 只能飞到城镇(宝可梦中心所在地)。"
            if trainer.money < FLY_COST:
                return False, f"🚁 机票需要 {FLY_COST}₽,你的钱不够。"
            return True, ""
        # 步行:必须相邻
        if target not in self.neighbors(cur):
            return (
                False,
                f"🗺️ 只能前往相邻地点。{self.node_zh(cur)} 附近有:"
                + "、".join(self.node_zh(n) for n in self.neighbors(cur)),
            )
        return True, ""

    def can_fly(self, trainer) -> bool:
        return trainer.badge_count() >= 3 or bool(trainer.flag("fly"))

    def where_am_i(self, trainer) -> str:
        region = trainer.region
        loc = trainer.location
        return f"{self.region_zh(region)} · {self.node_zh(loc)}"

    def services(self, key: str) -> list[str]:
        """该地点提供的服务:center / mart / gym / league / trial。"""
        out = []
        if self.is_hub(key):
            out += ["center", "mart"]
        region = self.region_of(key)
        if region and self.gym_at(region, key):
            out.append("gym")
        if self.is_gateway(key):
            out.append("league")
        return out

    def locations_with_species(self, species: str, limit: int = 12) -> list[dict]:
        """反查:哪些地点会出现该物种(基于真实地点分布)。"""
        out: list[dict] = []
        for key in self._index:
            for p in self.wild_pools(key):
                if p["species"] == species:
                    out.append(
                        {
                            "location": key,
                            "zh": self.node_zh(key),
                            "region": self.region_of(key),
                            "min": p["min"],
                            "max": p["max"],
                            "method": p["method"],
                        }
                    )
                    break
            if len(out) >= limit:
                break
        return out

    def region_unlock(self, region: str) -> dict:
        """地区解锁信息(给玩家看进度)。"""
        return {
            "region": region,
            "zh": self.region_zh(region),
            "gen": REGION_GEN.get(region, 0),
            "gyms": len(self.gyms(region)),
            "nodes": len(self.nodes(region)),
        }

    def shop_stock(self, key: str) -> list[str]:
        """商店货架(按徽章数递增);返回道具 key 列表。"""
        return list(SHOP_STOCK)


SHOP_STOCK = [
    "poke-ball",
    "potion",
    "antidote",
    "super-potion",
    "great-ball",
    "revive",
    "paralyze-heal",
    "burn-heal",
    "ice-heal",
    "awakening",
    "fresh-water",
    "soda-pop",
    "lemonade",
    "super-potion",
    "hyper-potion",
    "ultra-ball",
    "full-heal",
    "max-potion",
    "max-revive",
    "full-restore",
    "x-attack",
    "x-defense",
    "x-speed",
    "x-special",
    "dire-hit",
]

EVO_STONE_STOCK = [
    "fire-stone",
    "water-stone",
    "thunder-stone",
    "leaf-stone",
    "moon-stone",
    "sun-stone",
    "shiny-stone",
    "dusk-stone",
    "dawn-stone",
    "ice-stone",
]


def item_price(key: str, *, badge_count: int = 0, discount: float = 1.0) -> int:
    """道具价格:基础价按类别 + 徽章折扣 + 世界事件折扣。"""
    r = resolve_item(key)
    if not r:
        return 0
    k, entry = r
    kind = str(entry.get("kind") or "")
    base = {
        "ball": 200,
        "medicine": 300,
        "status": 200,
        "revive": 1500,
        "pp": 800,
        "battle": 500,
        "berry": 100,
        "rare": 4000,
        "stone": 3000,
        "evo": 5000,
    }.get(kind, 500)
    if k in ("master-ball", "rare-candy"):
        base = 20000
    if k == "poke-ball":
        base = 200
    elif k == "great-ball":
        base = 600
    elif k == "ultra-ball":
        base = 1200
    discount = clamp(1.0 - 0.02 * int(badge_count), 0.7, 1.0) * float(discount or 1.0)
    return max(10, round(base * discount / 10.0) * 10)
