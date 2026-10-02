"""地图:地区 / 节点 / 相邻关系 / 通行规则 / 道馆 / 服务设施。

数据来自 `pw/static/maps.json`(由 tools/build_map_graph.py 生成)与
`pw/static/gyms.json`(由 tools/build_gym_data.py 生成,真实游戏数据)。
若 maps.json 缺失,会在运行时用 locations.json 兜底生成一个最小连通图,
保证插件仍可用。
"""

from __future__ import annotations

import heapq
import json
import os
import re
from functools import lru_cache

from astrbot.api import logger

from .dex import _norm, get_dex
from .items import resolve_item
from .mega import (
    KEY_STONE,
    MEGA_STONE_BADGES,
)
from .mega import (
    all_stones as all_mega_stones,
)
from .mega import (
    stones_for as mega_stones_for,
)
from .util import clamp, game_day

_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# 地区顺序(解锁链):通关上一地区冠军后解锁下一地区
# ── 特殊区域叠加层(可选,想移除整层只改这一个开关)───────────────
# 图鉴里有一批宝可梦(伽勒尔/帕底亚与部分地区形态为主)在本作没有任何获取途径:
# 既不在真实野外分布,也不是定点/化石/领养/进化链。构建脚本把它们按属性挂到
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
# 尚无地图数据、但名字要能显示的地区(帕底亚还没做地图,可它的御三家能领养)
_EXTRA_REGION_ZH = {
    "paldea": "帕底亚",
}
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

# 这些"遭遇方式"不是随机野生遭遇,不应进入 /探索 与野外反查:
#  · gift / gift-egg / npc-trade —— 赠送与交换
#  · static / only-one / snag —— 定点与抢夺
#  · max-raid / dynamax-adventure —— 团体战与极巨大冒险
#  · 其余为配信/外设(宝可梦频道、Ranger、Colosseum 特典、吼吼鲸水桶等)
# 另外所有以 `-special` 结尾的方法都要剔除 —— PokeAPI 的 Let's Go "空中特殊遭遇"
# 数据不带可信等级区间(常见 min=3,max=56),会把急冻鸟/闪电鸟/火焰鸟/快龙
# 塞进 1 号道路。实测这些条目共 72 条、覆盖 3 种传说宝可梦。
NON_WILD_METHODS = {
    # 游走传说宝可梦:留给"稀有现身"每日事件,不做成路边随机遭遇
    "roaming-grass",
    "roaming-water",
    "gift",
    "gift-egg",
    "npc-trade",
    "static",
    "only-one",
    "interact",
    "max-raid",
    "dynamax-adventure",
    "snag",
    "snag-rematch",
    "colosseum-bonus-disc-jpn",
    "colosseum-bonus-disc-us",
    "pokemon-channel-pal",
    "pokemon-ranger",
    "wailmer-pail",
    # 下面这些是"定点/道具触发/扫描"类遭遇,探索(草丛/水面/钓鱼)遇不到。
    # 之前放行会让 /图鉴 的野外分布与委托的"可完成性校验"都把玩家引到遇不到的物种上。
    "island-scan",
    "pokeflute",
    "devon-scope",
    "squirt-bottle",
    "pokespot",
    "sky-ambush",
    "wanderer",
    "wanderer-water",
    "chase-water",
}
# 用于"核心等级区间"的方法(草丛/水面),用于收敛异常等级区间
CORE_METHODS = {
    "walk",
    "overworld",
    "overworld-dirt",
    "overworld-water",  # Let's Go 的水面即"核心"地形
    "grass-spots",
    "dark-grass",
    "surf",
}

# 上游地点中文名混有繁体(主要来自岛屿/城市名),这里做一次安全转简。
# 只包含"繁→简"确定且不会破坏已有简体名的映射。
_T2S = str.maketrans(
    {
        "號": "号",
        "島": "岛",
        "碼": "码",
        "頭": "头",
        "園": "园",
        "羅": "罗",
        "藍": "蓝",
        "灣": "湾",
        "爾": "尔",
        "奧": "奥",
        "樂": "乐",
        "歐": "欧",
        "納": "纳",
        "樹": "树",
        "馬": "马",
        "礦": "矿",
        "關": "关",
        "圓": "圆",
        "環": "环",
        "離": "离",
        "點": "点",
        "緣": "缘",
        "衆": "众",
        "會": "会",
        "國": "国",
        "學": "学",
        "車": "车",
        "東": "东",
        "門": "门",
        "長": "长",
        "陽": "阳",
        "雲": "云",
        "電": "电",
        "龍": "龙",
        "劍": "剑",
        "銀": "银",
        "鋼": "钢",
        "鐵": "铁",
        "紅": "红",
        "綠": "绿",
        "黃": "黄",
        "陸": "陆",
        "橋": "桥",
        "廳": "厅",
        "場": "场",
        "隊": "队",
        "華": "华",
        "萬": "万",
        "縣": "县",
        "鎮": "镇",
        "區": "区",
        "鄉": "乡",
        "燈": "灯",
        "爐": "炉",
        "館": "馆",
        "營": "营",
        "徑": "径",
        "巖": "岩",
        "嶺": "岭",
        "淵": "渊",
        "溝": "沟",
        "灘": "滩",
        "澗": "涧",
        "甯": "宁",
        "廣": "广",
        "廢": "废",
        "誕": "诞",
        "覺": "觉",
        "遙": "遥",
        "憶": "忆",
        "戰": "战",
        "爭": "争",
        "輪": "轮",
        "豐": "丰",
        "遺": "遗",
        "跡": "迹",
        "蔥": "葱",
        "鬱": "郁",
        "鏡": "镜",
        "閃": "闪",
        "連": "连",
        "終": "终",
        "結": "结",
        "亞": "亚",
        "帶": "带",
        "個": "个",
        "劃": "划",
        "動": "动",
        "塊": "块",
        "壞": "坏",
        "寶": "宝",
        "歲": "岁",
        "歸": "归",
        "為": "为",
        "無": "无",
        "牆": "墙",
        "現": "现",
        "產": "产",
        "祕": "秘",
        "種": "种",
        "稱": "称",
        "積": "积",
        "築": "筑",
        "總": "总",
        "聯": "联",
        "聽": "听",
        "腦": "脑",
        "臺": "台",
        "與": "与",
        "葉": "叶",
        "藝": "艺",
        "記": "记",
        "設": "设",
        "話": "话",
        "語": "语",
        "誌": "志",
        "認": "认",
        "課": "课",
        "誰": "谁",
        "談": "谈",
        "諾": "诺",
        "讀": "读",
        "貝": "贝",
        "資": "资",
        "費": "费",
        "賞": "赏",
        "質": "质",
        "購": "购",
        "較": "较",
        "載": "载",
        "軍": "军",
        "軟": "软",
        "農": "农",
        "遊": "游",
        "運": "运",
        "達": "达",
        "遞": "递",
        "適": "适",
        "選": "选",
        "郵": "邮",
        "醫": "医",
        "釋": "释",
        "鐘": "钟",
        "際": "际",
        "雙": "双",
        "雛": "雏",
        "靈": "灵",
        "靜": "静",
        "頂": "顶",
        "順": "顺",
        "須": "须",
        "風": "风",
        "飛": "飞",
        "驗": "验",
        "髮": "发",
        "鬥": "斗",
        "魯": "鲁",
        "鳥": "鸟",
        "鳳": "凤",
        "鳴": "鸣",
        "鷹": "鹰",
        "麗": "丽",
        "毀": "毁",
        "湧": "涌",
        "業": "业",
        "夢": "梦",
        "串": "串",
        "挑": "挑",
        "極": "极",
        "間": "间",
        "空": "空",
        "海": "海",
        "登": "登",
        "冰": "冰",
        "山": "山",
        "抉": "抉",
        "擇": "择",
        "聚": "聚",
        "圖": "图",
        "書": "书",
        "廠": "厂",
        "雞": "鸡",
        "鴨": "鸭",
        "豬": "猪",
        "貓": "猫",
        "狗": "狗",
        "騎": "骑",
        "峽": "峡",
        "濱": "滨",
        "瀨": "濑",
        "檜": "桧",
        "橙": "橙",
        "煙": "烟",
        "囪": "囱",
        "釜": "釜",
        "炎": "炎",
        "炮": "炮",
        "塔": "塔",
        "墳": "坟",
        "揚": "扬",
        "揮": "挥",
        "掃": "扫",
        "捕": "捕",
        "獲": "获",
        "獵": "猎",
        "獸": "兽",
        "蟲": "虫",
        "魚": "鱼",
        "蝦": "虾",
        "蟹": "蟹",
        "殼": "壳",
        "鑽": "钻",
        "鋁": "铝",
        "銅": "铜",
        "鉛": "铅",
        "錫": "锡",
        "鎂": "镁",
        "鈦": "钛",
        "鋅": "锌",
        "鎳": "镍",
        "銹": "锈",
        "鏈": "链",
    }
)


# 不是"地点"的伪条目,即便地图数据里残留也不展示:
#   roaming-{地区} —— 游走宝可梦的抽象容器
#   unknown-*      —— Pokéwalker/事件占位符
#   {地区}-pokemart / {地区}-pokecenter —— PokeAPI 里通用的商店/中心容器
PSEUDO_LOCATION_RE = re.compile(r"^(roaming-|unknown-)|.*-(pokemart|pokecenter)$")


def is_wild_method(method: str) -> bool:
    m = str(method or "")
    if not m or m in NON_WILD_METHODS:
        return False
    return not m.endswith("-special")


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


# 少数地图节点上游没有中文名(也不是道路/水路),这里补齐。
# 放模块级而不是类属性:类里的可变字面量会被判为"可变默认值"(RUF012)。
_EXTRA_ZH = {
    "mirage-island": "幻影岛",
    "kalos-berry-fields": "卡洛斯树果园",
}


class WorldMap:
    """地图只读视图(线程安全、懒加载、单例)。"""

    _instance: WorldMap | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    @staticmethod
    def _clean_zh(zh: str, region: str) -> str:
        """清洗上游地点中文名。

        上游数据按**英文名**匹配中文名,"Route 16" 这种同名地点会互相覆盖
        (关都16号道路拿到了阿罗拉的"16號道路(阿羅拉)")。因此:
          · 道路/水路名一律按标识符重新生成(彻底摆脱串区与繁体);
          · 其余名字去掉尾部的括号标注(地区/世代/版本),地区已在 UI 中展示。
        """
        text = str(zh or "").strip()
        text = re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", text).strip()
        return text.translate(_T2S)

    ROUTE_NUM_RE = re.compile(r"(?:^|-)(?:sea-)?route-(\d+)$")

    @classmethod
    def _localize_zh(cls, key: str, zh: str, region: str) -> str:
        if str(key) in _EXTRA_ZH:
            return _EXTRA_ZH[str(key)]
        m = cls.ROUTE_NUM_RE.search(str(key or ""))
        if m:
            num = int(m.group(1))
            water = "-sea-route-" in str(key)
            return f"{num}号水路" if water else f"{num}号道路"
        return cls._clean_zh(zh, region) or str(key)

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
        # 第一遍:剔除伪地点并建立全局索引(跨地区剪枝需要完整索引)
        for region, data in self.regions.items():
            nodes = data.get("nodes") or {}
            for key in [k for k in nodes if PSEUDO_LOCATION_RE.match(k)]:
                nodes.pop(key, None)
            for key in nodes:
                self._index[key] = region
        # 第二遍:修正中文名、剪掉指向伪地点的相邻关系
        for region, data in self.regions.items():
            for key, node in (data.get("nodes") or {}).items():
                node["zh"] = self._localize_zh(key, node.get("zh") or "", region)
                node["next"] = [
                    n for n in (node.get("next") or []) if n in self._index
                ]

    # ── 基础查询 ──
    def has_region(self, region: str) -> bool:
        return region in self.regions

    def region_zh(self, region: str) -> str:
        r = str(region or "")
        return (self.regions.get(r) or {}).get("zh") or _EXTRA_REGION_ZH.get(r) or r

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
        """返回该地点可随机遭遇的野生宝可梦 [{species,min,max,method,rarity}]。

        只保留真正的野生遭遇方式(is_wild_method),并按"核心等级区间"收敛
        异常等级(PokeAPI 的 Let's Go 空中遭遇常见 min=3/max=56)。
        """
        dex = get_dex()
        region = self.region_of(key)
        if not region:
            return []
        vg = dex.location_default_vg(key)
        rows = dex.location_pools(key, vg, include_special=True)
        rows = [r for r in rows if is_wild_method(str(r.get("method") or ""))]
        rows = list(rows)          # 叠加层已删除:只用真实分布(2026-09 起)
        if methods is not None:
            rows = [r for r in rows if r["method"] in methods]
        if not rows:
            return []
        core = [r for r in rows if r["method"] in CORE_METHODS]
        if core:
            core_lo = min(int(r["min"]) for r in core)
            core_hi = max(int(r["max"]) for r in core)
        else:
            core_lo = min(int(r["min"]) for r in rows)
            core_hi = max(int(r["max"]) for r in rows)
        out = []
        for r in rows:
            lo = max(1, int(r["min"]))
            hi = max(lo, int(r["max"]))
            if r["method"] not in CORE_METHODS:
                # 侧池(空中/垂钓/摇树/定点等)不得脱离该地点的核心等级区间
                # 侧池必须落进核心区间:下限抬到核心下限(不超过核心上限),
                # 上限压到核心上限。旧写法在 hi<lo 时执行 `hi = lo`,等于把上限
                # **抬回原值** —— tier1 的 3 号道路因此能刷出 Lv35 的水面宝可梦。
                lo = min(max(lo, core_lo), core_hi)
                hi = max(lo, min(hi, core_hi))
            out.append(
                {
                    "species": r["species"],
                    "min": lo,
                    "max": hi,
                    "method": r["method"],
                    "rarity": int(r.get("chance", 1) or 1),
                }
            )
        return [r for r in out if r["species"]]

    def gyms_at(self, region: str, location: str) -> list[dict]:
        """该地点的所有道馆(少数地点在原作里有两个道馆,例如城都的卡吉镇/湛蓝市)。

        构建数据时这些城市在 maps 里缺失,两个道馆被并到同一节点;
        `gym_at` 只返回第一个 → 第二枚徽章永远拿不到 → `/联盟` 要求全部徽章 →
        冠军拿不到 → 下一个地区永久锁死(整条解锁链死锁)。
        """
        return [g for g in self.gyms(region) if g.get("location") == location]

    def nearest_hub(self, current: str, candidates) -> str:
        """在候选城镇里挑一个到 current **实际路网距离最近**的。

        不能用"访问顺序的最后一项":`visited` 是按**首次到访**顺序追加的,
        玩家折返回老城镇后失败,旧实现会把他送回那个很远的城镇 ——
        实测站在 1 号道路(真新镇 1 跳)却被送回 6 跳外的深灰市。
        """
        cands = [str(c) for c in (candidates or []) if str(c) in self._index]
        if not cands:
            return ""
        if not current or current not in self._index:
            return cands[-1]
        if current in cands:
            return current
        from collections import deque

        dist: dict[str, int] = {current: 0}
        dq = deque([current])
        remaining = set(cands)
        found: dict[str, int] = {}
        while dq and remaining:
            node = dq.popleft()
            d = dist[node]
            for nxt in self.neighbors(node):
                if nxt in dist:
                    continue
                dist[nxt] = d + 1
                if nxt in remaining:
                    found[nxt] = d + 1
                    remaining.discard(nxt)
                dq.append(nxt)
        if not found:
            return cands[-1]      # 路网断连(理论上不会):退回旧行为
        # 同距离时按候选顺序取第一个,保证结果稳定可测
        return min(cands, key=lambda k: (found.get(k, 10 ** 9), cands.index(k)))

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

    def _route_cap(self, region: str, current: str, badges: list[str]) -> int:
        """通往"下一个道馆"的最优路线上的**最大危险度**(瓶颈路径)。

        用最小化"路径上最大 tier"的 Dijkstra:玩家至少应被允许走到下一个道馆,
        但也不能因此把远处的高危区域全部开放(只开放到该路线需要的程度)。
        """
        gym = self.next_gym(region, badges)
        dest = str((gym or {}).get("location") or "")
        if not dest or dest not in self._index:
            return 0
        if self.region_of(current) != self.region_of(dest):
            return 0
        best: dict[str, int] = {current: self.tier(current)}
        heap = [(best[current], current)]
        while heap:
            cost, node = heapq.heappop(heap)
            if node == dest:
                return cost
            if cost > best.get(node, 10 ** 9):
                continue
            for nxt in self.neighbors(node):
                cand = max(cost, self.tier(nxt))
                if cand < best.get(nxt, 10 ** 9):
                    best[nxt] = cand
                    heapq.heappush(heap, (cand, nxt))
        return best.get(dest, 0)

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
            left = max(1, int(lock) - game_day())
            return False, f"🚫 这里暂时无法进入(事件封锁中,还有 {left} 天解除)。"
        # 徽章门槛(危险度)。
        # 关键:must 保证玩家永远能走到"下一个道馆"。maps.json 的 tier 是按路线
        # 名次启发式生成的,道馆城镇的 tier 并不等于"第几枚徽章"(关都:深灰2 /
        # 华蓝4 / 枯叶6 / 玉虹7 / 浅红8 / 金黄7 / 红莲8),于是 `badges+1` 的门槛
        # 让关都从第 3 枚起永远进不去、合众 0 徽章时连第一个道馆都到不了 →
        # 联盟打不了 → 冠军拿不到 → 下一个地区永久锁死(实测 4/8 地区死锁)。
        # 规则改成:还能进入"通往下一个道馆的**必经路线**上所需的最高危险度"。
        if not by_fly:
            allowed = max(
                trainer.badge_count(region) + 1,
                self._route_cap(region, cur, trainer.badges),
            )
            if self.tier(target) > allowed:
                return (
                    False,
                    f"⚠️ {self.node_zh(target)}危险度过高({self.tier_label(target)})"
                    f" —— 先拿下{self.region_zh(region)}更多徽章"
                    f"(当前 {trainer.badge_count(region)} 枚)。",
                )
        # 跨地区:只能从枢纽(联赛)出发,且需要该地区的冠军旗标。
        # 这里原来还有一个 `port:{region}` 备选条件,但全仓库**没有任何地方设置它**
        # (只有这一处读取)→ 死分支:玩家永远看不到"从港口乘船"这条路,
        # 提示文字却在承诺它。已删除,改为与实现一致的说法。
        cur_region = self.region_of(cur)
        if region != cur_region and not trainer.flag(f"champion:{cur_region}"):
            return False, (
                f"🚢 跨地区需要先成为{self.region_zh(cur_region)}冠军"
                "(通关当地联盟)。"
            )
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

    def find_location(self, query: str, region: str = "") -> str:
        """地点名 → 地图节点 key(中文名/英文名/标识/模糊)。

        顺序很有讲究:**先在本地区节点里做显示名精确匹配**,再问 dex 的别名/编号
        解析 —— dex 的地点索引是早期生成的产物,改名/新增节点后可能过期,
        若先问它就会把「冠军之路」解析到别的节点,表现为 `/前往` 没反应(卡关)。
        """
        q = str(query or "").strip()
        if not q:
            return ""
        try:
            pool = [k for k in self._index if not region or self.region_of(k) == region]
        except Exception:
            pool = []
        nq = _norm(q)
        for k in pool:                      # ① 本地区显示名精确(忽略大小写/符号)
            if nq and _norm(self.node_zh(k)) == nq:
                return k
        key = get_dex().find_location(q, region)
        if key and (not region or self.region_of(key) == region):
            return key
        if not any(ch.isdigit() for ch in q):
            # ② 包含匹配:带数字的编号地点不做这一步,否则「1号道路」会撞上「11号道路」
            for k in pool:
                if nq and nq in _norm(self.node_zh(k)):
                    return k
        return key or ""

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

    def shop_stock(self, key: str, badges: int = 0, *, trainer=None) -> list[str]:
        """商店货架(按徽章数递增);返回道具 key 列表。

        注意:`badges` 默认 0 只为兼容旧调用,命令层要传 `trainer.badge_count()`。
        旧实现对所有地点/徽章返回同一份固定清单 —— 于是 60 个道具(11 种特殊球、
        5 种树果、24 个进化道具、PP 药、X 道具、除虫喷雾以外的进化石)
        在游戏里**没有任何获取途径**。

        传了 `trainer` 时,Mega 石会按「已获得钥石 + 已捕获对应原种」过滤:
        92 块石头全摆在货架上没有意义,还挤爆商店。
        """
        out: list[str] = []
        for tier, keys in SHOP_TIERS:
            if tier <= int(badges or 0):
                out.extend(keys)
        # 招式机与道具同一份货架(按同一套徽章档位解锁)
        out.extend(tm_stock(badges))
        visible = _visible_mega_stones(trainer)
        if visible is not None:
            all_mega = set(all_mega_stones())
            out = [k for k in out if k not in all_mega or k in visible]
        return out


# 商店货架分档:徽章越多解锁越多。每档都对应真实存在的 BAG_ITEMS key,
# 用于保证"数据里有的道具都能被买到"(此前 60 个道具完全没有获取途径)。
SHOP_TIERS: list[tuple[int, list[str]]] = [
    (0, [
        "poke-ball", "potion", "antidote", "fresh-water", "moomoo-milk",
        "cheri-berry", "chesto-berry", "pecha-berry", "rawst-berry",
        "aspear-berry", "oran-berry", "leppa-berry", "berry-juice", "sweet-heart",
    ]),
    (1, ["great-ball", "super-potion", "paralyze-heal", "awakening", "soda-pop"]),
    (2, ["burn-heal", "ice-heal", "lemonade", "ether", "x-attack", "x-defense",
         "x-sp-defense", "guard-spec", "premier-ball",
         # 心之鳞片:宝可梦中心「招式教学狂」的报酬(`/回忆` 招式,每次 1 枚)
         "heart-scale"]),
    (3, ["ultra-ball", "hyper-potion", "revive", "max-ether", "x-speed", "x-special", "dire-hit",
         "net-ball", "nest-ball", "repeat-ball",
         # 属性增强类持有道具(×1.2)
         "silk-scarf", "charcoal", "mystic-water", "miracle-seed", "magnet",
         "never-melt-ice", "black-belt", "poison-barb", "soft-sand", "sharp-beak",
         "twisted-spoon", "silver-powder", "hard-stone", "spell-tag", "dragon-fang",
         "black-glasses", "fairy-feather",
         # 钥石:解锁 Mega 进化(Mega 石本身按「已捕获」动态上架,见 main.py)
         "key-stone"]),
    (4, ["full-heal", "max-potion", "max-revive", "full-restore", "elixir",
         # 化石:在「研究所」用 `/复活 <化石>` 换回古代宝可梦
         "helix-fossil", "dome-fossil", "old-amber", "root-fossil", "claw-fossil",
         "skull-fossil", "armor-fossil", "cover-fossil", "plume-fossil",
         "jaw-fossil", "sail-fossil",
         # 伽勒尔:要两件拼合(化石鸟/鱼/龙/兽)
         "fossilized-bird", "fossilized-fish", "fossilized-drake",
         "fossilized-dino",
         "dusk-ball", "quick-ball", "timer-ball", "level-ball", "heavy-ball",
         "beast-ball",
         # 中档持有道具
         "muscle-band", "wise-glasses", "expert-belt", "light-clay", "heat-rock",
         "damp-rock", "smooth-rock", "icy-rock", "terrain-extender",
         "safety-goggles", "air-balloon", "covert-cloak", "clear-amulet",
         "loaded-dice", "throat-spray", "toxic-orb", "flame-orb",
         "eviolite", "assault-vest",
         # 树果(同时是可携带道具:文柚果回 1/4、木子果解全状态)
         "sitrus-berry", "lum-berry"]),
    (5, ["max-elixir", "rare-candy", "metal-coat", "kings-rock", "dragon-scale",
         "deep-sea-scale", "deep-sea-tooth", "up-grade", "protector",
         "electirizer", "magmarizer", "reaper-cloth", "razor-claw", "razor-fang",
         "sachet", "whipped-dream", "prism-scale", "oval-stone", "dubious-disc"]),
    (6, ["sweet-apple", "tart-apple", "syrupy-apple", "cracked-pot",
         "unremarkable-teacup", "auspicious-armor", "malicious-armor", "metal-alloy",
         "fire-stone", "water-stone", "thunder-stone", "leaf-stone", "moon-stone",
         "sun-stone", "shiny-stone", "dusk-stone", "dawn-stone", "ice-stone"]),
    (6, ["leftovers", "choice-band", "choice-specs", "choice-scarf", "life-orb",
         "focus-sash", "rocky-helmet", "heavy-duty-boots", "weakness-policy",
         "booster-energy", "black-sludge",
         # PP 上限提升与特性切换(见 /使用)
         "pp-up", "pp-max", "ability-capsule", "ability-patch"]),
    # 大师球 / 究极球 / 特性胶囊 / 膏药 / PP 提升:不进普通商店(大赛奖励或事件获取)
]

# Mega 石按同一套档位解锁(膨胀的 92 块石头由 shop_stock 按玩家过滤:
# 拿到钥石 + 捕获对应原种才展示,没钥石时一块都不卖)
for _tier_row in SHOP_TIERS:
    if _tier_row[0] == MEGA_STONE_BADGES:
        _tier_row[1].extend(all_mega_stones())
        break


def _visible_mega_stones(trainer) -> set[str] | None:
    """Mega 石展示集合(没传 trainer = 不过滤,返回 None)。"""
    if trainer is None:
        return None
    if trainer.count(KEY_STONE) <= 0:
        return set()
    caught = set((trainer.data or {}).get("dex_caught") or [])
    for row in list(trainer.party or []) + list((trainer.data or {}).get("box") or []):
        sp = str((row or {}).get("species") or "")
        if sp:
            caught.add(sp)
    return set(mega_stones_for(caught))

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
    "berry-juice",
    "sweet-heart",
    "leppa-berry",
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


def tm_stock(badges: int = 0) -> list[str]:
    """按徽章数解锁的招式机货架(与道具货架同一套档位语义)。"""
    from .items import TM_SHOP, tm_key

    out: list[str] = []
    for tier, moves in TM_SHOP:
        if tier <= int(badges or 0):
            out.extend(tm_key(m) for m in moves)
    return out


def item_price(key: str, *, badge_count: int = 0, discount: float = 1.0) -> int:
    """道具价格:基础价按类别 + 徽章折扣 + 世界事件折扣。"""
    r = resolve_item(key)
    if not r:
        return 0
    k, entry = r
    from .items import BAG_ITEMS

    kind = str((BAG_ITEMS.get(k) or entry).get("kind") or entry.get("kind") or "")
    # 同类道具必须分档:旧实现按 kind 统一定价,导致"全复药(回满+解状态)"
    # 和"伤药(回 20)"都是 300₽,进化石/进化道具只按默认 500₽ 卖
    # (ITEMS 里的条目没有 kind,resolve_item 优先返回 ITEMS 条目)。
    tier = {
        # 心之鳞片:回忆一个招式一份报酬(不吃 kind="rare" 的 4000₽ 默认价)
        "heart-scale": 2500,
        "potion": 300, "super-potion": 600, "hyper-potion": 900,
        "max-potion": 1500, "full-restore": 2000, "moomoo-milk": 500,
        "revive": 1500, "max-revive": 3000, "revival-herb": 2800,
        "ether": 600, "max-ether": 1200, "elixir": 600, "max-elixir": 1200,
        "energy-powder": 500, "energy-root": 800, "heal-powder": 300,
    }
    # 持有道具按强度定价(以前 kind="held" 一律 500₽,剩饭和丝绸围巾一个价)
    if k in ("leftovers", "choice-band", "choice-specs", "choice-scarf", "life-orb",
             "focus-sash", "rocky-helmet", "heavy-duty-boots", "weakness-policy",
             "booster-energy"):
        base = 4000
    elif k in ("muscle-band", "wise-glasses", "expert-belt", "light-clay", "heat-rock",
               "damp-rock", "smooth-rock", "icy-rock", "terrain-extender",
               "safety-goggles", "air-balloon", "covert-cloak", "clear-amulet",
               "loaded-dice", "throat-spray", "toxic-orb", "flame-orb",
               "eviolite", "assault-vest"):
        base = 2500
    else:
        base = 1000 if kind == "held" else None
    if kind == "tm":
        # 招式机按招式强度定价:威力越高越贵(变化招式按固定价)
        from .items import tm_move

        mv = tm_move(k)
        power = int((get_dex().moves.get(mv) or {}).get("basePower") or 0)
        base = 1500 + power * 50
    base = base or tier.get(k) or {
        "ball": 200,
        "medicine": 600,
        "status": 300,
        "revive": 1500,
        "pp": 800,
        "battle": 500,
        "berry": 150,
        "rare": 4000,
        "stone": 3000,
        "evo": 5000,
        "mega": 6000,
        "key": 10000,
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
