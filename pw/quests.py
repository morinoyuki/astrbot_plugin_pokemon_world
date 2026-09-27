"""支线任务系统。

设计原则(和主线/神兽/大赛一致):
* **必须真的做事才能完成** —— 每个目标都挂在真实行为上(捕获/击败/赶路/进化/
  升级/消费/走路…),由内核在动作发生时推进;没有任何"过一天自动完成"的目标。
* **数值由内核结算**,LLM 只负责"选题与包装"(任务标题、委托人、描述),
  且它的输出要经过白名单 + 数值裁剪 + 可获得性校验;LLM 不可用时用本地生成器兜底。
* 完成即刻发放奖励(金钱 + 道具),并返回可读的提示文本。

存档结构(挂在 trainer.data 上):
    t.data["quests"] = {
        "active": [ {...}, ... ],      # 进行中
        "done": [ "quest-id", ... ],   # 已完成(只留 id,避免存档膨胀)
        "counter": 3,                  # 已生成过多少条(用于 id 唯一)
        "day": 5,                      # 上次生成任务的游戏日
    }
"""

from __future__ import annotations

import logging
from functools import lru_cache

from .dex import get_dex
from .util import stable_rng

logger = logging.getLogger("pw.quests")

# ════════════════════════════════════════════════════════════════
# 目标类型目录
# ════════════════════════════════════════════════════════════════
# 每种目标声明:中文名、需要的额外参数、进度单位、以及"是否可被 LLM 选用"。
# doable  = 是否有把握在当前进度下完成(LLM 选题时的提示,不做硬校验)
OBJECTIVES: dict[str, dict] = {
    "catch": {"zh": "捕获宝可梦", "unit": "只", "params": (), "min": 1, "max": 5},
    "catch_type": {
        "zh": "捕获指定属性",
        "unit": "只",
        "params": ("type",),
        "min": 1,
        "max": 4,
    },
    "catch_species": {
        "zh": "捕获指定宝可梦",
        "unit": "只",
        "params": ("species",),
        "min": 1,
        "max": 2,
    },
    "catch_fish": {"zh": "钓鱼捕获", "unit": "只", "params": (), "min": 1, "max": 3},
    "defeat": {"zh": "击败野生宝可梦", "unit": "只", "params": (), "min": 2, "max": 10},
    "defeat_type": {
        "zh": "击败指定属性",
        "unit": "只",
        "params": ("type",),
        "min": 2,
        "max": 6,
    },
    "defeat_trainer": {"zh": "战胜训练家", "unit": "人", "params": (), "min": 1, "max": 4},
    "gym": {"zh": "挑战道馆", "unit": "次", "params": (), "min": 1, "max": 2},
    "no_item_win": {"zh": "不使用道具取胜", "unit": "场", "params": (), "min": 1, "max": 3},
    "evolve": {"zh": "宝可梦进化", "unit": "次", "params": (), "min": 1, "max": 2},
    "level_up": {"zh": "宝可梦升级", "unit": "级", "params": (), "min": 3, "max": 15},
    "dex": {"zh": "登记新图鉴", "unit": "种", "params": (), "min": 1, "max": 4},
    "travel": {"zh": "前往新地点", "unit": "处", "params": (), "min": 1, "max": 3},
    "shop_spend": {"zh": "在商店消费", "unit": "₽", "params": (), "min": 500, "max": 3000},
    "steps": {"zh": "走路", "unit": "步", "params": (), "min": 300, "max": 1500},
}

# 可被视为"训练家"的战斗类型
TRAINER_KINDS = frozenset({"trainer", "gym", "elite", "champion", "rocket", "tournament"})

# LLM 允许选用的目标(没过徽章/没进度的玩家也能做的,避免生成做不了的任务)
LLM_OBJECTIVES = (
    "catch",
    "catch_type",
    "defeat",
    "defeat_type",
    "defeat_trainer",
    "no_item_win",
    "evolve",
    "level_up",
    "dex",
    "travel",
    "shop_spend",
    "steps",
)

# ── 奖励池(LLM 只能从这些道具里挑,且数量受限)──
REWARD_ITEMS: dict[str, dict] = {
    # 化石也给一条非商店途径(权重低)
    "old-amber": {"zh": "琥珀", "weight": 1, "max": 1},
    "skull-fossil": {"zh": "头盖化石", "weight": 1, "max": 1},
    "poke-ball": {"zh": "精灵球", "weight": 10, "max": 5},
    "great-ball": {"zh": "超级球", "weight": 6, "max": 3},
    "ultra-ball": {"zh": "高级球", "weight": 3, "max": 2},
    "heal-ball": {"zh": "治愈球", "weight": 3, "max": 2},
    "potion": {"zh": "伤药", "weight": 9, "max": 3},
    "super-potion": {"zh": "好伤药", "weight": 6, "max": 3},
    "hyper-potion": {"zh": "厉害伤药", "weight": 3, "max": 2},
    "antidote": {"zh": "解毒药", "weight": 4, "max": 2},
    "paralyze-heal": {"zh": "解麻药", "weight": 4, "max": 2},
    "revive": {"zh": "活力碎片", "weight": 2, "max": 1},
    # 招式机作为稀有奖励(获取途径之一;另外两条是道馆首次通关与商店)
    "tm-protect": {"zh": "招式机·守住", "weight": 3, "max": 1},
    "tm-aerialace": {"zh": "招式机·燕返", "weight": 3, "max": 1},
    "tm-shadowball": {"zh": "招式机·影子球", "weight": 2, "max": 1},
    "tm-icebeam": {"zh": "招式机·冰冻光束", "weight": 2, "max": 1},
    "tm-thunderbolt": {"zh": "招式机·十万伏特", "weight": 2, "max": 1},
    "ether": {"zh": "元气之粉", "weight": 3, "max": 2},
    "oran-berry": {"zh": "橙橙果", "weight": 5, "max": 3},
    "sitrus-berry": {"zh": "文柚果", "weight": 3, "max": 2},
    "lum-berry": {"zh": "木子果", "weight": 2, "max": 2},
    "rare-candy": {"zh": "神奇糖果", "weight": 1, "max": 1},
}

MONEY_MIN = 100
MONEY_MAX = 3000
MAX_ACTIVE = 4
DAILY_COUNT = 2
EXPIRE_DAYS = 3

# ── 委托人与标题模板(本地兜底生成器用,取自原作 NPC 印象)──
GIVERS = (
    "大木博士",
    "乔伊小姐",
    "商店店员",
    "捕虫少年阿明",
    "钓鱼老手",
    "登山男",
    "研究员",
    "旅行者",
    "宝可梦培育家",
    "迷你裙少女",
    "泳装姐姐",
    "退休训练家",
)

TITLE_TPL: dict[str, tuple[str, str]] = {
    "catch": ("帮忙补充图鉴", "研究需要活体的样本,先抓 {n} 只野生宝可梦回来。"),
    "catch_type": (
        "收集{type}属性样本",
        "我手头的{type}属性资料还不够,请帮我抓 {n} 只{type}属性的宝可梦。",
    ),
    "catch_species": (
        "寻找{species}",
        "有人托我找一只{species},你能帮我抓 {n} 只吗?",
    ),
    "catch_fish": ("钓鱼收获", "码头的订单压了我三天了 —— 钓 {n} 只宝可梦上来。"),
    "defeat": ("驱赶野生宝可梦", "{n} 只野生宝可梦总在路边闹事,请把它们打退。"),
    "defeat_type": (
        "清理{type}属性",
        "{type}属性的宝可梦最近太嚣张了,教训 {n} 只。",
    ),
    "defeat_trainer": ("对练请求", "陪我练 {n} 场吧,我会付报酬的。"),
    "gym": ("道馆观察记录", "去道馆挑战 {n} 次,把过程讲给我听。"),
    "no_item_win": ("硬实力的证明", "不用任何道具赢 {n} 场,这才叫实力。"),
    "evolve": ("进化的瞬间", "我想看看宝可梦进化的样子,让 {n} 只进化吧。"),
    "level_up": ("训练成果", "把你的队伍练起来,累计升 {n} 级。"),
    "dex": ("图鉴补全", "图鉴上还有空白 —— 登记 {n} 种没见过的新宝可梦。"),
    "travel": ("旅行见闻", "去 {n} 个没去过的地方,回来讲讲路上的事。"),
    "shop_spend": ("刺激消费", "在店里花掉 {n}₽,我把回扣分你一半。"),
    "steps": ("徒步修行", "陪我走 {n} 步再说别的。"),
}


# ════════════════════════════════════════════════════════════════
# 存档访问
# ════════════════════════════════════════════════════════════════
def _box(trainer) -> dict:
    data = trainer.data.setdefault("quests", {})
    data.setdefault("active", [])
    data.setdefault("done", [])
    data.setdefault("counter", 0)
    return data


def active(trainer) -> list[dict]:
    return [q for q in _box(trainer)["active"] if q.get("state", "active") == "active"]


def completed_ids(trainer) -> list[str]:
    return list(_box(trainer)["done"])


# ════════════════════════════════════════════════════════════════
# 目标描述
# ════════════════════════════════════════════════════════════════
def type_zh(key: str) -> str:
    return get_dex().type_zh.get(key, key)


def objective_text(obj: dict) -> str:
    kind = str(obj.get("kind") or "")
    spec = OBJECTIVES.get(kind)
    if not spec:
        return "未知目标"
    label = spec["zh"]
    if obj.get("type"):
        label = label.replace("指定属性", f"{type_zh(str(obj['type']))}属性")
    if obj.get("species"):
        label = label.replace("指定宝可梦", _species_zh(str(obj["species"])))
    return label


def _species_zh(key: str) -> str:
    entry = get_dex().species.get(key) or {}
    return str(entry.get("zh") or entry.get("name") or key)


def progress_text(q: dict) -> str:
    obj = q.get("objective") or {}
    cur = int(q.get("progress") or 0)
    need = max(1, int(obj.get("count") or 1))
    unit = (OBJECTIVES.get(str(obj.get("kind"))) or {}).get("unit", "")
    return f"{objective_text(obj)} {min(cur, need)}/{need}{unit}"


def reward_text(q: dict) -> str:
    r = q.get("reward") or {}
    parts = []
    if int(r.get("money") or 0):
        parts.append(f"{int(r['money'])}₽")
    for key, n in (r.get("items") or {}).items():
        parts.append(f"{_item_zh(str(key))}×{int(n)}")
    return " · ".join(parts) or "口头感谢"


def _item_zh(key: str) -> str:
    from .items import BAG_ITEMS

    entry = BAG_ITEMS.get(key) or {}
    return str(entry.get("zh") or key)


# ════════════════════════════════════════════════════════════════
# 校验与生成
# ════════════════════════════════════════════════════════════════
def _as_int(v, default: int) -> int:
    try:
        # 注意要捕获 OverflowError:json.loads 默认接受 Infinity,
        # int(float("inf")) 会抛 OverflowError,漏掉就会让整批委托丢失。
        return int(float(v))
    except (TypeError, ValueError, OverflowError):
        return default


def sanitize_quest(raw: object, trainer, day: int, *, idx: int = 0) -> dict | None:
    """把 LLM 输出裁剪成合法任务;不合格返回 None(调用方用本地生成器兜底)。"""
    if not isinstance(raw, dict):
        return None
    obj_raw = raw.get("objective")
    if not isinstance(obj_raw, dict):
        return None
    kind = str(obj_raw.get("kind") or "").strip()
    spec = OBJECTIVES.get(kind)
    if not spec or kind not in LLM_OBJECTIVES:
        return None

    dex = get_dex()
    obj: dict = {"kind": kind}
    # 参数校验:属性必须是真实属性,物种必须是真实且玩家能遇到的物种
    if "type" in spec["params"]:
        t_key = dex.resolve_type(str(obj_raw.get("type") or ""))
        if not t_key:
            return None
        obj["type"] = t_key
    if "species" in spec["params"]:
        # 物种必须真实存在,而且玩家真的能在某处遇到它(否则任务永远做不完)
        hit = dex.resolve_species(str(obj_raw.get("species") or ""))
        if not hit:
            return None
        species = str(hit[0])
        if not _world().locations_with_species(species, limit=1):
            return None
        obj["species"] = species

    count = max(int(spec["min"]), min(int(spec["max"]), _as_int(obj_raw.get("count"), spec["min"])))
    obj["count"] = count

    reward_raw = raw.get("reward") if isinstance(raw.get("reward"), dict) else {}
    money = max(0, min(MONEY_MAX, _as_int(reward_raw.get("money"), 0)))
    if money and money < MONEY_MIN:
        money = MONEY_MIN
    items: dict[str, int] = {}
    raw_items = reward_raw.get("items")
    for key, n in (raw_items if isinstance(raw_items, dict) else {}).items():
        pool = REWARD_ITEMS.get(str(key))
        if not pool or len(items) >= 2:
            continue
        items[str(key)] = max(1, min(int(pool["max"]), _as_int(n, 1)))
    if not money and not items:
        money = MONEY_MIN

    box = _box(trainer)
    box["counter"] = int(box.get("counter") or 0) + 1
    return {
        "id": f"q{day}-{box['counter']}",
        "day": day,
        "expire_day": day + EXPIRE_DAYS,
        "giver": _clean_text(raw.get("giver"), GIVERS, 12),
        "title": _clean_text(raw.get("title"), (), 18) or "委托",
        "desc": _clean_text(raw.get("desc"), (), 60),
        "objective": obj,
        "progress": 0,
        "reward": {"money": money, "items": items},
        "region": trainer.region,
        "state": "active",
    }


def _clean_text(v: object, pool: tuple[str, ...], limit: int) -> str:
    s = str(v or "").strip().replace("\n", " ")
    if pool and s not in pool:
        s = pool[0]
    return s[:limit]


@lru_cache(maxsize=1)
def _total_locations() -> int:
    """地图节点总数(用于判断玩家是否已经走遍全图)。"""
    try:
        from .world import REGION_ORDER, WorldMap

        world = WorldMap()
        return sum(len(world.nodes(r)) for r in REGION_ORDER)
    except Exception:
        return 10 ** 6


def fallback_quest(trainer, day: int, *, idx: int = 0) -> dict:
    """本地生成器:不依赖 LLM,用 (玩家, 天, 序号) 派生确定性任务。"""
    rng = stable_rng("quest", trainer.uid, day, idx)
    world = _world()
    loc = trainer.location
    region = trainer.region

    # 优先挑"眼前就能做"的目标:当前地点的属性池 / 是否有水 / 商店
    pools = world.wild_pools(loc) if loc else []
    types_now = []
    for p in pools:
        entry = get_dex().species.get(str(p.get("species") or "")) or {}
        for t in (entry.get("types") or [])[:1]:
            if t not in types_now:
                types_now.append(t)
    # 注意:遭遇方式是 "old-rod/good-rod/super-rod/surf" 这种key,
    # 商店服务名是 "mart"(不是 "shop")—— 写错就会永远刷不出这两类委托。
    has_water = any(
        any(t in str(p.get("method") or "").lower() for t in ("rod", "surf", "fish"))
        for p in pools
    )
    has_shop = "mart" in (world.services(loc) if loc else [])

    choices = ["catch", "defeat", "level_up", "steps"]
    # 已把全图走完 / 图鉴见满的玩家不该再拿到做不完的委托
    if len(trainer.data.get("visited") or []) < _total_locations():
        choices.append("travel")
    if len(trainer.data.get("dex_seen") or []) < len(get_dex().species):
        choices.append("dex")
    if types_now:
        choices += ["catch_type", "catch_type", "defeat_type"]
    if has_water:
        choices.append("catch_fish")
    if has_shop:
        choices.append("shop_spend")
    if trainer.badge_count() >= 1:
        choices += ["defeat_trainer", "no_item_win"]
    if len(trainer.party) >= 3:
        choices.append("evolve")

    kind = choices[rng.randrange(len(choices))]
    spec = OBJECTIVES[kind]
    scale = 1.0 + min(trainer.badge_count(), 8) * 0.08
    count = spec["min"] + rng.randrange(max(1, spec["max"] - spec["min"] + 1))
    obj: dict = {"kind": kind, "count": int(count)}

    tpl_title, tpl_desc = TITLE_TPL.get(kind, ("委托", "帮我个忙。"))
    fmt = {"n": count}
    if "type" in spec["params"]:
        obj["type"] = str(types_now[rng.randrange(len(types_now))]) if types_now else "Normal"
        fmt["type"] = type_zh(obj["type"])
    if "species" in spec["params"]:
        cands = [str(p.get("species")) for p in pools] or ["rattata"]
        obj["species"] = cands[rng.randrange(len(cands))]
        fmt["species"] = _species_zh(obj["species"])
        obj["count"] = 1

    money = int(min(MONEY_MAX, max(MONEY_MIN, (120 + 90 * min(count, 15)) * scale)))
    if kind == "shop_spend":
        money = max(MONEY_MIN, int(count * 0.25))
    items: dict[str, int] = {}
    keys = list(REWARD_ITEMS)
    weights = [REWARD_ITEMS[k]["weight"] for k in keys]
    for _ in range(rng.randint(1, 2)):
        pick = rng.choices(keys, weights=weights, k=1)[0]
        items[pick] = min(int(REWARD_ITEMS[pick]["max"]), items.get(pick, 0) + 1)
    if "catch" in kind or kind.startswith("defeat"):
        items.setdefault("poke-ball", 2)

    box = _box(trainer)
    box["counter"] = int(box.get("counter") or 0) + 1
    return {
        "id": f"q{day}-{box['counter']}",
        "day": day,
        "expire_day": day + EXPIRE_DAYS,
        "giver": GIVERS[rng.randrange(len(GIVERS))],
        "title": tpl_title.format(**fmt)[:18],
        "desc": tpl_desc.format(**fmt)[:60],
        "objective": obj,
        "progress": 0,
        "reward": {"money": money, "items": items},
        "region": region,
        "state": "active",
    }


def _world():
    from .world import WorldMap

    return WorldMap()


# ── 每日生成 ──
# ════════════════════════════════════════════════════════════════
# 去重
# ════════════════════════════════════════════════════════════════
def _obj_key(q: dict) -> tuple:
    """目标唯一标识:类别 + 参数 + 数量,全都一样才算同一个任务。"""
    obj = q.get("objective") if isinstance(q.get("objective"), dict) else {}
    return (
        str(obj.get("kind") or ""),
        str(obj.get("type") or ""),
        str(obj.get("species") or ""),
        int(obj.get("count") or 0),
    )


def _dedupe(trainer, day: int, box: dict, added: list[dict]) -> list[dict]:
    """去掉"标题或目标完全相同"的委托,返回最终新增的委托。

    实测(llm 出题)一次委托刷新生成了两条:
      捕虫少年阿明「帮忙补充图鉴」、迷你裙少女「帮忙补充图鉴」
    —— 标题一字不差,玩家看到的就是"同一个任务刷了两遍"。
    本地生成器也有同样风险(两次都掷到 `steps` 且数量相同时标题就撞了)。

    规则:标题不能重复,目标也不能重复;**还要跟已经挂着的旧委托比**,
    免得跨天刷出一模一样的。撞了的先用本地生成器换一条(idx 换着试),
    换不出来就**丢弃** —— 宁可少一条,也不要给玩家两个看起来一样的东西。
    """
    pre = active(trainer)[: max(0, len(active(trainer)) - len(added))]
    seen_t = {str(q.get("title") or "") for q in pre}
    seen_o = {_obj_key(q) for q in pre}
    keep: list[dict] = []
    for i, q in enumerate(added):
        title = str(q.get("title") or "")
        key = _obj_key(q)
        if title and title not in seen_t and key not in seen_o:
            seen_t.add(title)
            seen_o.add(key)
            keep.append(q)
            continue
        repl = None
        for j in range(8):                      # 换一条本地生成的
            cand = fallback_quest(trainer, day, idx=1000 + i * 10 + j)
            ct, ck = str(cand.get("title") or ""), _obj_key(cand)
            if ct and ct not in seen_t and ck not in seen_o:
                repl = cand
                break
        if repl is None:
            logger.debug("宝可梦世界: 委托重复且换不出新任务,丢弃一条:%s", title or key)
            continue
        seen_t.add(str(repl.get("title") or ""))
        seen_o.add(_obj_key(repl))
        keep.append(repl)
    # `added` 里的条目**已经**追加进 box["active"] 了,这里按对象身份(id)
    # 原地替换成 keep 里的版本、丢掉被去重掉的 —— 用 `in` 比较会按值相等误判。
    repl = {id(o): k for o, k in zip(added, keep, strict=False)}
    box["active"] = [
        (repl.get(id(q)) or q) if any(q is o for o in added) else q
        for q in box["active"]
    ]
    return keep


def roll_daily(trainer, day: int, *, count: int = DAILY_COUNT) -> list[dict]:
    """不依赖 LLM 的每日任务生成(幂等:同一天只生成一次)。"""
    box = _box(trainer)
    if int(box.get("day") or 0) == day:
        return []
    box["day"] = day
    _expire(trainer, day)
    room = MAX_ACTIVE - len(active(trainer))
    added: list[dict] = []
    for i in range(max(0, min(count, room))):
        q = fallback_quest(trainer, day, idx=i)
        box["active"].append(q)
        added.append(q)
    return _dedupe(trainer, day, box, added)


async def roll_daily_async(trainer, day: int, narrator, *, count: int = DAILY_COUNT) -> list[dict]:
    """优先让 LLM 出题,失败/非法则逐条回退到本地生成器。"""
    box = _box(trainer)
    if int(box.get("day") or 0) == day:
        return []
    box["day"] = day
    _expire(trainer, day)
    room = MAX_ACTIVE - len(active(trainer))
    want = max(0, min(count, room))
    if not want:
        return []
    added: list[dict] = []
    if narrator is not None and getattr(narrator, "available", lambda: False)():
        try:
            data = await narrator.json(SYSTEM_PROMPT, build_prompt(trainer, day, want), fallback="{}")
        except Exception as exc:  # LLM 任何异常都不该影响游戏
            logger.debug("宝可梦世界 支线任务 LLM 失败: %s", exc)
            data = {}
        raw_list = data.get("quests") if isinstance(data, dict) else None
        if isinstance(raw_list, list):
            for i, raw in enumerate(raw_list[:want]):
                try:
                    q = sanitize_quest(raw, trainer, day, idx=i)
                except Exception:  # 单条脏数据只降级这一条,不影响其它
                    logging.getLogger("pw.quests").debug("委托条目裁剪失败", exc_info=True)
                    q = None
                if q:
                    box["active"].append(q)
                    added.append(q)
    for i in range(want - len(added)):
        q = fallback_quest(trainer, day, idx=i)
        box["active"].append(q)
        added.append(q)
    return _dedupe(trainer, day, box, added)


def _expire(trainer, day: int) -> list[dict]:
    """过期未完成的任务直接移除(不惩罚玩家,只是委托失效)。"""
    box = _box(trainer)
    gone = [
        q
        for q in box["active"]
        if int(q.get("expire_day") or 0) and day >= int(q["expire_day"])
    ]
    if gone:
        box["active"] = [q for q in box["active"] if q not in gone]
    return gone


# ════════════════════════════════════════════════════════════════
# 进度推进(由内核在真实动作后调用)
# ════════════════════════════════════════════════════════════════
def note(trainer, event: str, **kw) -> list[str]:
    """按事件推进所有进行中任务的进度,返回"任务完成"提示行。

    event 取值与 kw:
      catch      : species, types, method
      win        : kind, species, types, used_item
      travel     : location, new
      evolve     : species
      level_up   : levels
      dex        : species
      shop       : amount
      steps      : steps
    """
    lines: list[str] = []
    for q in list(active(trainer)):
        obj = q.get("objective") or {}
        need = max(1, int(obj.get("count") or 1))
        delta = _delta(obj, event, kw)
        if delta <= 0:
            continue
        q["progress"] = min(need, int(q.get("progress") or 0) + delta)
        if int(q["progress"]) >= need:
            lines.extend(_finish(trainer, q))
    return lines


def _delta(obj: dict, event: str, kw: dict) -> int:
    kind = str(obj.get("kind") or "")
    types = [str(t) for t in (kw.get("types") or [])]
    if kind == "catch" and event == "catch":
        return 1
    if kind == "catch_type" and event == "catch":
        return 1 if obj.get("type") in types else 0
    if kind == "catch_species" and event == "catch":
        return 1 if str(obj.get("species")) == str(kw.get("species")) else 0
    if kind == "catch_fish" and event == "catch":
        return 1 if str(kw.get("method") or "").lower().startswith("fish") else 0
    if kind == "defeat" and event == "win":
        return 1 if not kw.get("is_trainer") else 0
    if kind == "defeat_type" and event == "win":
        return 1 if (not kw.get("is_trainer")) and obj.get("type") in types else 0
    if kind == "defeat_trainer" and event == "win":
        return 1 if kw.get("is_trainer") else 0
    if kind == "gym" and event == "win":
        return 1 if str(kw.get("kind")) == "gym" else 0
    if kind == "no_item_win" and event == "win":
        return 1 if not kw.get("used_item") else 0
    if kind == "evolve" and event == "evolve":
        return 1
    if kind == "level_up" and event == "level_up":
        return max(0, int(kw.get("levels") or 0))
    if kind == "dex" and event == "dex":
        return 1
    if kind == "travel" and event == "travel":
        return 1 if kw.get("new") else 0
    if kind == "shop_spend" and event == "shop":
        return max(0, int(kw.get("amount") or 0))
    if kind == "steps" and event == "steps":
        return max(0, int(kw.get("steps") or 0))
    return 0


def _finish(trainer, q: dict) -> list[str]:
    """结算一条任务:发奖励、移入已完成。"""
    box = _box(trainer)
    box["active"] = [x for x in box["active"] if x.get("id") != q.get("id")]
    box["done"].append(str(q.get("id")))
    return [_reward_line(trainer, q)]


def _reward_line(trainer, q: dict) -> str:
    r = q.get("reward") or {}
    got: list[str] = []
    money = int(r.get("money") or 0)
    if money:
        trainer.add_money(money)
        got.append(f"{money}₽")
    for key, n in (r.get("items") or {}).items():
        trainer.add_item(str(key), int(n))
        got.append(f"{_item_zh(str(key))}×{int(n)}")
    return f"◆ 委托完成:{q.get('title')}({q.get('giver')}) —— 获得 {' · '.join(got) or '感谢'}!"


def abandon(trainer, index: int) -> dict | None:
    """按序号(从 1 开始,对应 /任务 列表)放弃一条委托。"""
    box = _box(trainer)
    acts = active(trainer)
    if not (1 <= index <= len(acts)):
        return None
    q = acts[index - 1]
    box["active"] = [x for x in box["active"] if x.get("id") != q.get("id")]
    return q


# ════════════════════════════════════════════════════════════════
# 文本 / LLM 提示
# ════════════════════════════════════════════════════════════════
def panel_text(trainer, *, limit: int = 6) -> str:
    acts = active(trainer)
    if not acts:
        return "今天还没有委托。等新的一天(`/今日`)刷新,或看看主线进展。"
    lines = [f"📋 进行中的委托({len(acts)}/{MAX_ACTIVE})"]
    for i, q in enumerate(acts[:limit], 1):
        lines.append(f"{i}. 【{q.get('giver')}】{q.get('title')}")
        if q.get("desc"):
            lines.append(f"   {q['desc']}")
        lines.append(f"   进度:{progress_text(q)}")
        lines.append(f"   报酬:{reward_text(q)}")
    if len(acts) > limit:
        lines.append(f"…… 还有 {len(acts) - limit} 条")
    lines.append("输入 `/任务 放弃 <序号>` 可以撇下某条委托。")
    return "\n".join(lines)


OBJECTIVE_CATALOG = "\n".join(
    f"- {k}:{v['zh']}"
    + (f"(需要参数 {', '.join(v['params'])})" if v["params"] else "")
    + f",建议数量 {v['min']}~{v['max']}"
    for k, v in OBJECTIVES.items()
    if k in LLM_OBJECTIVES
)

REWARD_CATALOG = "\n".join(
    f"- {k}:{v['zh']},最多 {v['max']} 个" for k, v in REWARD_ITEMS.items()
)

SYSTEM_PROMPT = f"""你是宝可梦世界的委托板管理员,为玩家生成"支线委托"。

委托必须满足:
1. **玩家必须真的做事才能完成** —— 只能从下面这些目标类型里选,不要发明新类型;
2. 目标要能用玩家当前进度完成(比如刚出发的玩家不要让他"挑战道馆 2 次");
3. 奖励是金钱与道具,数值要克制:金钱 {MONEY_MIN}~{MONEY_MAX},道具只能从奖励池里挑。

可用的目标类型:
{OBJECTIVE_CATALOG}

可用的奖励道具:
{REWARD_CATALOG}

只输出 JSON,不要多余文字:
{{"quests": [{{"giver": "委托人名字", "title": "标题(≤12字)", "desc": "一句话说明(≤40字)",
"objective": {{"kind": "目标类型", "count": 数字, "type": "属性英文名(仅 catch_type/defeat_type 需要)"}},
"reward": {{"money": 数字, "items": {{"道具 key": 数量}}}}}}]}}"""


def build_prompt(trainer, day: int, want: int) -> str:
    world = _world()
    pools = world.wild_pools(trainer.location) if trainer.location else []
    local_types = []
    for p in pools:
        entry = get_dex().species.get(str(p.get("species") or "")) or {}
        for t in (entry.get("types") or [])[:1]:
            if t not in local_types:
                local_types.append(t)
    party = "、".join(
        f"{_species_zh(str(m.get('species')))}Lv{int(m.get('level') or 1)}"
        for m in trainer.party[:6]
    )
    return (
        f"世界日:第 {trainer.day_no(day)} 天\n"
        f"玩家:{trainer.name}\n"
        f"所在地区:{world.region_zh(trainer.region)}"
        f"(第 {int(world.regions.get(trainer.region, {}).get('order') or 1)} 地区)\n"
        f"当前地点:{world.node_zh(trainer.location)}\n"
        f"徽章数:{trainer.badge_count()} / {len(world.gyms(trainer.region)) or 8}\n"
        f"队伍:{party or '无'}\n"
        f"此地野生宝可梦属性:{'、'.join(type_zh(t) for t in local_types) or '未知'}\n"
        f"硬币:{trainer.money}\n"
        f"请生成 {want} 条互不重复的委托。"
    )
