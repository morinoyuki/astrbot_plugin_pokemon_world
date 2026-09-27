"""内置宝可梦数据访问层(种族值 / 技能 / 招式学习 / 属性相克 / 特性 / 性格)。

数据由 `tools/build_pokemon_data.py` 生成,放在 `pokesim/static/*.json`,
运行时只读本地文件、不联网、不依赖第三方库。

对外主要入口:
    dex = get_dex()
    dex.resolve_species("皮卡丘")      -> ("pikachu", {...})
    dex.resolve_move("十万伏特")        -> ("thunderbolt", {...})
    dex.resolve_ability("静电")         -> ("static", {...})
    dex.type_multiplier("Electric", ["Water", "Flying"])  -> 4.0
    dex.compute_stats("pikachu", 50)    -> {"hp":..., "atk":..., ...}
    dex.learnable("pikachu", 30)        -> [{"move":..., "methods":..., "level":...}, ...]
"""

from __future__ import annotations

import difflib
import json
import os
import re
import unicodedata
from functools import cache, lru_cache
from typing import ClassVar

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

STAT_ORDER = ["hp", "atk", "def", "spa", "spd", "spe"]

# 归一化时要剔除的分隔符(便于「皮卡丘」「Pikachu」「Mr. Mime」「mr-mime」互查)
_SEP = set(" -_.·'’:\u3000()（）[]【】")


def _norm(text: str) -> str:
    if text is None:
        return ""
    # 全角→半角(数字/拉丁字母),让 "２０１号道路" 也能命中 "201号道路"
    s = unicodedata.normalize("NFKC", str(text).strip().lower())
    return "".join(ch for ch in s if ch not in _SEP)


# 遭遇方法分组(用于按环境筛选地点分布)
LAND_METHODS = {
    "walk",
    "grass-spots",
    "dark-grass",
    "cave-spots",
    "bridge-spots",
    "rough-terrain",
    "yellow-flowers",
    "purple-flowers",
    "red-flowers",
    "honey-tree",
    "berry-trees",
    "hidden-grotto",
    "rustling-bush-ambush",
    "trash-can-ambush",
    "ceiling-ambush",
    "ground-ambush",
    "horde",
    "sos",
    "sos-from-bubbling-spot",
    "overworld",
    "overworld-dirt",
}
WATER_METHODS = {
    "surf",
    "surf-spots",
    "seaweed",
    "bubbling-spots",
    "overworld-water",
}
FISH_METHODS = {
    "old-rod",
    "good-rod",
    "super-rod",
    "super-rod-spots",
    "feebas-tile-fishing",
}
ROCK_METHODS = {"rock-smash", "headbutt", "headbutt-low", "headbutt-normal", "headbutt-high"}
# 定点/赠予/群战外的特殊形式,默认不作为野生随机遭遇
SPECIAL_METHODS = {
    "static",
    "gift",
    "gift-egg",
    "npc-trade",
    "snag",
    "snag-rematch",
    "dynamax-adventure",
    "max-raid",
    "island-scan",
    "pokeflute",
    "devon-scope",
    "squirt-bottle",
    "wailmer-pail",
    "pokespot",
    "roaming-grass",
    "roaming-water",
    "wanderer",
    "wanderer-water",
    "chase-water",
    "overworld-flying",
    "overworld-special",
    "overworld-flying-special",
    "overworld-water-special",
    "pokemon-ranger",
}
LAND_GROUP = LAND_METHODS
WATER_GROUP = WATER_METHODS | FISH_METHODS


@cache
def _load(name: str) -> dict:
    path = os.path.join(DATA_DIR, f"{name}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Dex:
    """宝可梦图鉴 / 招式 / 特性数据查询器(单例,线程内只读)。"""

    def __init__(self) -> None:
        self.species: dict[str, dict] = _load("species")
        self.moves: dict[str, dict] = _load("moves")
        # 搏命(PokeAPI/Showdown 已有条目):无属性克制、1/4 最大 HP 反作用
        self.moves.setdefault(
            "struggle",
            {
                "name": "Struggle",
                "zh": "挣扎",
                "category": "Physical",
                "basePower": 50,
                "accuracy": True,
                "pp": 1,
                "priority": 0,
            },
        )
        self.moves["struggle"].update(
            {"type": "???", "recoilMaxHp": [1, 4], "struggle": True}
        )
        self.learnsets: dict[str, dict] = _load("learnsets")
        self.learnset_gen: dict[str, int] = _load("learnset_gen")
        self.typechart: dict[str, dict[str, float]] = _load("typechart")
        self.natures: dict[str, dict] = _load("natures")
        self.abilities: dict[str, dict] = _load("abilities")
        self.meta: dict = _load("meta")
        self.type_zh: dict[str, str] = self.meta.get("type_zh", {})
        self.stat_zh: dict[str, str] = self.meta.get("stat_zh", {})
        self.zh_to_type: dict[str, str] = {
            _norm(v): k for k, v in self.type_zh.items()
        }
        # 索引:_norm(别名) -> 正式 key
        self._species_idx: dict[str, str] = {}
        for key, s in self.species.items():
            for alias in (key, s.get("name"), s.get("zh")):
                if alias:
                    self._species_idx.setdefault(_norm(alias), key)
        self._move_idx: dict[str, str] = {}
        for key, m in self.moves.items():
            for alias in (key, m.get("name"), m.get("zh")):
                if alias:
                    self._move_idx.setdefault(_norm(alias), key)
        self._ability_idx: dict[str, str] = {}
        for key, a in self.abilities.items():
            for alias in (key, a.get("name"), a.get("zh")):
                if alias:
                    self._ability_idx.setdefault(_norm(alias), key)

        try:
            locs = _load("locations")
        except (OSError, ValueError):
            locs = {}
        self.locations: dict[str, dict] = locs.get("locations", {})
        self.location_regions: dict[str, dict] = locs.get("regions", {})
        self.version_groups: dict[str, dict] = locs.get("versionGroups", {})
        self.location_methods: dict[str, str] = locs.get("methods", {})
        self._location_idx: dict[str, str] = {}
        for key, v in self.locations.items():
            aliases = [key, v.get("name"), v.get("zh"), *(v.get("aliases") or [])]
            for alias in aliases:
                if alias:
                    self._location_idx.setdefault(_norm(alias), key)

    # ─────────────── 通用解析 ───────────────

    @staticmethod
    def _resolve(query: str, table: dict, index: dict) -> tuple[str, dict] | None:
        if query is None:
            return None
        raw = str(query).strip()
        if not raw:
            return None
        if raw in table:
            return raw, table[raw]
        key = index.get(_norm(raw))
        if key:
            return key, table[key]
        # 模糊匹配(容错) — 只对长度足够的查询启用
        norm = _norm(raw)
        if len(norm) >= 2:
            close = difflib.get_close_matches(norm, index.keys(), n=1, cutoff=0.82)
            if close:
                key = index[close[0]]
                return key, table[key]
        return None

    def resolve_species(self, query: str) -> tuple[str, dict] | None:
        return self._resolve(query, self.species, self._species_idx)

    def resolve_move(self, query: str) -> tuple[str, dict] | None:
        return self._resolve(query, self.moves, self._move_idx)

    def resolve_ability(self, query: str) -> tuple[str, dict] | None:
        return self._resolve(query, self.abilities, self._ability_idx)

    def resolve_type(self, query: str) -> str | None:
        """把中英文属性名解析成规范英文属性名(Normal/Fire/…)。"""
        if not query:
            return None
        raw = str(query).strip()
        if raw in self.type_zh:
            return raw
        for t in self.type_zh:
            if t.lower() == raw.lower():
                return t
        return self.zh_to_type.get(_norm(raw))

    def resolve_nature(self, query: str) -> str | None:
        if not query:
            return None
        key = _norm(query)
        for k, v in self.natures.items():
            if key in (k, _norm(v.get("zh", "")), _norm(v.get("name", ""))):
                return k
        return None

    def type_label(self, t: str) -> str:
        """英文属性名 → 显示名(优先中文)。"""
        return self.type_zh.get(t, t)

    def stat_label(self, s: str) -> str:
        return self.stat_zh.get(s, s)

    # ─────────────── 属性相克 ───────────────

    def type_multiplier(self, attack_type: str, defender_types) -> float:
        """计算属性相克倍率(可叠加双属性)。返回 1.0 / 2.0 / 4.0 / 0.5 / 0.25 / 0。"""
        atk = self.resolve_type(attack_type) or attack_type
        if not atk:
            return 1.0
        if atk == "Stellar":
            # 星晶属性进攻对所有属性中性(引擎对特定目标另行处理)
            return 1.0
        chart = self.typechart.get(atk) or {}
        mult = 1.0
        for d in defender_types or []:
            dt = self.resolve_type(d) or d
            mult *= chart.get(dt, 1.0)
        return mult

    def effectiveness_label(self, mult: float) -> str:
        if mult == 0:
            return "无效(0×)"
        if mult >= 4:
            return "效果绝佳(4×)"
        if mult > 1:
            return "效果拔群(2×)"
        if mult <= 0.25:
            return "效果甚微(1/4×)"
        if mult < 1:
            return "效果不理想(1/2×)"
        return "效果一般(1×)"

    # ─────────────── 数值计算 ───────────────

    # ── 招式展示 ──
    MOVE_CATEGORY_ZH: ClassVar[dict[str, str]] = {
        "Physical": "物理",
        "Special": "特殊",
        "Status": "变化",
    }
    STATUS_FIELD_ZH: ClassVar[dict[str, str]] = {
        "brn": "灼伤", "par": "麻痹", "slp": "睡眠", "frz": "冰冻", "psn": "中毒",
        "tox": "剧毒", "confusion": "混乱", "flinch": "畏缩", "trap": "束缚",
        "leechseed": "寄生种子", "partiallytrapped": "束缚", "saltcure": "盐腌",
        "protect": "守住", "attract": "着迷", "substitute": "替身", "curse": "咒语",
        "aquaring": "水流环", "foresight": "识破", "charge": "充电",
        "defensecurl": "变圆", "bide": "忍耐", "smackdown": "击落",
        "banefulbunker": "碉堡", "burningbulwark": "火焰守护", "destinybond": "同命",
        "focusenergy": "集气", "ingrain": "扎根", "magiccoat": "魔法反射",
        "magnetrise": "电磁飘浮", "minimize": "变小", "mudsport": "玩泥巴",
        "powertrick": "力量戏法", "roost": "羽栖", "smackdown2": "击落",
        "stockpile": "蓄力", "tarshot": "沥青射击", "telekinesis": "意念移物",
        "watersport": "玩水", "yawn": "哈欠",
    }
    BOOST_FIELD_ZH: ClassVar[dict[str, str]] = {
        "atk": "攻击", "def": "防御", "spa": "特攻", "spd": "特防", "spe": "速度",
        "accuracy": "命中", "evasion": "闪避",
    }
    WEATHER_ZH: ClassVar[dict[str, str]] = {
        "sunnyday": "大晴天", "raindance": "下雨", "sandstorm": "沙暴",
        "snowscape": "下雪", "hail": "冰雹", "desolateland": "大日照",
        "primordialsea": "大雨", "deltastream": "乱流",
    }
    TERRAIN_ZH: ClassVar[dict[str, str]] = {
        "electricterrain": "电气场地", "grassyterrain": "青草场地",
        "mistyterrain": "薄雾场地", "psychicterrain": "精神场地",
    }
    SIDE_CONDITION_ZH: ClassVar[dict[str, str]] = {
        "reflect": "反射壁", "lightscreen": "光墙", "auroraveil": "极光幕",
        "mist": "白雾", "safeguard": "神秘守护", "tailwind": "顺风",
        "spikes": "撒菱", "stealthrock": "隐形岩", "toxicspikes": "毒菱",
        "stickyweb": "黏黏网", "luckychant": "幸运咒语", "quickguard": "快速防守",
        "wideguard": "广域防守", "matblock": "掀榻榻米", "craftyshield": "戏法防守",
    }

    def move_category_zh(self, category: str) -> str:
        """物理 / 特殊 / 变化。"""
        return self.MOVE_CATEGORY_ZH.get(str(category or ""), str(category or "?"))

    def _boost_text(self, boosts) -> str:
        if not isinstance(boosts, dict):
            return ""
        parts = []
        for key, val in boosts.items():
            label = self.BOOST_FIELD_ZH.get(str(key), str(key))
            try:
                n = int(val)
            except (TypeError, ValueError):
                continue
            if n:
                parts.append(f"{label} {'+' if n > 0 else ''}{n}")
        return "、".join(parts)

    def _ratio_text(self, raw) -> str:
        """`drain`/`recoil` 在数据里是 [1,2] / [33,100] 这种分数。"""
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            try:
                num, den = float(raw[0]), float(raw[1])
                if den:
                    return f"{abs(num) / den * 100:.0f}%"
            except (TypeError, ValueError):
                return ""
        try:
            return f"{abs(float(raw)) * 100:.0f}%"
        except (TypeError, ValueError):
            return ""

    def move_effect_text(self, key: str) -> str:
        """从**结构化字段**推出一句中文效果(招式的 `desc` 只是图鉴风味文字)。

        对战中真正生效的是 `boosts`/`secondary`/`drain`/`recoil`/`multihit`/
        `priority`/`critRatio`/`weather`/`terrain`/`heal`/`selfdestruct` 等字段,
        这里把它们翻成人话 —— 光看 `desc` 玩家常常不知道招式实际做什么。
        推不出内容时返回空串,由调用方退回显示 `desc`。
        """
        e = self.moves.get(str(key or ""))
        if not e:
            return ""
        bits: list[str] = []
        pri = int(e.get("priority") or 0)
        if pri > 0:
            bits.append(f"优先度 +{pri}")
        elif pri < 0:
            bits.append(f"优先度 {pri}")
        if e.get("critRatio"):
            bits.append("容易会心")
        mh = e.get("multihit")
        if isinstance(mh, list) and len(mh) == 2:
            bits.append(f"连续攻击 {mh[0]}~{mh[1]} 次")
        elif isinstance(mh, int) and mh > 1:
            bits.append(f"连续攻击 {mh} 次")
        drain = self._ratio_text(e.get("drain"))
        if drain:
            bits.append(f"回复造成伤害的 {drain}")
        recoil = self._ratio_text(e.get("recoil"))
        if recoil:
            bits.append(f"自身承受 {recoil} 反作用伤害")
        heal = self._ratio_text(e.get("heal"))
        if heal:
            bits.append(f"回复自身最大 HP 的 {heal}")
        if e.get("selfdestruct"):
            bits.append("使用后自身倒下")
        if e.get("stallingMove"):
            bits.append("变成防守姿态,本回合优先")
        if e.get("ignoreAbility"):
            bits.append("无视对手特性")

        def _status_bits(d, prefix="") -> list[str]:
            out: list[str] = []
            st = d.get("status") if isinstance(d, dict) else None
            vst = d.get("volatileStatus") if isinstance(d, dict) else None
            boosts = d.get("boosts") if isinstance(d, dict) else None
            who = "自身" if (isinstance(d, dict) and d.get("self")) else "对手"
            if st:
                out.append(prefix + f"使{who}" + self.STATUS_FIELD_ZH.get(str(st), str(st)))
            if vst and not st:
                out.append(prefix + f"使{who}" + self.STATUS_FIELD_ZH.get(str(vst), str(vst)))
            if boosts:
                who = "自身" if (isinstance(d, dict) and d.get("self")) else "对手"
                bt = self._boost_text(boosts)
                if bt:
                    out.append(prefix + f"使{who} {bt}")
            return out

        sec = e.get("secondary")
        if isinstance(sec, dict):
            chance = int(sec.get("chance") or 0)
            bits += _status_bits(sec, f"{chance}% " if chance else "")
        elif isinstance(sec, list):
            for item in sec:
                if isinstance(item, dict):
                    bits += _status_bits(item)
        if not isinstance(sec, dict) or not bits:
            own = {
                "status": e.get("status"),
                "volatileStatus": e.get("volatileStatus"),
                "boosts": e.get("boosts"),
                "self": e.get("target") in ("self", "allySide", "allyTeam"),
            }
            bits += [x for x in _status_bits(own) if x not in bits]
        w = e.get("weather")
        if w:
            bits.append("天气变为" + self.WEATHER_ZH.get(str(w), str(w)))
        ter = e.get("terrain")
        if ter:
            bits.append("布下" + self.TERRAIN_ZH.get(str(ter), str(ter)))
        sc = e.get("sideCondition")
        if sc:
            bits.append("在我方场地布下「" + self.SIDE_CONDITION_ZH.get(str(sc), str(sc)) + "」")
        if e.get("selfSwitch"):
            bits.append("使用后自身退场")
        base = int(e.get("basePower") or 0)
        if base and not bits:
            bits.append("造成伤害")
        # 去重但保持顺序
        seen: set[str] = set()
        uniq = [b for b in bits if not (b in seen or seen.add(b))]
        return "、".join(uniq)

    def move_short_desc(self, key: str, *, limit: int = 40) -> str:
        """一句话效果:优先用结构化推导;推不出(或只推出"造成伤害")时用 desc。

        数据结构里少数招式丢了 `secondary`(如火焰牙的"畏缩/灼伤"、三色牙),
        这时 `desc` 往往还写着效果 —— 所以挑 desc 里**带效果关键词**的那一句,
        比只显示"造成伤害"有用得多。
        """
        eff = self.move_effect_text(key)
        if eff and eff != "造成伤害":
            return eff
        raw = str((self.moves.get(str(key or "")) or {}).get("desc") or "")
        if "无法使用这个招式" in raw:
            # 147 个招式在数据里是这条占位说明(本代不可用),别当效果显示
            return eff or "造成伤害"
        desc = raw.replace("\n", " ").strip()
        clauses = [c for c in re.split(r"[。;]", desc) if c.strip()]
        if not clauses:
            return eff or "造成伤害"
        keys = ("有时", "陷入", "下降", "提高", "回复", "必定", "连续", "反作",
                "优先", "无视", "交换", "能力")
        for c in clauses[1:] or clauses:        # 先看第 2 句起的效果描述
            if any(k in c for k in keys):
                return c.strip()[:limit]
        return (clauses[0] if eff else (clauses[0] if clauses else ""))[:limit] or eff

    def compute_stats(
        self,
        species_key: str,
        level: int,
        ivs: dict | None = None,
        evs: dict | None = None,
        nature: str = "hardy",
    ) -> dict[str, int]:
        """按第 3 世代以后的公式算实际数值(不含战斗中变化)。"""
        entry = self.species.get(species_key)
        if entry is None:
            return dict.fromkeys(STAT_ORDER, 1)
        base = entry.get("baseStats", {})
        level = max(1, min(100, int(level)))
        nat = self.natures.get(self.resolve_nature(nature) or "hardy", {})
        inc, dec = nat.get("inc", ""), nat.get("dec", "")
        out: dict[str, int] = {}
        for stat in STAT_ORDER:
            b = int(base.get(stat, 1))
            iv = int((ivs or {}).get(stat, 31))
            ev = int((evs or {}).get(stat, 0))
            core = (2 * b + iv + ev // 4) * level // 100
            if stat == "hp":
                val = core + level + 10
            else:
                val = core + 5
                if inc == stat:
                    val = int(val * 1.1)
                elif dec == stat:
                    val = int(val * 0.9)
            out[stat] = max(1, val)
        return out

    # ─────────────── 招式学习 ───────────────

    def _lineage(self, species_key: str) -> list[str]:
        """返回该宝可梦的进化前系(自身 + prevo + baseSpecies),用于合并招式表。"""
        chain: list[str] = []
        seen: set[str] = set()
        queue = [species_key]
        while queue:
            key = queue.pop(0)
            if not key or key in seen:
                continue
            seen.add(key)
            chain.append(key)
            entry = self.species.get(key) or {}
            nxts = []
            if entry.get("prevo"):
                nxts.append(entry["prevo"])
            if entry.get("baseSpecies"):
                base = _norm(entry["baseSpecies"])
                if base != key:
                    nxts.append(base)
            queue.extend(n for n in nxts if n not in seen)
        return chain

    def learnset(self, species_key: str) -> dict[str, str]:
        """合并进化链上所有形态的第 9 世代(或该族最新世代)招式表。

        返回 {move_key: "L1,L5,M,T"}。等级招取最低等级,其余方法求并集。
        """
        merged: dict[str, set[str]] = {}
        for key in self._lineage(species_key):
            row = self.learnsets.get(key)
            if not row:
                continue
            for move, codes in row.items():
                merged.setdefault(move, set()).update(codes.split(","))
        out: dict[str, str] = {}
        for move, codes in merged.items():
            out[move] = ",".join(_sort_methods(codes))
        return out

    def learnable(
        self,
        species_key: str,
        level: int = 100,
        include_tm: bool = True,
        include_tutor: bool = True,
        include_egg: bool = False,
        include_event: bool = False,
    ) -> list[dict]:
        """列出该宝可梦可学招式。

        返回 [{move, key?, methods: ["L12","M"], level: 12|None}],按等级/名称排序。
        """
        row = self.learnset(species_key)
        out: list[dict] = []
        for move, codes in row.items():
            methods: list[str] = []
            min_level: int | None = None
            for c in codes.split(","):
                if not c:
                    continue
                if c.startswith("L"):
                    lv = int(c[1:] or 0)
                    # 进化招(L0)与已到等级
                    if lv <= level or lv == 0:
                        methods.append(c)
                    if min_level is None or lv < min_level:
                        min_level = lv
                elif (c == "M" and include_tm) or (c == "T" and include_tutor) or (c == "E" and include_egg) or (c in ("S", "R", "V") and include_event):
                    methods.append(c)
            if methods:
                out.append({"move": move, "methods": methods, "level": min_level})
        out.sort(key=lambda x: (x["level"] is None, x["level"] or 0, x["move"]))
        return out

    def _own_learnset(self, species_key: str) -> dict[str, str]:
        """仅该形态自己的招式表(不含进化前),用于等级提升与默认配招。"""
        row = self.learnsets.get(species_key)
        if row:
            return row
        base = (self.species.get(species_key) or {}).get("baseSpecies")
        if base:
            r = self.resolve_species(base)
            if r:
                return self.learnsets.get(r[0], {}) or {}
        return {}

    def tm_moves(self, species: str) -> set[str]:
        """该物种**能用招式机学会**的招式集合。

        判据是学习表里的 **M 码**(Pokémon Showdown 的学习表把"某世代可用招式机
        学会"标成 M),所以与 `/图鉴` 展示的学习途径天然一致。
        排除非标准招式(当前世代已无法使用的那 147 个)。
        """
        codes = self._own_learnset(str(species or "")) or {}
        out: set[str] = set()
        for mv, cd in codes.items():
            if "M" not in str(cd):
                continue
            entry = self.moves.get(str(mv)) or {}
            if entry.get("isNonstandard"):
                continue
            out.add(str(mv))
        return out

    def tm_compatible(self, species: str, move: str) -> bool:
        """能否用这台招式机。"""
        return str(move or "") in self.tm_moves(species)

    def level_up_moves(self, species_key: str, old_level: int, new_level: int) -> list[str]:
        """返回在 (old_level, new_level] 区间内新学会的等级招(按等级升序)。"""
        row = self._own_learnset(species_key)
        got: list[tuple[int, str]] = []
        for move, codes in row.items():
            for c in codes.split(","):
                if c.startswith("L"):
                    lv = int(c[1:] or 0)
                    if old_level < lv <= new_level:
                        got.append((lv, move))
                        break
        got.sort()
        return [m for _, m in got]

    def level_moves(self, species_key: str, level: int) -> list[str]:
        """当前等级应已掌握的全部等级招(按习得等级升序,仅该形态自身)。"""
        row = self._own_learnset(species_key)
        got: list[tuple[int, str]] = []
        for move, codes in row.items():
            for c in codes.split(","):
                if c.startswith("L"):
                    lv = int(c[1:] or 0)
                    if lv <= level:
                        got.append((lv, move))
                        break
        got.sort()
        return [m for _, m in got]

    def default_moveset(
        self, species_key: str, level: int, n: int = 4, tm_fill: bool = True
    ) -> list[str]:
        """生成一套默认招式:**优先当前等级已学会的等级招**。

        修正两个会出荒唐配置的问题:
        1. 以前不管三七二十一用 TM/蛋招补满 4 个 —— 野生宝可梦本来就可能只有
           1~3 个招式,硬补会补出"4 级小拉达会 34 级的蛮干、学的是迷人/诱惑
           这类 0 威力招式",双方都打不动 → 战斗永远结束不了(实测出现过);
        2. 数据里少数物种(小拉达/波波/独角虫…)的低级等级招只标了 VC(V),
           `level_moves` 取不到 → 于是整只宝可梦一个等级招都没有。这时才兜底:
          先用该形态**最低等级**的等级招,再用**有威力**的可学招式,最后才用
           任意可学招式,保证至少能打伤害。
        """
        picks: list[str] = []
        for m in reversed(self.level_moves(species_key, level)):
            if m not in picks:
                picks.append(m)
            if len(picks) >= n:
                break
        if picks:
            return picks[:n]          # 有几招算几招
        if not tm_fill:
            return picks
        # 后面都是"数据缺低级招"时的猜测,招式数上限压到 2 ——
        # 免得 4 级小拉达顶着居合斩/充电光束一整套 TM
        n = min(n, 2)
        row = self._own_learnset(species_key)
        by_level: list[tuple[int, str]] = []
        for move, codes in row.items():
            for c in str(codes).split(","):
                if c.startswith("L"):
                    by_level.append((int(c[1:] or 0), move))
                    break
        # 数据里这些物种的早期招式只标了 VC(V),L 招从十几级才开始 ——
        # 不能把 53 级的暴风塞给 5 级波波,所以按"等级够得着 + 优先有威力"挑,
        # 够得着的都没有时只兜一个最低等级的能打伤害的招(好过 4 个变化招)
        cap = n
        reach = [m for lv, m in sorted(by_level) if lv <= max(15, level * 3)]
        for want_power in (True, False):
            for move in reach:
                power = int((self.moves.get(move) or {}).get("basePower") or 0)
                if want_power and power <= 0:
                    continue
                if move not in picks:
                    picks.append(move)
                if len(picks) >= cap:
                    return picks[:cap]
        if not picks:
            for _lv, move in sorted(by_level):
                if int((self.moves.get(move) or {}).get("basePower") or 0) > 0:
                    picks.append(move)
                    break
            if not picks and by_level:
                picks.append(sorted(by_level)[0][1])
        cands = [it["move"] for it in self.learnable(species_key, level)]
        for want_power in (True, False):   # 先补能打伤害的
            for move in cands:
                if move in picks:
                    continue
                power = int((self.moves.get(move) or {}).get("basePower") or 0)
                if want_power and power <= 0:
                    continue
                picks.append(move)
                if len(picks) >= n:
                    return picks[:n]
        return picks[:n]

    def evolution(self, species_key: str, level: int) -> str | None:
        """若达到条件则返回进化后的形态 key(仅处理等级进化,等级记录在进化后形态上)。"""
        entry = self.species.get(species_key) or {}
        for e in entry.get("evos") or []:
            cand = self.species.get(e) or {}
            if cand.get("battleOnly"):
                continue
            if cand.get("prevo") and cand["prevo"] != species_key:
                continue
            lv = cand.get("evoLevel")
            if lv and level >= int(lv):
                return e
        return None

    # ─────────────── 进化判定 ───────────────

    FRIENDSHIP_EVO = 160

    @staticmethod
    def _norm_item(s: str | None) -> str:
        return "".join(ch for ch in str(s or "").lower() if ch.isalnum())

    def ability_options(self, species_key: str) -> dict[str, str]:
        """该物种的特性槽位:`{"0":..., "1":..., "H":...}`(没有的槽位不出现)。"""
        entry = self.species.get(str(species_key)) or {}
        ab = entry.get("abilities")
        return {str(k): str(v) for k, v in ab.items()} if isinstance(ab, dict) else {}

    def item_matches(self, user_item: str | None, evo_item: str | None) -> bool:
        if not user_item or not evo_item:
            return False
        a = self._norm_item(user_item)
        b = self._norm_item(evo_item)
        return bool(a) and (a == b or a in b or b in a)

    @staticmethod
    def _friendship_need(reason: str) -> int:
        m = re.search(r"(\d+)\s*(?:friendship|affection)", reason, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return 160

    @staticmethod
    def _daytime_ok(reason: str, daytime: str | None) -> bool:
        low = reason.lower()
        needs_night = "night" in low or "midnight" in low or "full moon" in low
        needs_day = "during the day" in low or "daytime" in low
        if not needs_night and not needs_day:
            return True
        if not daytime:
            return False  # 需要昼夜信息才能判定
        if needs_night:
            return daytime == "night"
        return daytime == "day"

    @staticmethod
    def _stat_ok(reason: str, stats: dict | None) -> bool:
        if not stats:
            return True
        low = reason.lower()
        if "atk stat" in low and "def stat" in low:
            atk = int(stats.get("atk", 0))
            dfn = int(stats.get("def", 0))
            if "equal" in low:
                return atk == dfn
            if ">" in low:
                return atk > dfn
            if "<" in low:
                return atk < dfn
        return True

    def _move_type(self, move_key: str) -> str:
        return str((self.moves.get(move_key) or {}).get("type") or "")

    def evolution_options(
        self,
        species_key: str,
        *,
        level: int = 1,
        moves=( ),
        item: str | None = None,
        friendship: int = 70,
        gender: str = "",
        trade: bool = False,
        daytime: str | None = None,
        stats: dict | None = None,
        party=(),
    ) -> list[dict]:
        """列出当前状态下所有进化选项,每项 {target, kind, reason, met, ...}。

        party 用于判定"队伍里有某只宝可梦"这类条件(小球飞鱼需要铁炮鱼)。
        """
        entry = self.species.get(species_key) or {}
        moveset = set(moves or ())
        out: list[dict] = []
        for e in entry.get("evos") or []:
            ce = self.species.get(e) or {}
            if ce.get("battleOnly") or ce.get("isCosmeticForme"):
                continue
            kind = ce.get("evoType", "level") or "level"
            reason = ce.get("evoCondition") or ""
            req_level = int(ce.get("evoLevel") or 0)
            req_item = ce.get("evoItem")
            req_move = ce.get("evoMove")
            met = False
            if kind in ("level", ""):
                met = level >= (req_level or 1)
            elif kind == "levelFriendship":
                met = level >= (req_level or 1) and friendship >= self._friendship_need(reason)
            elif kind == "levelMove":
                rm = self.resolve_move(req_move) if req_move else None
                met = bool(rm) and rm[0] in moveset and level >= (req_level or 1)
            elif kind == "levelHold":
                met = self.item_matches(item, req_item) and level >= (req_level or 1)
                met = met and self._daytime_ok(reason, daytime)
            elif kind == "useItem":
                met = self.item_matches(item, req_item)
            elif kind == "trade":
                met = bool(trade) and (not req_item or self.item_matches(item, req_item))
            elif kind == "levelExtra":
                met = self._extra_met(ce, moveset, friendship, party)
            else:  # other:特殊条件,需手动 force
                met = False
            fixed = (ce.get("gender") or "").upper()[:1]
            if fixed in ("M", "F") and gender and gender != fixed:
                met = False
            # 昼夜限定:对所有进化方式都适用(无条件时为 no-op)
            met = met and self._daytime_ok(reason, daytime)
            met = met and self._stat_ok(reason, stats)
            low = reason.lower()
            if "female" in low and gender and gender != "F":
                met = False
            if re.search(r"\bmale\b", low) and gender and gender != "M":
                met = False
            out.append(
                {
                    "target": e,
                    "kind": kind,
                    "reason": reason,
                    "met": met,
                    "level": req_level,
                    "item": req_item,
                    "move": req_move,
                }
            )
        return out

    def _extra_met(self, ce: dict, moveset: set, friendship: int, party=()) -> bool:
        reason = (ce.get("evoCondition") or "").lower()
        if "fairy" in reason:
            return friendship >= 100 and any(
                self._move_type(m) == "Fairy" for m in moveset
            )
        if "party" in reason:
            # "with a Remoraid in party":队伍里真的要有那只宝可梦。
            # 旧写法无条件返回 True → 小球飞鱼不需要铁炮鱼也能进化。
            have = {str(x) for x in party}
            if not have:
                return False
            for word in re.findall(r"[a-z][a-z\-]{3,}", reason):
                hit = self.resolve_species(word)
                if hit and str(hit[0]) in have:
                    return True
            return False
        # 磁场等特殊地点条件:当前引擎不模拟地点磁场,按可达成处理(已在文档声明)
        return True

    def level_evolutions(
        self,
        species_key: str,
        *,
        level: int,
        moves=(),
        item: str | None = None,
        friendship: int = 70,
        gender: str = "",
        daytime: str | None = None,
        stats: dict | None = None,
    ) -> list[dict]:
        """升级时可自动触发的进化(等级/亲密度/招式/携带物/特殊)。"""
        kinds = {"level", "levelFriendship", "levelMove", "levelHold", "levelExtra"}
        opts = self.evolution_options(
            species_key,
            level=level,
            moves=moves,
            item=item,
            friendship=friendship,
            gender=gender,
            daytime=daytime,
            stats=stats,
        )
        return [o for o in opts if o["met"] and o["kind"] in kinds]

    def use_item_evolutions(
        self, species_key: str, item: str | None, gender: str = ""
    ) -> list[dict]:
        """使用道具可触发的进化(带性别限制)。"""
        return [
            o
            for o in self.evolution_options(species_key, item=item, gender=gender)
            if o["met"] and o["kind"] == "useItem"
        ]

    # ─────────────── 经验 / 成长曲线 ───────────────

    def growth_of(self, species_key: str) -> str:
        e = self.species.get(species_key) or {}
        if e.get("growthRate"):
            return e["growthRate"]
        base = e.get("baseSpecies")
        if base:
            r = self.resolve_species(base)
            if r and r[1].get("growthRate"):
                return r[1]["growthRate"]
        return "medium"

    def base_exp(self, species_key: str) -> int:
        e = self.species.get(species_key) or {}
        if e.get("baseExp"):
            return int(e["baseExp"])
        base = e.get("baseSpecies")
        if base:
            r = self.resolve_species(base)
            if r and r[1].get("baseExp"):
                return int(r[1]["baseExp"])
        return 60

    def exp_for_level(self, growth: str, level: int) -> int:
        """升到 `level` 级所需的累计经验(第 3 世代后曲线)。"""
        n = max(0, min(100, int(level)))
        if n <= 0:
            return 0
        if growth == "fast":
            val = 4 * n**3 / 5
        elif growth == "medium-slow":
            val = 6 * n**3 / 5 - 15 * n**2 + 100 * n - 140
        elif growth == "slow":
            val = 5 * n**3 / 4
        elif growth == "slow-then-very-fast":
            r = n % 3
            if n <= 50:
                val = n**3 * (100 - n) / 50
            elif n <= 68:
                val = n**3 * (150 - n) / 100
            elif n <= 98:
                val = n**3 * (1274 + r * r - 9 * r - 20 * (n // 3)) / 1000
            else:
                val = n**3 * (160 - n) / 100
        elif growth == "fast-then-very-slow":
            if n <= 15:
                val = n**3 * (24 + (n + 1) // 3) / 50
            elif n <= 35:
                val = n**3 * (14 + n) / 50
            else:
                val = n**3 * (32 + n // 2) / 50
        else:  # medium-fast
            val = n**3
        return max(0, int(val))

    def level_from_exp(self, growth: str, exp: int) -> int:
        """根据累计经验反推等级(1-100)。"""
        exp = max(0, int(exp))
        level = 1
        while level < 100 and self.exp_for_level(growth, level + 1) <= exp:
            level += 1
        return level

    def exp_yield(self, species_key: str, level: int, trainer: bool = False) -> int:
        """击倒一只宝可梦获得的经验值(第 5 世代后简化公式)。"""
        base = self.base_exp(species_key)
        lv = max(1, int(level))
        exp = base * lv // 7
        if trainer:
            exp = exp * 3 // 2
        return max(1, exp)

    # ─────────────── 地点 / 野外分布 ───────────────

    def _loc_in_region(self, key: str, region: str) -> bool:
        return not region or (self.locations.get(key) or {}).get("region") == region

    def resolve_region(self, query: str) -> str:
        """地区名/中文名/标识 → 地区 key(如 关都 → kanto)。"""
        if not query:
            return ""
        raw = _norm(query)
        if raw in self.location_regions:
            return raw
        for k, v in self.location_regions.items():
            if raw in (_norm(k), _norm(v.get("name", "")), _norm(v.get("zh", ""))):
                return k
        for k, v in self.location_regions.items():
            zh = _norm(v.get("zh", ""))
            if raw and zh and (raw in zh or zh in raw):
                return k
        return ""

    def resolve_location(self, query: str, region: str = "") -> tuple[str, dict] | None:
        if query is None:
            return None
        raw = str(query).strip()
        if not raw:
            return None
        region = self.resolve_region(region) or region
        if raw in self.locations and self._loc_in_region(raw, region):
            return raw, self.locations[raw]
        key = self._location_idx.get(_norm(raw))
        if key and self._loc_in_region(key, region):
            return key, self.locations[key]
        norm = _norm(raw)
        # 「N号道路 / N号水路」必须按编号精确匹配。子串匹配会双向出错:
        # "1号道路" 命中 "31号道路"(输入被当成子串),而 "21号道路" 又会命中
        # "1号道路"(地点名是输入的子串),把人送到完全不相干的路上。
        m = re.search(r"(\d+)\s*号\s*(道路|水路)", raw)
        if m:
            want, water = int(m.group(1)), m.group(2) == "水路"
            for k, v in sorted(self.locations.items()):
                if region and v.get("region") != region:
                    continue
                mm = re.search(r"(?:^|-)(?:sea-)?route-(\d+)$", k)
                if not mm or int(mm.group(1)) != want:
                    continue
                if ("-sea-route-" in k) == water:
                    return k, v
            return None     # 说了地区又说清编号 → 没有就是没有,不要瞎猜
        cands: list[str] = []
        for k, v in self.locations.items():
            if region and v.get("region") != region:
                continue
            names = [_norm(k), _norm(v.get("name", "")), _norm(v.get("zh", ""))]
            names += [_norm(a) for a in (v.get("aliases") or [])]
            if any(norm and n and (norm in n or n in norm) for n in names):
                cands.append(k)
        # 指定了地区就不再回退到其它地区搜索:否则 `/前往 20号道路` 在城都会
        # 被解析到合众的同名道路,给出"跨地区"这种莫名其妙的提示。
        if not cands:
            return None
        for k in cands:
            v = self.locations[k]
            if norm in (_norm(v.get("zh", "")), _norm(v.get("name", ""))):
                return k, v
        return cands[0], self.locations[cands[0]]

    def find_location(self, query: str, region: str = "") -> str:
        r = self.resolve_location(query, region)
        return r[0] if r else ""

    def location_default_vg(self, key: str) -> str:
        pools = (self.locations.get(key) or {}).get("pools") or {}
        if not pools:
            return ""
        return max(pools, key=lambda g: int((self.version_groups.get(g) or {}).get("order", 0)))

    def location_pools(
        self,
        key: str,
        version_group: str = "",
        methods: set[str] | None = None,
        include_special: bool = False,
    ) -> list[dict]:
        """返回地点遭遇条目 [{species,min,max,method,chance}]。

        methods 显式指定时只保留这些方法;否则默认排除 SPECIAL_METHODS。
        """
        v = self.locations.get(key) or {}
        pools = v.get("pools") or {}
        vg = version_group or self.location_default_vg(key)
        rows = pools.get(vg)
        if rows is None:
            rows = [r for rr in pools.values() for r in rr]
        out: list[dict] = []
        for skey, lo, hi, method, chance in rows or []:
            if methods is not None:
                if method not in methods:
                    continue
            elif not include_special and method in SPECIAL_METHODS:
                continue
            out.append(
                {"species": skey, "min": int(lo), "max": int(hi), "method": method, "chance": int(chance)}
            )
        return out

    def list_locations(self, region: str = "") -> list[str]:
        keys = [k for k, v in self.locations.items() if not region or v.get("region") == region]
        return sorted(keys, key=lambda k: (self.locations[k].get("region", ""), k))


def _sort_methods(codes: set[str]) -> list[str]:
    level = sorted(
        (c for c in codes if c.startswith("L")), key=lambda c: int(c[1:] or 0)
    )
    other = sorted(c for c in codes if not c.startswith("L"))
    return level + other


@lru_cache(maxsize=1)
def get_dex() -> Dex:
    return Dex()


# 便捷函数(避免到处 get_dex())
def resolve_species(query: str):
    return get_dex().resolve_species(query)


def resolve_move(query: str):
    return get_dex().resolve_move(query)


def type_multiplier(attack_type: str, defender_types) -> float:
    return get_dex().type_multiplier(attack_type, defender_types)
