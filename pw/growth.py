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
        evolved = apply_evolution(mon, picked)
        if not evolved:
            break
    return evolved


def apply_evolution(mon: Pokemon, target: str) -> str:
    """执行进化:换物种、重算数值(HP 同比例)、学进化招式、保留昵称与招式。"""
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
    evolved = auto_evolve(mon, daytime=daytime, party=party)
    if evolved:
        res.evolved_from = before_species
        res.evolved_to = evolved
    return res
