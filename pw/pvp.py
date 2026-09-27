"""玩家对玩家(PvP)回合制对战。

两名玩家各自出招,**双方都交了才结算一回合** —— 引擎本来就支持外部传入
敌方行动(`Battle.step(player_action, enemy_action)`),所以这里只做三件事:

1. 会话:谁挑战谁、赌注多少、当前战况(存在**世界状态**里,同群的两人共享,
   因此同一群可以同时进行多场对战);
2. 收集行动:每侧一份,齐了就推进一回合;
3. 超时兜底:一方迟迟不出招(默认 90 秒)就自动出招,不让对战卡死。

结算(赌注、写回两边存档、发战报)由指令层负责 —— 这里保持纯函数,方便测试。
"""

from __future__ import annotations

import time

from .battle import BattleError, battle_from_dict, parse_action
from .dex import get_dex
from .engine import Pokemon, start_battle
from .growth import move_zh

# 一个群最多同时挂几场对战(超出的按最旧清理)
ROOM = 6
# 赌注上限
WAGER_MAX = 20000
# 一方多久没出招就自动出招(秒)
TIMEOUT = 90.0
# 会话存放位置(世界状态里的键)
BOX_KEY = "pvp"


def key_for(a: str, b: str) -> str:
    """一对玩家的会话键(与顺序无关,同一对只可能有一场)。"""
    x, y = sorted((str(a), str(b)))
    return f"{x}|{y}"


def box(state) -> dict:
    """取会话表,顺手清掉过期/超量的会话。"""
    data = state.data
    raw = data.get(BOX_KEY)
    if not isinstance(raw, dict):
        raw = {}
        data[BOX_KEY] = raw
    now = time.time()
    for k, row in list(raw.items()):
        if not isinstance(row, dict):
            raw.pop(k, None)
            continue
        stage = str(row.get("stage") or "")
        age = now - float(row.get("at") or now)
        # 报价 5 分钟没人理、对战 2 小时没动静 → 清掉
        if (stage == "offer" and age > 300) or (stage == "battle" and age > 7200):
            raw.pop(k, None)
    if len(raw) > ROOM:
        for k in sorted(raw, key=lambda x: float((raw[x] or {}).get("at") or 0))[
            : len(raw) - ROOM
        ]:
            raw.pop(k, None)
    return raw


def incoming(state, uid: str) -> list[dict]:
    """别人向我发起的报价(新的在前)。"""
    rows = [
        row
        for row in box(state).values()
        if isinstance(row, dict)
        and row.get("stage") == "offer"
        and str(row.get("to")) == str(uid)
    ]
    return sorted(rows, key=lambda r: float(r.get("at") or 0), reverse=True)


def active_for(state, uid: str) -> dict | None:
    """我正在进行的对战会话(没有就返回 None)。"""
    for row in box(state).values():
        if not isinstance(row, dict) or row.get("stage") != "battle":
            continue
        if uid in (str(row.get("from")), str(row.get("to"))):
            return row
    return None


def offer(
    state,
    *,
    challenger: str,
    challenger_name: str,
    target: str,
    wager: int = 0,
    day: int = 0,
) -> dict:
    """发起挑战(不立刻开打,等对方接受)。"""
    if str(challenger) == str(target):
        raise BattleError("❌ 不能和自己对战。")
    rows = box(state)
    k = key_for(challenger, target)
    old = rows.get(k)
    if isinstance(old, dict) and old.get("stage") == "battle":
        raise BattleError("❌ 你们俩还有一场没打完。")
    wager = max(0, min(int(wager or 0), WAGER_MAX))
    row = {
        "stage": "offer",
        "from": str(challenger),
        "from_name": str(challenger_name or ""),
        "to": str(target),
        "wager": wager,
        "day": int(day or 0),
        "at": time.time(),
    }
    rows[k] = row
    return row


def begin(state, row: dict, a_party: list[Pokemon], b_party: list[Pokemon],
          *, seed: int = 0, weather: str = "") -> list[str]:
    """两边都同意了 —— 建一场真正对战(双方队伍都进引擎)。"""
    if not a_party or not b_party:
        raise BattleError("❌ 有一方队伍是空的。")
    battle = start_battle(
        a_party, b_party,
        weather=weather,
        seed=int(seed) or int(time.time()),
        wild=False,                     # 不是野生:不能投球、不能逃跑
        bag={},                         # 各自的道具用各自 trainer 的背包
    )
    log = battle.start()
    row.update({
        "stage": "battle",
        "battle": battle.to_dict(),
        "turn": 0,
        "actions": {},
        "acted_at": {},
        "log": list(log)[-12:],
        "at": time.time(),
    })
    return log


def side_names(row: dict) -> tuple[str, str]:
    return str(row.get("from")), str(row.get("to"))


def battle_of(row: dict):
    return battle_from_dict(row["battle"])


def store(row: dict, battle) -> None:
    row["battle"] = battle.to_dict()
    row["at"] = time.time()


class SideView:
    """把 `Battle` 的某一侧伪装成"玩家侧",以便**复用** `parse_action` 的校验。

    `parse_action` 读 `battle.player.active` / `battle.wild` / `battle.bag`;
    PvP 里第二个人是 enemy 侧,所以要换一层皮 —— 这样两个人的行动校验
    走的是同一段代码,不会出现"A 能换人 B 不能"这种漂移。
    """

    def __init__(self, battle, first: bool, bag: dict | None = None):
        self.player = battle.player if first else battle.enemy
        self.wild = False                 # 玩家对战:不能投球也不能逃跑
        self.bag = dict(bag or {})


def parse_for(row: dict, uid: str, trainer, raw: str) -> dict:
    """解析某一侧的行动(复用 `parse_action`;非法行动抛 BattleError)。"""
    battle = battle_of(row)
    first = str(uid) == str(row.get("from"))
    view = SideView(battle, first,
                    bag=(getattr(trainer, "data", {}) or {}).get("bag") or {})
    return parse_action(raw, trainer, view)


def submit(row: dict, uid: str, action: dict) -> bool:
    """记下某侧的行动;返回"双方都齐了"。"""
    acts = row.setdefault("actions", {})
    acts[str(uid)] = dict(action)
    row.setdefault("acted_at", {})[str(uid)] = time.time()
    return len(acts) >= 2


def pending_side(row: dict) -> str:
    """还没出招的那一侧(都齐了返回空串)。"""
    a, b = side_names(row)
    acts = row.get("actions") or {}
    if a not in acts:
        return a
    if b not in acts:
        return b
    return ""


def timeout_side(row: dict, now: float | None = None) -> str:
    """超时未出招的那一侧(没有则返回空串)。"""
    now = time.time() if now is None else float(now)
    acts = row.get("actions") or {}
    times = row.get("acted_at") or {}
    a, b = side_names(row)
    for uid in (a, b):
        if uid in acts:
            continue
        other = b if uid == a else a
        # 注意:0 是合法时间戳(测试会把时间抹成 0),所以不能用 `or` 兜底
        stamp = times.get(other)
        if stamp is None:
            stamp = row.get("at")
        stamp = now if stamp is None else float(stamp)
        if now - stamp >= TIMEOUT:
            return uid
    return ""


def auto_action(battle, first: bool) -> dict:
    """超时自动出招:能打就随便打一招,要换人就换第一只还能打的。"""
    side = battle.player if first else battle.enemy
    if side.mon is None or side.mon.fainted:
        for i, mon in enumerate(side.party):
            if not mon.fainted:
                return {"type": "switch", "index": i}
        return {"type": "forfeit"}
    if battle.awaiting_switch and side is battle.player:
        for i, mon in enumerate(side.party):
            if not mon.fainted and i != side.active:
                return {"type": "switch", "index": i}
    dex = get_dex()
    moves = list(side.mon.moves or [])
    usable = [m for m in moves if int(side.mon.pp.get(m, 0) or 0) > 0]
    pool = usable or moves
    if not pool:
        return {"type": "move", "move": "struggle"}
    # 优先挑有威力的招(变化招打不出结果会让对战拖很久)
    power = [m for m in pool if int((dex.moves.get(m) or {}).get("basePower") or 0) > 0]
    pick = (power or pool)[0]
    return {"type": "move", "move": pick}


def advance(row: dict) -> dict:
    """双方行动齐了 —— 推进一回合,返回结果摘要。"""
    battle = battle_of(row)
    a, b = side_names(row)
    acts = row.get("actions") or {}
    if a not in acts or b not in acts:
        raise BattleError("❌ 还没等到双方的行动。")
    act_a = dict(acts[a])
    act_b = dict(acts[b])
    # 引擎里 player 侧固定是挑战者(先手顺序由速度决定,不必交换)
    first_is_a = str(row.get("from")) == a
    pa, pb = (act_a, act_b) if first_is_a else (act_b, act_a)
    lines = battle.step(pa, pb)
    row["actions"] = {}
    row["turn"] = int(row.get("turn") or 0) + 1
    store(row, battle)
    row["log"] = list(lines)[-12:]
    return {
        "lines": list(lines),
        "finished": bool(battle.finished),
        "winner": str(battle.winner or ""),
        "forfeited": bool(
            pa.get("type") == "forfeit" or pb.get("type") == "forfeit"
        ),
        "awaiting_switch": bool(battle.awaiting_switch and not battle.finished),
    }


def winner_uid(row: dict, winner: str) -> str:
    """引擎的 player/enemy 胜者 → 玩家 uid。"""
    if winner == "player":
        return str(row.get("from"))
    if winner == "enemy":
        return str(row.get("to"))
    return ""


def move_list(battle, first: bool, *, limit: int = 4) -> str:
    """给某一侧看的"我能出什么招"。"""
    side = battle.player if first else battle.enemy
    mon = side.mon
    if mon is None:
        return ""
    out = [
        f"{i}.{move_zh(m)}({mon.pp.get(m, 0)})" for i, m in enumerate(mon.moves, 1)
    ]
    return " ".join(out[:limit])
