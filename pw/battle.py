"""对战会话:野生 / 训练家 / 道馆 / 四天王 / 冠军 / 火箭队。

一切数值(伤害、经验、金钱、徽章、捕获)都由本地内核给出,LLM 只负责
把这些事实写成文字。存档里只保留一份 `Battle` 快照,便于随时中断/续战。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import growth
from .dex import get_dex
from .encounter import roll_level, roll_location_encounter
from .engine import Pokemon, battle_from_dict, create_pokemon, start_battle
from .items import resolve_bag_item
from .player import Trainer, dict_to_mon, mon_to_dict
from .util import clamp, hash_int, stable_rng
from .world import WorldMap

BATTLE_KINDS = {
    "wild": "野生宝可梦",
    "trainer": "训练家",
    "gym": "道馆馆主",
    "trial": "考验",
    "elite": "四天王",
    "champion": "冠军",
    "rocket": "火箭队",
    "legend": "传说宝可梦",
    "tournament": "世界大赛",
}

# 道馆(及其他头衔战)失败时的罚金比例
LOSS_MONEY_RATE = 0.5


@dataclass
class TurnResult:
    lines: list[str] = field(default_factory=list)
    finished: bool = False
    outcome: str = ""  # win / loss / caught / escaped / forfeit / ""
    item_key: str = ""  # 本次投出的精灵球 key(捕获画面要显示实际用的球)
    awaiting_switch: bool = False
    growth: list[str] = field(default_factory=list)
    # **按宝可梦分开**的成长明细:结算后要单独给"升级的那一只"出成长卡,
    # 光靠 growth 里的文字行去反推会把别的队员的"学会/待学"也算进去
    # (实测:杰尼龟的"想学「缩入壳中」"出现在了波波的成长卡上)。
    growth_detail: list[dict] = field(default_factory=list)
    levels_gained: int = 0          # 本次战斗累计升了几级(任务系统用)
    evolved: list[str] = field(default_factory=list)  # 本次战斗发生进化的物种 key
    rewards: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def daytime_of(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return "day" if 6 <= now.hour < 18 else "night"


def _level_bounds(rows: list[dict]) -> tuple[int, int]:
    lo = min((r["min"] for r in rows), default=2)
    hi = max((r["max"] for r in rows), default=lo)
    return max(2, lo), max(2, hi)


# ── 组建敌方队伍 ─────────────────────────────────────────────────
def build_enemy(specs) -> list[Pokemon]:
    out: list[Pokemon] = []
    for s in specs or []:
        if isinstance(s, Pokemon):
            out.append(s)
            continue
        sp = s.get("species") or ""
        if not sp:
            continue
        out.append(
            create_pokemon(
                sp,
                int(s.get("level", 5) or 5),
                nickname=str(s.get("nickname") or ""),
                moves=list(s.get("moves") or []) or None,
                item=str(s.get("item") or ""),
                ability=str(s.get("ability") or ""),
                gender=str(s.get("gender") or ""),
            )
        )
    return out


# ── 野生遭遇 ─────────────────────────────────────────────────────
def roll_wild(trainer: Trainer, *, rng=None, environment: str = "") -> dict | None:
    """按当前地点的真实分布抽一只野生宝可梦。"""
    dex = get_dex()
    loc = trainer.location
    if not loc:
        return None
    r = rng or stable_rng("wild", loc, trainer.data.get("steps", 0))
    hit = roll_location_encounter(dex, loc, environment=environment or "land", rng=r)
    if hit is None:
        # 陆地没有就试试水里/钓鱼
        for env in ("water", "fish"):
            hit = roll_location_encounter(dex, loc, environment=env, rng=r)
            if hit:
                break
    if hit is None:
        return None
    # 等级必须落在**收敛后的核心区间**内:world.wild_pools 会把钓鱼/摇树等
    # 侧池压进该地点的核心区间,而 roll_location_encounter 用的是原始区间
    # (tier1 的 3 号道路因此能刷出 Lv35 的水面遭遇)。
    lo, hi = int(hit.get("min") or 0), int(hit.get("max") or 0)
    try:
        from .world import WorldMap

        cand = next(
            (p for p in WorldMap().wild_pools(loc) if p.get("species") == hit.get("species")),
            None,
        )
        if cand:
            lo, hi = int(cand["min"]), int(cand["max"])
    except Exception:  # 拿不到收敛区间时退回原始区间,不影响对战
        pass
    if lo > 0 and hi >= lo:
        hit["level"] = r.randint(lo, hi)
    elif not hit.get("level"):
        hit["level"] = roll_level(dex, trainer.party, rng=r) or 5
    hit["level"] = int(clamp(hit["level"], 2, 100))
    return hit


# ── 会话状态 ─────────────────────────────────────────────────────
def session(trainer: Trainer) -> dict:
    return trainer.data.get("battle") or {}


def in_battle(trainer: Trainer) -> bool:
    """是否有一场**进行中**的对战(已结束但留着给画面用的不算)。"""
    b = session(trainer).get("battle")
    return bool(b) and not bool(b.get("finished"))


def clear_finished(trainer: Trainer) -> None:
    """清掉"已经打完、只为渲染而留着"的对战数据。"""
    b = session(trainer).get("battle")
    if b and b.get("finished"):
        trainer.data["battle"] = None


class BattleError(Exception):
    """对战操作错误(直接把 message 给玩家)。"""


def start(
    trainer: Trainer,
    enemy_specs,
    *,
    kind: str = "wild",
    wild: bool = False,
    meta: dict | None = None,
    weather: str = "",
    seed: int = 0,
    day: int = 0,
) -> list[str]:
    """开始一场对战,返回开场日志。"""
    if in_battle(trainer):
        raise BattleError("⚠️ 还有一场对战没有结束。")
    if trainer.all_fainted():
        raise BattleError("❌ 你的宝可梦全都失去战斗能力了,先去宝可梦中心治疗。")
    enemy = build_enemy(enemy_specs)
    if not enemy:
        raise BattleError("❌ 对手队伍是空的。")
    party = trainer.party_mon()
    battle = start_battle(
        party,
        enemy,
        weather=weather,
        seed=int(seed) or hash_int("battle", trainer.uid, trainer.location, kind, day),
        wild=bool(wild),
        bag=dict(trainer.bag),
    )
    log = battle.start()
    for mon in enemy:
        trainer.mark_seen(mon.species)
    trainer.data["battle"] = {
        "kind": kind,
        "wild": bool(wild),
        "battle": battle.to_dict(),
        "meta": dict(meta or {}),
        "started_day": int(day or 0),
        "log": list(log)[-12:],
    }
    return log


def _load(trainer: Trainer):
    data = trainer.data.get("battle")
    if not data:
        raise BattleError("❌ 当前没有进行中的对战。")
    return data


def _store(trainer: Trainer, data: dict, log: list[str]) -> None:
    data["log"] = list(log)[-12:]
    trainer.data["battle"] = data


def _sync(trainer: Trainer, battle) -> None:
    """把对战中的队伍/背包写回存档(HP/PP/异常/道具消耗)。"""
    for i, mon in enumerate(battle.player.party):
        if i < len(trainer.party):
            trainer.party[i] = mon_to_dict(mon, trainer.party[i])
    trainer.data["bag"] = {k: v for k, v in battle.bag.items() if int(v) > 0}


# ── 行动解析 ─────────────────────────────────────────────────────
def parse_action(raw: str, trainer: Trainer, battle) -> dict:
    """把玩家输入解析成引擎 action;失败抛 BattleError(不消耗回合)。"""
    dex = get_dex()
    s = str(raw or "").strip()
    if not s:
        raise BattleError("❌ 请输入行动,例如 `move 十万伏特`、`switch 2`、`item 伤药`。")
    low = s.lower()
    tera = False
    if low.startswith("tera ") or s.startswith("太晶"):
        tera = True
        s = s[5:].strip() if low.startswith("tera ") else s[2:].strip()
        low = s.lower()

    if low in ("run", "flee", "逃跑", "逃走"):
        return {"type": "run"}
    if low in ("forfeit", "giveup", "投降", "认输"):
        return {"type": "forfeit"}

    if low.startswith(("catch", "ball", "throw")) or s.startswith(
        ("投球", "捕获", "捕捉")
    ):
        rest = s.split(" ", 1)[1].strip() if " " in s else ""
        key = "poke-ball"
        if rest:
            r = resolve_bag_item(rest)
            if not r:
                raise BattleError(f"❌ 未收录精灵球「{rest}」。")
            if r[1].get("kind") != "ball":
                raise BattleError(f"❌ {r[1]['zh']}不是精灵球。")
            key = r[0]
        if trainer.count(key) <= 0 and int((battle.bag or {}).get(key, 0)) <= 0:
            entry = resolve_bag_item(key)
            raise BattleError(f"❌ 背包里没有 {entry[1]['zh'] if entry else key}。")
        return {"type": "catch", "item": key}

    if low.startswith(("switch", "换人", "替换")):
        rest = s.split(" ", 1)[1].strip() if " " in s else ""
        if not rest:
            raise BattleError("❌ 用法:`switch <队伍序号>`。")
        found = trainer.find(rest)
        if found is None:
            raise BattleError(f"❌ 队伍里找不到「{rest}」。")
        idx, _mon = found
        if idx == battle.player.active:
            raise BattleError("❌ 它已经在场上了。")
        if idx >= len(battle.player.party) or battle.player.party[idx].fainted:
            raise BattleError(f"❌ 序号 {idx + 1} 已失去战斗能力,无法上场。")
        return {"type": "switch", "index": idx}

    if low.startswith(("item ", "use ", "道具", "使用")):
        rest = s.split(" ", 1)[1].strip() if " " in s else ""
        r = resolve_bag_item(rest)
        if not r:
            raise BattleError(f"❌ 未收录道具「{rest}」。")
        key, entry = r
        if entry.get("kind") == "ball":
            raise BattleError(f'❌ {entry["zh"]}是精灵球,请用 `catch {entry["zh"]}`。')
        kind = str(entry.get("kind") or "")
        if kind in ("medicine", "status", "revive", "pp", "battle", "berry"):
            if trainer.count(key) <= 0 and int((battle.bag or {}).get(key, 0)) <= 0:
                raise BattleError(f'❌ 背包里没有 {entry["zh"]}。')
            return {"type": "item", "item": key}
        raise BattleError(f'❌ 对战中不能使用 {entry["zh"]}。')

    body = s
    if low.startswith("move "):
        body = s[5:].strip()
    if not body:
        raise BattleError("❌ 请输入要使用的招式。")
    mon = battle.player.mon
    if mon is None:
        raise BattleError("❌ 场上没有宝可梦。")
    resolved = dex.resolve_move(body)
    if resolved is None:
        # 允许用序号选招
        if body.isdigit():
            i = int(body) - 1
            if 0 <= i < len(mon.moves):
                return {"type": "move", "move": mon.moves[i], "tera": tera}
        raise BattleError(
            f"❌ 未找到招式「{body}」。可用:"
            + "、".join(growth.move_zh(m) for m in mon.moves)
        )
    key = resolved[0]
    if key not in mon.moves:
        raise BattleError(f"❌ {mon.display} 不会「{growth.move_zh(key)}」。")
    return {"type": "move", "move": key, "tera": tera}


# ── 回合推进 ─────────────────────────────────────────────────────
def take_turn(
    trainer: Trainer,
    raw: str,
    *,
    daytime: str | None = None,
    money_mult: float = 1.0,
    weather: str = "",
    day: int = 0,
) -> TurnResult:
    data = _load(trainer)
    battle = battle_from_dict(data["battle"])
    res = TurnResult()
    try:
        action = parse_action(raw, trainer, battle)
    except BattleError as e:
        res.error = str(e)
        return res

    if weather and weather != battle.weather:
        battle.weather = weather
        battle.weather_turns = max(battle.weather_turns, 5)

    if action.get("type") == "move" and action.get("tera") and battle.player.tera_used:
        res.error = "⚠️ 本场对战已经太晶化过了。"
        return res
    lines = battle.step(action)
    res.lines = list(lines)
    _sync(trainer, battle)

    if battle.awaiting_switch and not battle.finished:
        res.awaiting_switch = True

    if not battle.finished:
        _store(trainer, {**data, "battle": battle.to_dict()}, lines)
        return res

    # ── 结算 ──
    res.finished = True
    meta = data.get("meta") or {}
    if battle.captured:
        res.outcome = "caught"
        if action.get("type") == "catch":
            res.item_key = str(action.get("item") or "")   # 捕获画面显示实际用的球
        _finish_catch(trainer, battle, res, day=day)
    elif battle.escaped:
        res.outcome = "escaped"
        res.lines.append("🏃 成功脱离了战斗。")
    elif battle.stalled:
        # 双方都打不动(数据里少数低级宝可梦只有变化招)时的兜底收场,
        # 既不算赢也不算输,不扣钱也不发奖励
        res.outcome = "stalled"
        res.lines.append("⌛ 战斗拖得太久,双方各自收起了宝可梦。")
    elif battle.winner == "player":
        res.outcome = "win"
        _finish_win(trainer, battle, meta, res, daytime=daytime, money_mult=money_mult, day=day)
    else:
        res.outcome = "loss" if action.get("type") != "forfeit" else "forfeit"
        _finish_loss(trainer, battle, meta, res, forfeit=action.get("type") == "forfeit")

    # 把"刚打完的这一场"写回 session(**带 finished=True**)。
    #
    # 这里有两个坑:
    # ① 直接清空(旧实现)→ `view()` 退到"待机"分支:我方变成队伍第一只(还是刚被
    #    `heal_party()` 治好的满血值)、敌方变成"？" —— 用户反馈的败北界面错乱。
    # ② 什么都不写 → session 里留着**回合开始前**那份旧快照(finished=False):
    #    画面同样错(我方还是满血),更糟的是 `in_battle()` 仍为 True,玩家每发一次
    #    `/对战` 就会用旧状态**重新结算一次败北**(非野生战反复扣一半金钱)。
    # 正确做法:写入已结束的终局状态。渲染层拿到真实的我方(已倒下)/敌方,
    # `in_battle()` 因为 finished=True 立即返回 False;画面发完再 `clear_finished()`。
    trainer.data["battle"] = {**data, "battle": battle.to_dict()}
    return res


def _finish_catch(trainer: Trainer, battle, res: TurnResult, *, day: int = 0) -> None:
    dex = get_dex()
    captured = dict(battle.captured or {})
    mon = dict_to_mon(captured)
    trainer.mark_caught(mon.species)
    entry = trainer.add_pokemon(mon, day=day)
    where = "队伍" if entry in trainer.party else "电脑"
    res.rewards.append(
        f"🎉 捕获成功!{dex.species.get(mon.species, {}).get('zh', mon.species)} "
        f"Lv{mon.level} 已加入你的{where}。"
    )
    res.rewards.append(f"📖 图鉴已记录:{len(trainer.data['dex_caught'])} 种。")
    trainer.data["steps"] = int(trainer.data.get("steps", 0)) + 30


def _finish_win(
    trainer: Trainer,
    battle,
    meta: dict,
    res: TurnResult,
    *,
    daytime: str | None,
    money_mult: float,
    day: int,
) -> None:
    dex = get_dex()
    kind = str(meta.get("kind") or "wild")
    enemy = list(battle.enemy.party)
    total_exp = sum(
        dex.exp_yield(m.species, m.level, trainer=(kind != "wild")) for m in enemy
    )
    participants = [i for i, m in enumerate(battle.player.party) if not m.fainted]
    if not participants:
        participants = [0]
    share = max(1, int(total_exp / len(participants)))
    for i in participants:
        cur = dict_to_mon(trainer.party[i])
        _lv_before = int(cur.level)
        g = growth.gain_exp(
            cur, share, daytime=daytime,
            party=[str(m.get("species") or "") for m in trainer.party],
        )
        cur.friendship = min(255, cur.friendship + 2)
        trainer.party[i] = mon_to_dict(cur, trainer.party[i])
        res.growth.append(
            f"{cur.display}: +{share} EXP"
            + (f" → Lv{cur.level}" if g.levels_gained else "")
        )
        # 每条都**自带宝可梦名字**:结算卡空间有限会截断条目,只靠"上一行是谁"
        # 来推断归属会把"学会了「缩入壳中」"读到下一只头上(实测踩过)。
        for mv in g.learned:
            res.growth.append(f"　└ {cur.display} 学会了「{growth.move_zh(mv)}」!")
        for mv in g.pending:
            res.growth.append(
                f"　└ {cur.display} 想学「{growth.move_brief(mv)}」"
                "(招式已满,用 /学招 替换)"
            )
        if g.pending:
            trainer.party[i]["pending"] = list(g.pending)
        res.levels_gained += int(g.levels_gained or 0)
        res.growth_detail.append(
            {
                "index": i,
                "name": cur.display,
                "levels": int(g.levels_gained or 0),
                "exp": int(share),
                "from_level": _lv_before,
                "to_level": int(cur.level),
                "learned": list(g.learned),
                "pending": list(g.pending),
                "evolved_from": str(g.evolved_from or ""),
                "evolved_to": str(g.evolved_to or ""),
            }
        )
        if g.evolved_to:
            res.evolved.append(str(g.evolved_to))
            res.growth.append(
                f"　└ ✨ 进化了!{growth.species_zh(g.evolved_from)} → "
                f"{growth.species_zh(g.evolved_to)}"
            )
    res.rewards.append(f"⭐ 获得经验 {total_exp} 点(每只 {share})。")

    # 金钱
    money = 0
    for m in enemy:
        money += int(m.level) * {"trainer": 12, "rocket": 15, "gym": 20}.get(kind, 0)
    if kind in ("elite",):
        money = sum(m.level for m in enemy) * 25
    elif kind == "champion":
        money = sum(m.level for m in enemy) * 30
    elif kind == "trial":
        money = sum(m.level for m in enemy) * 20
    if kind == "gym" and meta.get("gym"):
        money += int(meta["gym"].get("order", 1)) * 500
    money = int(money * float(money_mult or 1.0))
    if money:
        trainer.add_money(money)
        res.rewards.append(f"💰 获得赏金 {money}₽(现有 {trainer.money}₽)。")

    # 徽章 / 旗标
    region = trainer.region
    if kind in ("gym", "trial") and meta.get("gym"):
        gym = meta["gym"]
        if trainer.add_badge(region, int(gym.get("order", 1))):
            label = gym.get("badge") or gym.get("title") or "徽章"
            res.rewards.append(f"🏅 获得「{label}」!(共 {trainer.badge_count(region)} 枚)")
    if kind == "elite" and meta.get("elite"):
        e = meta["elite"]
        trainer.set_flag(f"elite:{region}:{int(e.get('order', 1))}")
        res.rewards.append(f"🏆 击败了四天王 {e.get('name', '')}!")
    if kind == "champion" and not trainer.flag(f"champion:{region}"):
        trainer.set_flag(f"champion:{region}", True)
        order = list(WorldMap().regions_with_data())
        if region in order:
            idx = order.index(region)
            if idx + 1 < len(order):
                nxt = order[idx + 1]
                unlocked = trainer.data.setdefault("unlocked_regions", [])
                if nxt not in unlocked:
                    unlocked.append(nxt)
                nodes = WorldMap().nodes(nxt)
                if nodes:
                    trainer.data["region"] = nxt
                    start = WorldMap().start_location(nxt)
                    if start:
                        trainer.data["location"] = start
                        visited = trainer.data.setdefault("visited", [])
                        if start not in visited:
                            visited.append(start)
                res.rewards.append(
                    f"👑 你成为了{WorldMap().region_zh(region)}冠军!"
                    f"新的地区已开放:{WorldMap().region_zh(nxt)}。"
                )
    if kind == "rocket":
        key = f"rocket:{meta.get('event_id') or meta.get('location') or ''}"
        trainer.set_flag(key, True)
        res.rewards.append("🚀 击退了火箭队!地区恢复了平静。")

    trainer.data["steps"] = int(trainer.data.get("steps", 0)) + 20
    trainer.data["stats"] = dict(trainer.data.get("stats") or {})
    trainer.data["stats"]["battles_won"] = (
        int(trainer.data["stats"].get("battles_won", 0)) + 1
    )


def _finish_loss(
    trainer: Trainer, battle, meta: dict, res: TurnResult, *, forfeit: bool
) -> None:
    world = WorldMap()
    region = trainer.region
    kind = str(meta.get("kind") or "wild")
    lost = 0
    if kind != "wild":
        lost = int(trainer.money * LOSS_MONEY_RATE)
        trainer.add_money(-lost)
    trainer.heal_party()
    # 送回**离失败地点最近**的城镇。
    # 旧实现取"visited 里最后一个 hub" = 最近一次**首次到访**的城镇:玩家折返
    # 回老城镇后失败,会被送回很远的城镇(实测站在 1 号道路被送到 6 跳外的深灰市,
    # 而真新镇只有 1 跳)。玩家预期是"附近的宝可梦中心"。
    hubs = [k for k in trainer.data.get("visited", []) if world.is_hub(k)]
    here = str(trainer.data.get("location") or "")
    back = world.nearest_hub(here, hubs) or (
        hubs[-1] if hubs else world.start_location(region)
    )
    if back:
        trainer.data["location"] = back
    res.rewards.append("😵 你的宝可梦全都失去了战斗能力……")
    if forfeit:
        res.rewards.append("🏳️ 你选择了认输。")
    if lost:
        res.rewards.append(f"💸 慌乱中丢掉了 {lost}₽。")
    res.rewards.append(
        f"🏥 你被送回了 {world.node_zh(back)}({world.region_zh(region)}),队伍已恢复。"
    )


# ── 展示 ─────────────────────────────────────────────────────────
def status_text(trainer: Trainer) -> str:
    data = trainer.data.get("battle")
    if not data:
        return "❌ 当前没有进行中的对战。"
    battle = battle_from_dict(data["battle"])
    dex = get_dex()
    kind = str(data.get("kind") or "wild")
    meta = data.get("meta") or {}
    title = meta.get("title") or BATTLE_KINDS.get(kind, kind)

    def side_block(mon: Pokemon | None, tag: str) -> list[str]:
        if mon is None:
            return [f"{tag}:—"]
        zt = "/".join(dex.type_label(t) for t in mon.types)
        st = _status_zh(mon.status)
        return [
            f"{tag}:{mon.display} Lv{mon.level} [{zt}]"
            f"{' ' + st if st else ''}",
            f"　HP {mon.cur_hp}/{mon.max_hp} "
            f"[{growth_move_bar(mon.cur_hp, mon.max_hp)}]",
        ]

    lines = [f"⚔️ 对战 —— {title}(第 {battle.turn + 1} 回合)"]
    lines += side_block(battle.enemy.mon, "👹 对手")
    lines += side_block(battle.player.mon, "🔵 我方")
    if battle.weather:
        lines.append(f"🌦️ 天气:{battle.weather}")
    if battle.terrain:
        lines.append(f"🌈 场地:{battle.terrain}")
    mon = battle.player.mon
    if mon:
        mv = []
        for i, m in enumerate(mon.moves, 1):
            pp = mon.pp.get(m, 0)
            mv.append(f"{i}.{growth.move_zh(m)}({pp})")
        lines.append("招式:" + " ".join(mv))
    if battle.awaiting_switch:
        lines.append("⚠️ 需要换人 → `switch <序号>`")
    tail = data.get("log") or []
    if tail:
        lines.append("── 上回合 ──")
        lines += [f"· {t}" for t in tail[-6:]]
    return "\n".join(lines)


def growth_move_bar(cur: int, total: int, width: int = 10) -> str:
    from .util import bar

    return bar(cur, total, width)


def _status_zh(status: str) -> str:
    return {
        "brn": "🔥灼伤",
        "par": "⚡麻痹",
        "psl": "☠️中毒",
        "psn": "☠️中毒",
        "tox": "☠️剧毒",
        "slp": "💤睡眠",
        "frz": "❄️冰冻",
    }.get(str(status or ""), "")


def team_status(trainer: Trainer) -> str:
    dex = get_dex()
    if not trainer.party:
        return "队伍是空的。"
    lines = []
    for i, p in enumerate(trainer.party, 1):
        mon = dict_to_mon(p)
        zt = "/".join(dex.type_label(t) for t in mon.types)
        nick = f"「{mon.nickname}」" if mon.nickname else ""
        lines.append(
            f"{i}. {mon.display}{nick} Lv{mon.level} [{zt}] "
            f"HP {mon.cur_hp}/{mon.max_hp} {_status_zh(mon.status)}"
        )
        lines.append(
            "　招式:" + " ".join(f"{growth.move_zh(m)}({mon.pp.get(m, 0)})" for m in mon.moves)
        )
    return "\n".join(lines)


# ── 供图片渲染使用的视图 ─────────────────────────────────────────
STATUS_CODES = ("brn", "par", "psn", "tox", "slp", "frz")


def _mon_view(mon: Pokemon | None, *, exp_pct: float = 0.0) -> dict:
    if mon is None:
        return {}
    dex = get_dex()
    return {
        "species": mon.species,
        "name": mon.display,
        "level": int(mon.level),
        "cur_hp": int(mon.cur_hp),
        "max_hp": int(max(1, mon.max_hp)),
        "status": str(mon.status or ""),
        "gender": str(mon.gender or ""),
        "types": [dex.type_label(t) for t in (mon.types or [])],
        "exp_pct": float(exp_pct),
    }


def exp_progress(mon: Pokemon) -> float:
    """当前等级内的经验进度(0-100),用于战斗界面经验条。"""
    dex = get_dex()
    growth = dex.growth_of(mon.species)
    lo = dex.exp_for_level(growth, mon.level)
    hi = dex.exp_for_level(growth, min(100, mon.level + 1))
    if hi <= lo:
        return 100.0
    return float(clamp(100.0 * (mon.exp - lo) / (hi - lo), 0.0, 100.0))


def view(trainer: Trainer) -> dict:
    """把当前对战(或待机状态)整理成渲染层需要的视图。"""
    data = session(trainer)
    party = [dict_to_mon(p) for p in trainer.party]
    me = party[0] if party else None
    out = {
        "my": _mon_view(me, exp_pct=exp_progress(me) if me else 0.0),
        "foe": {},
        "party": [
            {
                "species": m.species,
                "cur_hp": int(m.cur_hp),
                "max_hp": int(max(1, m.max_hp)),
            }
            for m in party
        ],
        "turn": 0,
        "weather": "",
        "terrain": "",
        "title": "",
        "kind": "",
    }
    if not data:
        return out
    battle = battle_from_dict(data["battle"])
    meta = data.get("meta") or {}
    out["my"] = _mon_view(battle.player.mon, exp_pct=exp_progress(battle.player.mon) if battle.player.mon else 0.0)
    out["foe"] = _mon_view(battle.enemy.mon)
    out["party"] = [
        {
            "species": m.species,
            "cur_hp": int(m.cur_hp),
            "max_hp": int(max(1, m.max_hp)),
        }
        for m in battle.player.party
    ]
    out["turn"] = int(battle.turn)
    out["weather"] = str(battle.weather or "")
    out["terrain"] = str(battle.terrain or "")
    out["title"] = str(meta.get("title") or "")
    out["kind"] = str(data.get("kind") or "")
    return out
