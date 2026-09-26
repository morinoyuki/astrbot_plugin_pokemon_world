"""每日刷新(凌晨 4 点):天气 → 世界事件 → 玩家个人事件。

生成流程刻意做成"两段式":
  1. 确定性部分(天气、兜底事件)永远可用,保证游戏不空转;
  2. LLM 部分(选题 + 文案)失败时静默降级,不阻塞任何玩家操作。
"""

from __future__ import annotations

from astrbot.api import logger

from . import events as EV
from .narrate import Narrator
from .util import game_day
from .world import REGION_ORDER, WorldMap
from .worldstate import WorldState


def relevant_regions(players: list, state: WorldState) -> list[str]:
    """参与本世界事件生成的地区(玩家已解锁的 + 下一个待解锁的)。"""
    world = WorldMap()
    unlocked: set[str] = set()
    for t in players:
        unlocked.add(t.region)
        unlocked.update(t.data.get("unlocked_regions") or [])
    ordered = [r for r in REGION_ORDER if r in unlocked and world.nodes(r)]
    if not ordered:
        ordered = [r for r in REGION_ORDER if world.nodes(r)][:1]
    # 追加下一个地区,制造"前方有事发生"的期待
    for r in REGION_ORDER:
        if r not in ordered and world.nodes(r):
            ordered.append(r)
            break
    return ordered


async def roll_day(
    *,
    scope: str,
    state: WorldState,
    players: list,
    day: int | None = None,
    narrator: Narrator | None = None,
) -> dict:
    """把世界推进到一个新的游戏日(幂等:同一天重复调用不会重复生成)。"""
    day = int(day or game_day())
    result = {"rolled": False, "world_events": [], "player_events": {}}
    # `<=` 而不是 `==`:除了同一天重复调用(幂等),还要挡住**系统时钟回拨**。
    # 回拨时 day 会变小,若照常滚动会把 until_day 已过的事件清掉、重新生成一天,
    # 且时钟恢复后会再滚一次;世界日是单调的,不该倒退。
    if day <= int(state.data.get("last_roll_day") or 0):
        return result

    state.data["day"] = day
    gone = state.expire(day)
    if gone:
        logger.debug("宝可梦世界 %s: 过期事件 %s 条", scope, len(gone))
    state.roll_weather(day)
    state.prune_player_events(day)

    regions = relevant_regions(players, state)

    # ── 世界事件 ──
    world_events: list[dict] = []
    if narrator and narrator.available():
        prompt = EV.build_world_prompt(state, day, [_brief(t) for t in players])
        prompt += f"\n本世界当前可发生事件的地区:{', '.join(regions)}"
        data = await narrator.json(EV.WORLD_SYSTEM_PROMPT, prompt, fallback="{}")
        raw_list = data.get("events") if isinstance(data, dict) else None
        if isinstance(raw_list, list):
            for raw in raw_list[: EV.MAX_WORLD_EVENTS]:
                try:
                    ev = EV.sanitize_world_event(raw, state, day)
                except Exception:  # 单条脏数据只丢弃这一条(json 里的 Infinity 等)
                    logger.debug("世界事件裁剪失败", exc_info=True)
                    ev = None
                if ev and ev.get("region") in regions:
                    world_events.append(ev)
    if not world_events:
        world_events = EV.fallback_world_events(state, day, regions=regions)
    for ev in world_events:
        state.add_event(ev)

    # ── 玩家个人事件 ──
    player_events: dict[str, dict] = {}
    for t in players:
        ev = None
        if narrator and narrator.available():
            data = await narrator.json(
                EV.PLAYER_SYSTEM_PROMPT, EV.build_player_prompt(t, day), fallback="{}"
            )
            try:
                ev = EV.sanitize_player_event(data, t, day)
            except Exception:  # 同上:个人事件也逐条降级
                logger.debug("个人事件裁剪失败", exc_info=True)
                ev = None
        if not ev:
            ev = EV.fallback_player_event(t, day)
        if ev:
            state.add_player_event(t.uid, ev)
            player_events[t.uid] = ev

    summary = " · ".join(EV.event_text(e) for e in world_events) or "平静的一天"
    state.add_day_log(f"世界:{summary}", day=day)
    state.data["last_roll_day"] = day
    result.update({"rolled": True, "world_events": world_events, "player_events": player_events})
    return result


def _brief(t) -> dict:
    return {
        "name": t.name,
        "region": t.region,
        "location": t.location,
        "party": len(t.party),
        "badges": t.badge_count(),
    }


def ensure_rolled_sync(state: WorldState, players: list, day: int | None = None) -> bool:
    """同步版的"日期是否已推进"检查(用于不需要 LLM 的快速路径)。"""
    day = int(day or game_day())
    # 与 roll_day 一致:世界日单调,回拨不算"新的一天"
    if day <= int(state.data.get("last_roll_day") or 0):
        return False
    state.data["day"] = day
    state.expire(day)
    state.roll_weather(day)
    state.prune_player_events(day)
    if not state.active_events():
        for ev in EV.fallback_world_events(state, day, regions=relevant_regions(players, state)):
            state.add_event(ev)
    state.data["last_roll_day"] = day
    return True


def deliver_player_events(trainer, state: WorldState) -> list[str]:
    """把玩家今日尚未领取的个人事件落到存档上,返回描述行。"""
    lines: list[str] = []
    for ev in state.player_events(trainer.uid, pending_only=True):
        head = f"📨 【{ev.get('title') or ev.get('kind')}】"
        desc = str(ev.get("desc") or "").strip()
        if desc:
            head += f" {desc}"
        applied = EV.apply_player_event(trainer, ev)
        lines.append(head)
        lines.extend(f"　{line}" for line in applied)
        state.mark_player_event_done(trainer.uid, ev.get("id") or "")
    return lines


def today_brief(state: WorldState, *, region: str = "", location: str = "") -> str:
    """给玩家看的今日简报。"""
    world = WorldMap()
    day = state.day
    weather = world.region_zh(region) + ":" + (
        {
            "": "晴朗",
            "sun": "大晴天",
            "rain": "下雨",
            "sand": "沙暴",
            "snow": "下雪",
        }.get(state.weather_for(region), "晴朗")
        if region
        else ""
    )
    lines = [f"📅 第 {day} 游戏日(04:00 刷新)"]
    if region:
        lines.append(f"🌦️ 当前地区天气:{weather}")
    if location:
        ev = state.event_at(location)
        if ev:
            lines.append(f"📍 本地事件:{EV.event_text(ev)}")
    act = state.active_events()
    if act:
        lines.append("🌍 世界动态:")
        lines += [f"　{EV.event_text(e)}" for e in act[:6]]
    else:
        lines.append("🌍 世界动态:风平浪静。")
    locks = state.data.get("locks") or {}
    if locks:
        names = [
            f"{world.node_zh(k)}(还有 {max(1, int(v) - int(state.day))} 天)"
            for k, v in list(locks.items())[:5]
        ]
        lines.append("🚧 封锁中:" + "、".join(names))
    return "\n".join(lines)
