"""成长系统:经验 / 升级 / 学招 / 进化(严格按图鉴数据,不靠 LLM 编)。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .dex import get_dex
from .engine import Pokemon


@dataclass
class GrowthResult:
    """一次经验结算的全部事实(交给 LLM 只做润色,不得改动数值)。"""

    levels_gained: int = 0
    learned: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    evolved_from: str = ""
    evolved_to: str = ""
    capped: bool = False
    messages: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.levels_gained
            or self.learned
            or self.evolved_to
            or self.capped
        )


def move_zh(key: str) -> str:
    return ((get_dex().moves.get(key) or {}).get("zh")) or key


def set_pending(md: dict, moves) -> list[str]:
    """把**这一批**新招式一起放进待决定,返回被顶掉的上一批旧待定。

    规则(按设计确定):
    · 一次升级里学到的多招(一口气升几级 / 同级多个招式)**全部算待决定**,
      玩家在 `/学招` 里挨个决定"替换哪一招"或"放弃";
    · 但旧的一批还没决定就又来了新的一批 → 旧的那批算放弃(等于是错过)。
    """
    new = [str(m) for m in (moves or []) if m]
    if not new:
        md["pending"] = []
        return []
    old = [str(m) for m in (md.get("pending") or []) if str(m) not in new]
    # 同批内的重复去掉,顺序保持出现顺序(玩家按这个顺序挨个决定)
    md["pending"] = list(dict.fromkeys(new))
    return old


def move_brief(key: str | None) -> str:
    """招式**一行简介**:名字 [属性/分类] 威力 + 一句效果。

    给"待替换招式"这类列表用 —— 只写名字玩家不知道这招做什么
    (完整资料走 `/招式 <序号>`;这里必须塞得进一行)。
    """
    d = get_dex()
    k = str(key or "")
    e = d.moves.get(k) or {}
    zh = e.get("zh") or move_zh(k)
    head = f"{zh} [{d.type_label(str(e.get('type') or ''))}/" \
           f"{d.move_category_zh(str(e.get('category') or ''))}]"
    base = int(e.get("basePower") or 0)
    if base:
        head += f" 威力{base}"
    eff = d.move_short_desc(k)
    return f"{head} —— {eff}" if eff else head


def species_zh(key: str) -> str:
    return ((get_dex().species.get(key) or {}).get("zh")) or key


def learn_move(mon: Pokemon, move_key: str) -> bool:
    """写入招式与 PP(不校验是否已会)。"""
    dex = get_dex()
    if move_key not in dex.moves or move_key in mon.moves:
        return False
    if len(mon.moves) >= 4:
        return False
    mon.moves.append(move_key)
    mon.pp[move_key] = int((dex.moves.get(move_key) or {}).get("pp", 10) or 10)
    return True


def replace_move(mon: Pokemon, old_key: str, new_key: str) -> bool:
    dex = get_dex()
    if new_key not in dex.moves or old_key not in mon.moves:
        return False
    if new_key in mon.moves:
        return False
    mon.moves[mon.moves.index(old_key)] = new_key
    mon.pp.pop(old_key, None)
    mon.pp[new_key] = int((dex.moves.get(new_key) or {}).get("pp", 10) or 10)
    if mon.last_move == old_key:
        mon.last_move = ""
    return True


def recompute(mon: Pokemon, *, heal_delta: bool = True) -> int:
    """按当前等级/个体/努力/性格重算数值,HP 按最大值变化量同步。"""
    dex = get_dex()
    old_max = mon.max_hp
    mon.stats = dex.compute_stats(
        mon.species, mon.level, mon.ivs, mon.evs, mon.nature
    )
    mon.max_hp = max(1, int(mon.stats.get("hp", 1)))
    delta = mon.max_hp - old_max
    if heal_delta and delta:
        if mon.cur_hp > 0:
            mon.cur_hp = max(1, min(mon.max_hp, mon.cur_hp + delta))
        else:
            mon.cur_hp = 0
    mon.cur_hp = max(0, min(mon.max_hp, mon.cur_hp))
    return delta


def auto_evolve(
    mon: Pokemon,
    *,
    daytime: str | None = None,
    trade: bool = False,
    party=(),
    pending_out: list[str] | None = None,
) -> str:
    """结算"升级即以当前条件成立"的进化(等级/亲密度/招式/携带/能力值)。

    道具进化(useItem)与通信进化不在此自动触发 —— 需要玩家显式使用道具或交换。
    返回进化后的物种 key;未进化返回 ""。
    """
    dex = get_dex()

    def _options():
        return dex.evolution_options(
            mon.species,
            level=mon.level,
            moves=set(mon.moves),
            item=mon.item or None,
            friendship=mon.friendship,
            gender=mon.gender,
            trade=trade,
            daytime=daytime,
            stats=mon.stats,
            party=party,
        )

    # 循环结算:一次大额经验会跨过多级门槛(小火龙→火恐龙→喷火龙),
    # 只进化一次会卡在中间形态,而到了 Lv100 就再也补不上(只能靠 /进化)。
    evolved = ""
    for _ in range(4):          # 最多 4 段,防数据环状导致死循环
        picked = ""
        for opt in _options():
            if not opt.get("met") or opt.get("kind") in ("useItem", "trade"):
                continue
            picked = str(opt["target"])
            break
        if not picked:
            break
        evolved = apply_evolution(mon, picked, pending_out=pending_out)
        if not evolved:
            break
    return evolved


def apply_evolution(
    mon: Pokemon, target: str, *, pending_out: list[str] | None = None
) -> str:
    """执行进化:换物种、重算数值(HP 同比例)、学进化招式、保留昵称与招式。

    招式栏已满时,进化该学的新招式**不再静默丢掉**,而是追加到 `pending_out`
    (由调用方记到存档的 pending 里,等玩家自己决定替换还是放弃)——
    规则与升级一致:新招式必须由玩家二选一。
    """
    dex = get_dex()
    if target not in dex.species or target == mon.species:
        return ""
    old_key = mon.species
    old_max = mon.max_hp
    old_hp = mon.cur_hp
    mon.species = target
    # 特性若仍是原种可用的则保留,否则换新种的默认特性
    entry = dex.species.get(target) or {}
    abilities = entry.get("abilities") or {}
    valid = {str(v) for v in abilities.values() if v}
    if mon.ability not in valid:
        default = abilities.get("0")
        r = dex.resolve_ability(default) if default else None
        mon.ability = r[0] if r else ""
    # 太晶属性:若原来是"属性跟随"的默认值,则跟随新属性
    types = entry.get("types") or []
    if not mon.tera_type or mon.tera_type in (dex.species.get(old_key, {}).get("types") or []):
        mon.tera_type = types[0] if types else mon.tera_type
    recompute(mon, heal_delta=False)
    if old_max > 0 and old_hp > 0:
        mon.cur_hp = max(1, min(mon.max_hp, round(old_hp * mon.max_hp / old_max)))
    # 进化时习得的 L0 招式
    for mv in dex.level_moves(target, 0):
        if mv in mon.moves:
            continue
        if len(mon.moves) < 4:
            learn_move(mon, mv)
        elif pending_out is not None and mv not in pending_out:
            pending_out.append(mv)
    return target


def gain_exp(
    mon: Pokemon, amount: int, *, daytime: str | None = None, party=()
) -> GrowthResult:
    """结算经验:升到足够等级、自动学招(未满 4 招)、条件满足则进化。"""
    dex = get_dex()
    res = GrowthResult()
    amount = max(0, int(amount))
    if amount <= 0:
        return res
    if mon.level >= 100:
        # 满级不再涨经验,但**仍要尝试进化**:否则 Lv100 才凑齐条件的宝可梦
        # (小球飞鱼、亲密度进化、携带道具进化)会永久停在非最终形态。
        res.capped = True
        before = mon.species
        evolved = auto_evolve(mon, daytime=daytime, party=party)
        if evolved:
            res.evolved_from = before
            res.evolved_to = evolved
        return res

    mon.exp += amount
    growth = dex.growth_of(mon.species)
    new_level = min(100, dex.level_from_exp(growth, mon.exp))
    if new_level <= mon.level:
        return res

    old_level = mon.level
    mon.level = new_level
    res.levels_gained = new_level - old_level
    recompute(mon)

    for mv in dex.level_up_moves(mon.species, old_level, new_level):
        if mv in mon.moves or mv in res.learned:
            continue
        if len(mon.moves) < 4:
            learn_move(mon, mv)
            res.learned.append(mv)
        else:
            res.pending.append(mv)
    if mon.level >= 100:
        res.capped = True

    before_species = mon.species
    evolved = auto_evolve(mon, daytime=daytime, party=party, pending_out=res.pending)
    if evolved:
        res.evolved_from = before_species
        res.evolved_to = evolved
    return res


# ── 亲密度 ──────────────────────────────────────────────────────
# 与正作一致:0~255,进化条件多要求 ≥100(见 dex 数据),报恩/迁怒按它算威力。
FRIEND_TIERS: tuple[tuple[int, str], ...] = (
    (220, "形影不离"), (180, "挚友"), (100, "亲密"),
    (60, "熟悉"), (25, "认识"), (0, "陌生"),
)


def _friend_value(value) -> int:
    try:
        return max(0, min(255, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def friendship_tier(value) -> str:
    """亲密度档位名(0~255 从高到低匹配)。"""
    v = _friend_value(value)
    for floor, name in FRIEND_TIERS:
        if v >= floor:
            return name
    return "陌生"


def friendship_hearts(value, *, slots: int = 5) -> str:
    """♥♥♡♡♡ —— 0~255 映射成 0~5 颗心。"""
    full = round(_friend_value(value) / 255 * slots)
    return "♥" * full + "♡" * (slots - full)


def add_friendship(mon, delta: int) -> int:
    """增减亲密度(夹在 0~255),返回实际变化量;对象/字典都能用。"""
    try:
        cur = int(getattr(mon, "friendship", None) or mon["friendship"])
    except Exception:
        cur = 70
    new = _friend_value(cur + int(delta))
    try:
        mon.friendship = new
    except Exception:
        mon["friendship"] = new
    return new - cur


def friend_line(value) -> str:
    """一行亲密度文案(文字回退与资料页共用)。"""
    v = _friend_value(value)
    return f"{v}/255 {friendship_tier(v)} {friendship_hearts(v)}"
