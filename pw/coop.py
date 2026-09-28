"""合作双打:两位玩家组队,各自指挥自己场上的宝可梦(引擎侧的 2v2)。

设计要点:
· **一对搭档 = 一个会话**。邀请 5 分钟有效,开打后战斗会话保留 2 小时;
  和 PvP(`pw/pvp.py`)同一套“存世界状态里、同群共享”的做法。
· **各自的宝可梦归各自**:引擎里玩家一侧的队伍是“主办方队伍 + 搭档队伍”
  拼接起来的,`owners` 记住每只归谁 —— 补位/结算/写回存档都按这个切。
· **每回合各交一次行动**:两边都交齐(或有人超时)才推进一回合。
· 只管野外/训练家对战这类“ roam ”战斗;**道馆/大赛/联盟/主线一律单打**,
  由命令层保证(见 main.py 的 `/双打` 与 `_coop_*`)。

本模块不引引擎,只做状态与规则,便于测试。
"""

from __future__ import annotations

import time

BOX_KEY = "coop"          # 世界状态里的会话表
OFFER_TTL = 300.0         # 邀请 5 分钟
BATTLE_TTL = 7200.0       # 战斗会话 2 小时
TIMEOUT = 90.0            # 一回合等待上限(超时自动出招,不能让搭档干等)


def key_for(a: str, b: str) -> str:
    """一对搭档的会话 key(与顺序无关)。"""
    x, y = sorted((str(a), str(b)))
    return f"{x}|{y}"


def box(state) -> dict:
    """取出会话表并顺手清理过期项。"""
    table = state.data.setdefault(BOX_KEY, {})
    now = time.time()
    for key in list(table.keys()):
        row = table[key] or {}
        if row.get("battle"):
            if now - float(row.get("ts") or 0) > BATTLE_TTL:
                del table[key]
        elif now - float(row.get("ts") or 0) > OFFER_TTL:
            del table[key]
    return table


def offer(state, a: str, b: str, a_name: str = "", b_name: str = "") -> None:
    """a 邀请 b 组队。"""
    table = box(state)
    key = key_for(a, b)
    table[key] = {
        "key": key, "a": str(a), "b": str(b),
        "names": {str(a): a_name, str(b): b_name},
        "ts": time.time(), "battle": None, "actions": {}, "acted_at": {},
    }


def incoming(state, uid: str) -> list[dict]:
    """发给我的组队邀请。"""
    uid = str(uid)
    return [row for row in box(state).values()
            if not row.get("battle") and row.get("b") == uid and row.get("a") != uid]


def pair_for(state, uid: str) -> dict | None:
    """我当前的搭档会话(邀请/战斗中都算)。"""
    uid = str(uid)
    for row in box(state).values():
        if uid in (row.get("a"), row.get("b")):
            return row
    return None


def partner_of(row: dict, uid: str) -> str:
    return str(row.get("b") if str(uid) == str(row.get("a")) else row.get("a"))


def accept(state, row: dict) -> None:
    row["ts"] = time.time()
    row.setdefault("names", {})


def decline(state, row: dict) -> None:
    box(state).pop(row.get("key"), None)


def leave(state, uid: str) -> bool:
    """退出组队(战斗中不允许)。"""
    row = pair_for(state, uid)
    if row is None or row.get("battle"):
        return False
    box(state).pop(row.get("key"), None)
    return True


def start_battle(state, row: dict, *, kind: str, title: str,
                 host_len: int, host: str, ally: str) -> None:
    """登记一场合作双打(真正的 Battle 存在主办方存档里)。"""
    row["battle"] = {
        "kind": kind, "title": title, "host_len": int(host_len),
        "host": str(host), "ally": str(ally), "turn": 0,
    }
    row["actions"] = {}
    row["acted_at"] = {}
    row["ts"] = time.time()


def battle_of(row: dict | None) -> dict | None:
    return (row or {}).get("battle")


def set_action(row: dict, uid: str, action: dict) -> bool:
    """交行动;返回是否双方都交齐了。"""
    uid = str(uid)
    row.setdefault("actions", {})[uid] = action
    row.setdefault("acted_at", {})[uid] = time.time()
    a, b = str(row.get("a")), str(row.get("b"))
    return a in row["actions"] and b in row["actions"]


def pending(row: dict, uid: str) -> bool:
    return str(uid) in (row.get("actions") or {})


def timed_out(row: dict, uid: str, now: float | None = None) -> bool:
    """搭档是否已经等太久(该自动出招了)。"""
    acts = row.get("actions") or {}
    other = partner_of(row, uid)
    if other in acts:
        return False
    stamp = (row.get("acted_at") or {}).get(str(uid))
    if stamp is None:
        stamp = row.get("ts") or 0
    return (now if now is not None else time.time()) - float(stamp) > TIMEOUT


def clear(state, row: dict | None) -> None:
    if row:
        box(state).pop(row.get("key"), None)
