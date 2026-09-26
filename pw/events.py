"""每日事件(凌晨 4 点刷新)。

设计原则 —— **强规则 + LLM 只负责"选题与文案"**:
  · 事件种类(kind)、可施加的数值修正(effects)、地点/地区都限定在枚举内;
  · 所有数值由本模块裁剪到安全区间,LLM 无法凭空造出 +999 倍金钱;
  · LLM 失败/超时/输出非法时,回退到本地确定性事件生成器(永不空转)。
"""

from __future__ import annotations

import json
import re

from .dex import get_dex
from .items import BAG_ITEMS, resolve_bag_item
from .trainer import generate_team
from .util import clamp, stable_rng
from .world import REGION_ORDER, WorldMap

# ── 枚举:什么样的世界事件是合法的 ──────────────────────────────
WORLD_KINDS = {
    "rocket": "邪恶组织(火箭队等)占据某地,封锁道路,可前往挑战其成员",
    "swarm": "某地宝可梦大量出现(遭遇率提升)",
    "rare": "稀有宝可梦现身(稀有遭遇率提升)",
    "weather": "天气异常(该地区对战天气改变)",
    "sale": "商店打折(道具更便宜)",
    "festival": "庆典/集市(遭遇率与赏金提升)",
    "block": "道路封锁(塌方/施工/检查站)",
    "rumor": "只是传闻与线索,没有数值影响",
}

EFFECT_RANGES = {
    "encounter_mult": (0.5, 3.0),
    "rare_mult": (1.0, 5.0),
    "money_mult": (0.5, 3.0),
    "shop_discount": (0.5, 0.95),
    "battle_weather": None,  # 特殊:枚举
}
ALLOWED_WEATHER = ["sun", "rain", "sand", "snow"]

PLAYER_KINDS = {
    "gift_item": "捡到/收到道具",
    "gift_money": "捡到/收到零花钱",
    "wild_battle": "遭遇一只野生宝可梦",
    "meet_trainer": "遇到一位训练家发起挑战",
    "friend": "与自己的宝可梦增进感情",
    "rumor": "收到一条私人线索(无数值影响)",
    "lost_item": "丢了道具(少量惩罚)",
}

MAX_PLAYER_MONEY = 5000
MAX_WORLD_EVENTS = 3
MAX_PLAYER_EVENTS = 1


# ── 校验 / 裁剪 ──────────────────────────────────────────────────
# 必须**非贪婪且逐候选尝试**:`\{.*\}` 会把 `{"a":1} noise {"b":2}` 整段吞掉,
# 解析失败后返回 {} —— 明明有一个合法的 JSON 对象却丢弃了(LLM 常在对象后补说明文字)。
_JSON_BLOCK = re.compile(r"\{.*?\}", re.S)
_DECODER = json.JSONDecoder()


def parse_llm_json(text: str) -> dict:
    """从 LLM 输出里抠出第一个 JSON 对象(容忍 ```json 包裹/前后废话)。"""
    if not text:
        return {}
    s = text.strip()
    # 逐个 `{` 起点尝试 raw_decode:这样 `{"a":1} noise {"b":2}` 能拿到第一个对象,
    # 而嵌套对象也不会被非贪婪正则截断(截断后 JSON 会解析失败)。
    for i, ch in enumerate(s):
        if ch != "{":
            continue
        for candidate in (s[i:], s[i:].replace("'", '"')):
            try:
                data, _end = _DECODER.raw_decode(candidate)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
            break
    return {}


def _clean_effects(raw: object) -> dict:
    eff: dict = {}
    if not isinstance(raw, dict):
        return eff
    for k, rng in EFFECT_RANGES.items():
        if k not in raw:
            continue
        if rng is None:  # battle_weather:枚举而非区间
            w = str(raw[k] or "").strip().lower()
            if w in ALLOWED_WEATHER:
                eff[k] = w
            continue
        lo, hi = rng
        try:
            v = float(raw[k])
        except (TypeError, ValueError):
            continue
        eff[k] = round(clamp(v, lo, hi), 2)
    return eff


def sanitize_world_event(raw: object, state, day: int) -> dict | None:
    """把 LLM 给的一条世界事件裁剪成合法事件;不合法返回 None。"""
    if not isinstance(raw, dict):
        return None
    world = WorldMap()
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in WORLD_KINDS:
        return None
    region = world.resolve_region(str(raw.get("region") or ""))
    if not region:
        region = ""
    location = str(raw.get("location") or "").strip()
    if location and (not region or world.region_of(location) != region):
        location = ""
    if kind in ("rocket", "block", "swarm", "rare", "weather", "sale", "festival"):
        # 这些事件必须有一个真实地点
        if not location:
            region = region or "kanto"
            if world.nodes(region):
                rng = stable_rng("evt-loc", state.scope, day, raw.get("title"))
                location = rng.choice(world.list_towns(region) or list(world.nodes(region)))
        if not location:
            return None
    try:
        days = int(raw.get("days", 1) or 1)
    except (TypeError, ValueError):
        days = 1
    days = int(clamp(days, 1, 3))
    ev = {
        "kind": kind,
        "title": str(raw.get("title") or WORLD_KINDS[kind])[:40],
        "desc": str(raw.get("desc") or "")[:400],
        "region": region,
        "location": location,
        "effects": _clean_effects(raw.get("effects")),
        "days": days,
        "until_day": day + days - 1,
        "created_day": day,
        "source": "llm",
    }
    if kind == "rocket":
        ev["trainer"] = str(raw.get("leader") or "火箭队手下")[:20]
        ev["team"] = _rocket_team(state, day, location, raw.get("team"))
    if kind == "swarm" and raw.get("species"):
        r = get_dex().resolve_species(str(raw["species"]))
        if r:
            ev["species"] = r[0]
    if kind == "rare" and raw.get("species"):
        r = get_dex().resolve_species(str(raw["species"]))
        if r:
            ev["species"] = r[0]
    return ev


def _rocket_team(state, day: int, location: str, raw_team: object) -> list[dict]:
    """火箭队队伍:优先用 LLM 指定的合法物种,否则本地按地点分布生成。"""
    dex = get_dex()
    out: list[dict] = []
    if isinstance(raw_team, list):
        for item in raw_team[:4]:
            if isinstance(item, str):
                sp, lvl = item, 0
            elif isinstance(item, dict):
                sp, lvl = str(item.get("species") or ""), item.get("level")
            else:
                continue
            r = dex.resolve_species(sp)
            if not r:
                continue
            out.append({"species": r[0], "level": int(clamp(_as_int(lvl, 12), 2, 80))})
    if not out:
        team = generate_team(
            dex,
            party=[],
            trainer="火箭队干部",
            level=0,
            location=location,
            region=WorldMap().region_of(location),
        )
        out = [
            {"species": m["species"], "level": int(m["level"])}
            for m in (team.get("team") or [])[:3]
        ]
    return out


def _as_int(v, default: int) -> int:
    try:
        # OverflowError 必须一起捕获:json.loads 默认接受 Infinity
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return default


def sanitize_player_event(raw: object, trainer, day: int) -> dict | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in PLAYER_KINDS:
        return None
    ev = {
        "kind": kind,
        "title": str(raw.get("title") or PLAYER_KINDS[kind])[:40],
        "desc": str(raw.get("desc") or "")[:400],
        "day": day,
        "source": "llm",
    }
    if kind == "gift_item" or kind == "lost_item":
        r = resolve_bag_item(str(raw.get("item") or ""))
        if not r:
            ev["item"] = "potion"
            ev["n"] = 1
        else:
            ev["item"] = r[0]
            ev["n"] = int(clamp(_as_int(raw.get("n"), 1), 1, 5))
    elif kind == "gift_money":
        ev["money"] = int(clamp(_as_int(raw.get("money"), 500), 50, MAX_PLAYER_MONEY))
    elif kind == "friend":
        ev["friendship"] = int(clamp(_as_int(raw.get("friendship"), 8), 1, 20))
    elif kind in ("wild_battle", "meet_trainer"):
        dex = get_dex()
        r = dex.resolve_species(str(raw.get("species") or ""))
        level = 0
        if trainer.party:
            level = int(trainer.party[0].get("level", 5) or 5)
        ev["level"] = int(clamp(_as_int(raw.get("level"), level or 8), 2, 100))
        if r:
            ev["species"] = r[0]
        elif kind == "meet_trainer":
            team = generate_team(
                dex,
                party=trainer.party,
                trainer=str(raw.get("trainer") or "训练家"),
                location=trainer.location,
                region=trainer.region,
            )
            ev["team"] = [
                {"species": m["species"], "level": int(m["level"])}
                for m in (team.get("team") or [])[:3]
            ]
        if kind == "wild_battle" and "species" not in ev:
            return None
    return ev


# ── 本地兜底事件(LLM 不可用时) ──────────────────────────────────
FALLBACK_WORLD = [
    ("swarm", "宝可梦大量出现", {"encounter_mult": 1.8}),
    ("rare", "稀有宝可梦的传闻", {"rare_mult": 2.0}),
    ("festival", "小镇庆典", {"encounter_mult": 1.4, "money_mult": 1.3}),
    ("weather", "天气异常", {}),
    ("rocket", "可疑的黑衣人", {}),
    ("sale", "商店清仓", {"shop_discount": 0.8}),
    ("block", "道路施工", {}),
    ("rumor", "旅人的传闻", {}),
]


def fallback_world_events(state, day: int, *, regions: list[str] | None = None) -> list[dict]:
    world = WorldMap()
    regions = regions or [r for r in REGION_ORDER if world.nodes(r)] or ["kanto"]
    rng = stable_rng("fallback-world", state.scope, day)
    n = rng.randint(1, 2)
    out: list[dict] = []
    for _ in range(n):
        kind, title, eff = rng.choice(FALLBACK_WORLD)
        region = rng.choice(regions)
        towns = world.list_towns(region) or list(world.nodes(region))
        if not towns:
            continue
        location = rng.choice(towns)
        days = rng.randint(1, 2)   # days 与 until_day 必须自洽(否则"持续 2 天"当天就失效)
        ev = {
            "kind": kind,
            "title": title,
            "desc": "",
            "region": region,
            "location": location,
            "effects": dict(eff),
            "days": days,
            "until_day": day + days - 1,
            "created_day": day,
            "source": "fallback",
        }
        if kind == "weather":
            ev["effects"]["battle_weather"] = rng.choice(ALLOWED_WEATHER)
            ev["desc"] = f"{world.node_zh(location)}一带天气异常。"
        if kind == "rocket":
            ev["trainer"] = rng.choice(["火箭队手下", "火箭队干部"])
            ev["team"] = _rocket_team(state, day, location, None)
            ev["desc"] = f"{world.node_zh(location)}出现了可疑的黑衣人。"
        out.append(ev)
    return out


FALLBACK_PLAYER = [
    ("gift_item", "路边的小包裹", {"item": "potion", "n": 2}),
    ("gift_item", "好心人赠予", {"item": "poke-ball", "n": 3}),
    ("gift_money", "捡到钱包", {"money": 800}),
    ("friend", "一起露营", {"friendship": 10}),
    ("wild_battle", "草丛里的动静", {}),
    ("rumor", "奇怪的梦", {}),
]


def fallback_player_event(trainer, day: int) -> dict:
    rng = stable_rng("fallback-player", trainer.uid, day)
    kind, title, extra = rng.choice(FALLBACK_PLAYER)
    ev = {"kind": kind, "title": title, "desc": "", "day": day, "source": "fallback"}
    ev.update(extra)
    if kind == "wild_battle":
        hit = None
        from .battle import roll_wild

        hit = roll_wild(trainer, rng=rng)
        if not hit:
            return {"kind": "rumor", "title": "奇怪的梦", "desc": "", "day": day, "source": "fallback"}
        ev["species"] = hit["species"]
        ev["level"] = int(hit["level"])
    return ev


# ── 应用玩家事件 ─────────────────────────────────────────────────
def apply_player_event(trainer, ev: dict) -> list[str]:
    """把玩家事件的数值奖励落到存档上,返回给玩家看的描述行。"""
    kind = str(ev.get("kind") or "")
    out: list[str] = []
    if kind == "gift_item":
        key = ev.get("item") or "potion"
        n = int(ev.get("n", 1) or 1)
        trainer.add_item(key, n)
        zh = (BAG_ITEMS.get(key) or {}).get("zh", key)
        out.append(f"🎁 获得 {zh} ×{n}")
    elif kind == "lost_item":
        key = ev.get("item") or "potion"
        n = int(ev.get("n", 1) or 1)
        if trainer.take_item(key, n):
            zh = (BAG_ITEMS.get(key) or {}).get("zh", key)
            out.append(f"💧 失去了 {zh} ×{n}")
        else:
            out.append("💧 幸好背包里没什么可丢的。")
    elif kind == "gift_money":
        m = int(ev.get("money", 0) or 0)
        trainer.add_money(m)
        out.append(f"💰 获得 {m}₽(现有 {trainer.money}₽)")
    elif kind == "friend":
        f = int(ev.get("friendship", 5) or 5)
        for p in trainer.party:
            from .player import dict_to_mon, mon_to_dict

            mon = dict_to_mon(p)
            mon.friendship = min(255, mon.friendship + f)
            trainer.party[trainer.party.index(p)] = mon_to_dict(mon, p)
        out.append(f"💗 队伍亲密度 +{f}")
    return out


def event_text(ev: dict, *, world_only: bool = False) -> str:
    """事件的一句话展示。"""
    world = WorldMap()
    kind = str(ev.get("kind") or "")
    title = ev.get("title") or kind
    place = ""
    if ev.get("location"):
        place = f"{world.region_zh(ev.get('region') or '')}·{world.node_zh(ev['location'])}"
    days = int(ev.get("days", 1) or 1)
    tail = f"(持续 {days} 天)" if days > 1 else ""
    head = f"【{place}】" if place else ""
    desc = str(ev.get("desc") or "").strip()
    line = f"{head}{title}{tail}"
    if desc:
        line += f" —— {desc}"
    return line


# ── LLM 提示词 ───────────────────────────────────────────────────
WORLD_SYSTEM_PROMPT = """你是一款宝可梦文字游戏的"世界事件设计师"。
你只输出 JSON,不要输出任何解释、Markdown 代码块或额外文字。

你会收到:当前游戏日、可用的地区与城镇列表、最近几天的事件记录、玩家概况。
请设计 1~3 条"今天发生在世界上的事件",让长期游玩有新鲜感与压迫感。
要求:
- kind 必须是给定枚举之一;
- 地点必须来自给定列表(region 与 location 必须匹配);
- effects 只能使用给定键,数值必须在给定范围内;
- 事件之间不要重复同类;优先制造"玩家可以去做点什么"的驱动力;
- rocket(邪恶组织)类事件不要每天都有,要留出空档;
- 所有文字用简体中文,控制在 40 字以内,风格像游戏内广播。"""

PLAYER_SYSTEM_PROMPT = """你是一款宝可梦文字游戏的"个人事件设计师"。
你只输出 JSON,不要输出任何解释、Markdown 代码块或额外文字。

你会收到一名玩家的概况(名字/位置/队伍/徽章)。
请设计今天发生在"这名玩家身上"的 1 条小事件,让日常有温度或有惊喜。
要求:
- kind 必须是给定枚举之一;
- 奖励要克制(道具 1~3 个、金钱 ≤ 5000、亲密度 ≤ 20);
- 与玩家当前进度相称(新手不要给神兽);
- 所有文字用简体中文,40 字以内。"""


def build_world_prompt(state, day: int, players: list[dict]) -> str:
    world = WorldMap()
    regions = []
    for r in REGION_ORDER:
        towns = world.list_towns(r)
        if not towns:
            continue
        names = "、".join(f"{k}({world.node_zh(k)})" for k in towns[:12])
        regions.append(f"- {r}({world.region_zh(r)}):{names}")
    history = state.recent_log(5)
    active = state.active_events()
    lines = [
        f"游戏日:{day}",
        "地区与城镇(region(location 标识)):",
        *regions,
        "事件种类 kind 枚举:",
        *[f"  - {k}:{v}" for k, v in WORLD_KINDS.items()],
        "effects 可用键与范围:encounter_mult 0.5~3.0, rare_mult 1.0~5.0, "
        "money_mult 0.5~3.0, shop_discount 0.5~0.95, battle_weather(sun/rain/sand/snow)",
        f"最近 {len(history)} 天记录:"
        + ("; ".join(f"第{h['day']}天 {h['text']}" for h in history) or "无"),
        "仍在生效的事件:"
        + ("; ".join(event_text(e) for e in active) or "无"),
        "玩家概况:",
        *[
            f"  - {p.get('name')}({p.get('region')}/{p.get('location')})"
            f" 队伍 {p.get('party')} 只,徽章 {p.get('badges')}"
            for p in players[:8]
        ],
        "",
        '请输出:{"events":[{"kind":"...","title":"...","desc":"...",'
        '"region":"kanto","location":"pallet-town","days":1,'
        '"effects":{"encounter_mult":1.5},"species":"pikachu","leader":"火箭队干部",'
        '"team":[{"species":"rattata","level":12}]}]}',
    ]
    return "\n".join(lines)


def build_player_prompt(trainer, day: int) -> str:
    world = WorldMap()
    party = [
        f"{(p.get('nickname') or p.get('species'))} Lv{p.get('level')}"
        for p in trainer.party[:6]
    ]
    return "\n".join(
        [
            f"游戏日:{day}",
            f"玩家:{trainer.name}",
            f"位置:{world.region_zh(trainer.region)}·{world.node_zh(trainer.location)}",
            f"徽章:{trainer.badge_count()} 枚",
            f"金钱:{trainer.money}",
            f"队伍:{'、'.join(party) or '空'}",
            "事件种类 kind 枚举:",
            *[f"  - {k}:{v}" for k, v in PLAYER_KINDS.items()],
            "可选字段:item(道具标识,如 potion/poke-ball/super-potion)、n(1~5)、"
            "money(50~5000)、friendship(1~20)、species(宝可梦标识)、level(2~100)",
            "",
            '请输出:{"kind":"gift_item","title":"...","desc":"...","item":"potion","n":1}',
        ]
    )


def world_kind_catalog() -> str:
    return "\n".join(f"· {k} — {v}" for k, v in WORLD_KINDS.items())


def player_kind_catalog() -> str:
    return "\n".join(f"· {k} — {v}" for k, v in PLAYER_KINDS.items())
