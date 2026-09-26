"""世界状态(按 scope 共享):游戏日 / 天气 / 每日事件 / 封锁 / 全局修正。

每个群(或私聊)拥有一个独立世界 —— 同一个群里所有玩家的世界事件共享,
玩家个人事件则按 uid 分开保存。
"""

from __future__ import annotations

import os

from .util import (
    clamp,
    ensure_dir,
    game_day,
    read_json,
    safe_name,
    stable_rng,
    write_json_atomic,
)
from .world import REGION_ORDER

WEATHERS = ["", "", "", "sun", "rain", "sand", "snow"]
WEATHER_ZH = {
    "": "晴朗",
    "sun": "大晴天",
    "rain": "下雨",
    "sand": "沙暴",
    "snow": "下雪",
}
DEFAULT_MODIFIERS = {
    "encounter_mult": 1.0,
    "rare_mult": 1.0,
    "money_mult": 1.0,
    "shop_discount": 1.0,
    "battle_weather": "",
}


class WorldState:
    """一个 scope 的世界状态(可变 dict 包装)。"""

    def __init__(self, data: dict, scope: str = ""):
        self.data = data
        self.scope = scope
        self._fixup()

    def _fixup(self) -> None:
        d = self.data
        d.setdefault("scope", self.scope)
        d.setdefault("day", game_day())
        d.setdefault("last_roll_day", 0)
        d.setdefault("weather", {})
        d.setdefault("events", [])
        d.setdefault("player_events", {})
        d.setdefault("locks", {})
        d.setdefault("modifiers", dict(DEFAULT_MODIFIERS))
        d.setdefault("npc_spawns", [])
        d.setdefault("day_log", [])
        d.setdefault("active_players", [])
        d.setdefault("last_seen", 0)

    # ── 基础 ──
    @property
    def day(self) -> int:
        return int(self.data.get("day") or game_day())

    @property
    def modifiers(self) -> dict:
        m = dict(DEFAULT_MODIFIERS)
        m.update(self.data.get("modifiers") or {})
        return m

    def weather_for(self, region: str) -> str:
        return str((self.data.get("weather") or {}).get(region) or "")

    def set_weather(self, region: str, weather: str) -> None:
        self.data.setdefault("weather", {})[region] = weather

    def roll_weather(self, day: int) -> None:
        """按 scope + 日 + 地区稳定随机天气(同一世界同一天恒定)。"""
        for region in REGION_ORDER:
            r = stable_rng("weather", self.scope, day, region)
            self.data.setdefault("weather", {})[region] = r.choice(WEATHERS)

    # ── 事件 ──
    def active_events(self, *, region: str = "", kind: str = "") -> list[dict]:
        out = []
        for e in self.data.get("events") or []:
            if int(e.get("until_day", 0)) < self.day:
                continue
            if region and e.get("region") and e["region"] != region:
                continue
            if kind and e.get("kind") != kind:
                continue
            out.append(e)
        return out

    def event_at(self, location: str) -> dict:
        """当前地点上正在发生的事件(取最近一个)。"""
        if not location:
            return {}
        for e in self.active_events():
            if e.get("location") == location:
                return e
        return {}

    def add_event(self, event: dict) -> None:
        ev = dict(event)
        ev.setdefault("id", f"e{len(self.data.get('events') or []) + 1}")
        ev.setdefault("created_day", self.day)
        ev.setdefault("until_day", self.day)
        self.data.setdefault("events", []).append(ev)
        loc = ev.get("location")
        if loc and ev.get("kind") in ("block", "lock", "rocket"):
            self.data.setdefault("locks", {})[loc] = int(ev["until_day"])
        self._recompute_modifiers()

    def expire(self, day: int) -> list[dict]:
        """清理过期事件,返回被清理的事件列表。"""
        keep, gone = [], []
        for e in self.data.get("events") or []:
            (keep if int(e.get("until_day", 0)) >= day else gone).append(e)
        self.data["events"] = keep
        locks = {}
        for loc, until in (self.data.get("locks") or {}).items():
            if int(until) >= day:
                locks[loc] = int(until)
        self.data["locks"] = locks
        self._recompute_modifiers()
        return gone

    def _recompute_modifiers(self) -> None:
        """把当天所有生效事件折算成一组世界修正值。

        **倍数类效果必须封顶**:单个事件已经被裁剪到各自的区间内,但多场事件
        同时生效时原来是**连乘**的 —— 9 场满增益叠起来 money_mult 高达 19683 倍
        (1000₽ 的战斗奖励变成 1968 万₽),rare_mult 更是能到 195 万倍。
        所以这里对最终结果再做一次区间钳制:总量也必须落在声明范围内。
        """
        from .events import EFFECT_RANGES  # 延迟导入,避免模块循环依赖

        m = dict(DEFAULT_MODIFIERS)
        for e in self.active_events():
            eff = e.get("effects") or {}
            for k in ("encounter_mult", "rare_mult", "money_mult"):
                if k in eff:
                    m[k] = float(m.get(k, 1.0)) * float(eff[k])
            if "shop_discount" in eff:
                m["shop_discount"] = min(
                    float(m.get("shop_discount", 1.0)), float(eff["shop_discount"])
                )
            if eff.get("battle_weather"):
                m["battle_weather"] = str(eff["battle_weather"])
        for k, rng in EFFECT_RANGES.items():
            if rng and k in m:
                m[k] = float(clamp(float(m[k]), rng[0], rng[1]))
        self.data["modifiers"] = m

    def locked_until(self, location: str) -> int | None:
        v = (self.data.get("locks") or {}).get(location)
        return int(v) if v else None

    # ── 玩家事件 ──
    def player_events(self, uid: str, *, pending_only: bool = True) -> list[dict]:
        lst = (self.data.get("player_events") or {}).get(uid) or []
        if not pending_only:
            return list(lst)
        return [e for e in lst if not e.get("delivered")]

    def add_player_event(self, uid: str, event: dict) -> None:
        ev = dict(event)
        ev.setdefault("id", f"p{len((self.data.get('player_events') or {}).get(uid) or []) + 1}")
        ev.setdefault("day", self.day)
        ev.setdefault("delivered", False)
        self.data.setdefault("player_events", {}).setdefault(uid, []).append(ev)

    def mark_player_event_done(self, uid: str, event_id: str) -> None:
        for e in (self.data.get("player_events") or {}).get(uid) or []:
            if e.get("id") == event_id:
                e["delivered"] = True

    def prune_player_events(self, day: int, keep_days: int = 3) -> None:
        for uid, lst in (self.data.get("player_events") or {}).items():
            self.data["player_events"][uid] = [
                e for e in lst if int(e.get("day", 0)) >= day - keep_days
            ]

    # ── 记录 ──
    def add_day_log(self, text: str, *, day: int | None = None) -> None:
        logs = self.data.setdefault("day_log", [])
        logs.append({"day": int(day or self.day), "text": str(text)})
        del logs[:-14]

    def recent_log(self, n: int = 5) -> list[dict]:
        return list(self.data.get("day_log") or [])[-n:]

    def touch_player(self, uid: str, *, ts: int = 0) -> None:
        lst = self.data.setdefault("active_players", [])
        if uid and uid not in lst:
            lst.append(uid)
        del lst[:-200]
        self.data["last_seen"] = int(ts or 0) or self.data.get("last_seen", 0)


class WorldStore:
    """世界状态存档:每个 scope 一个 world.json。"""

    def __init__(self, data_dir: str):
        self._root = ensure_dir(os.path.join(data_dir, "pokemon_world"))

    def _path(self, scope: str) -> str:
        return os.path.join(self._root, safe_name(scope), "_world.json")

    def load(self, scope: str) -> dict:
        return read_json(self._path(scope)) or {}

    def save(self, scope: str, data: dict) -> None:
        write_json_atomic(self._path(scope), data)

    def delete(self, scope: str) -> bool:
        p = self._path(scope)
        if os.path.exists(p):
            os.remove(p)
            return True
        return False

    def list_scopes(self) -> list[str]:
        if not os.path.isdir(self._root):
            return []
        return sorted(
            name
            for name in os.listdir(self._root)
            if os.path.exists(os.path.join(self._root, name, "_world.json"))
        )
