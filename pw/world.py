"""地图:地区 / 节点 / 相邻关系 / 通行规则 / 道馆 / 服务设施。

数据来自 `pw/static/maps.json`(由 tools/build_map_graph.py 生成)与
`pw/static/gyms.json`(由 tools/build_gym_data.py 生成,真实游戏数据)。
若 maps.json 缺失,会在运行时用 locations.json 兜底生成一个最小连通图,
保证插件仍可用。
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache

from astrbot.api import logger

from .dex import _norm, get_dex
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


# 不是"地点"的伪条目(游走容器与占位符),即便地图数据里残留也不展示
PSEUDO_LOCATION_RE = re.compile(r"^(roaming-|unknown-)")


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
                lo = max(lo, core_lo)
                hi = min(hi, max(core_hi, core_lo))
                if hi < lo:
                    hi = lo
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

    def find_location(self, query: str, region: str = "") -> str:
        """地点名 → 地图节点 key(支持中文名/英文名/标识/模糊匹配)。"""
        q = str(query or "").strip()
        if not q:
            return ""
        if q in self._index and (not region or self.region_of(q) == region):
            return q
        key = get_dex().find_location(q, region)
        if key and key in self._index:
            return key
        # 兜底:按节点中文名/标识做包含匹配(限定在指定地区内)
        nq = _norm(q)
        if not nq:
            return ""
        pool = (
            [k for k in self._index if self.region_of(k) == region]
            if region
            else list(self._index)
        )
        for k in sorted(pool, key=len):
            if nq == _norm(k) or nq == _norm(self.node_zh(k)):
                return k
        for k in sorted(pool, key=len):
            if nq in _norm(k) or nq in _norm(self.node_zh(k)):
                return k
        return ""

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
