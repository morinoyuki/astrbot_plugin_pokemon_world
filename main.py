"""宝可梦世界 AstrBot 插件 —— 强规则文字冒险。

设计要点
========
1. **内核与文字分离**:伤害/命中/经验/进化/捕获/金钱全部由 `pw/` 内的确定性
   内核结算,LLM 只负责把"既成事实"写成叙事(不得改动任何数值)。
2. **一切通过指令驱动**:玩家用 `/前往 /探索 /对战 /捕捉 ...` 操作,状态落盘,
   可长期游玩;LLM 不可用时游戏依然完整可玩。
3. **每天凌晨 4 点刷新世界**:天气、世界事件(火箭队占据某地等)、个人事件
   由 LLM 按严格 JSON 生成,数值经过裁剪,失败自动回退本地事件。
"""

from __future__ import annotations

import asyncio
import contextlib
import glob
import os
import tempfile
import time
from datetime import datetime
from uuid import uuid4

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star
from astrbot.core.star.star_tools import StarTools

from .prompts import (
    HELP_TEXT,
    NARRATE_EVENT_SYSTEM,
    NARRATE_SCENE_SYSTEM,
    WELCOME_TEMPLATE,
    narration_facts,
)
from .pw import battle as B
from .pw import daily as D
from .pw import events as EV
from .pw import growth, legendary, npc, story
from .pw import quests as QT
from .pw import ui_info as UII
from .pw import ui_menu as UIM
from .pw import ui_quest as UIQ
from .pw import ui_render as UI
from .pw.dex import get_dex
from .pw.items import BAG_ITEMS
from .pw.narrate import Narrator
from .pw.player import Trainer, TrainerStore, new_trainer
from .pw.util import (
    bar,
    clamp,
    coerce_bool,
    coerce_int,
    fmt_money,
    game_day,
    game_day_str,
    hash_int,
    now_ts,
    stable_rng,
)
from .pw.world import (
    EVO_STONE_STOCK,
    FLY_COST,
    WorldMap,
    item_price,
)
from .pw.worldstate import WorldState, WorldStore

DEFAULT_STARTERS = ["新叶喵", "呆火鳄", "润水鸭", "皮卡丘", "伊布", "小火龙", "杰尼龟", "妙蛙种子"]
EXPLORE_ITEM_POOL = ["potion", "poke-ball", "antidote", "oran-berry", "super-potion"]


class PokemonWorldPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        self.data_dir = StarTools.get_data_dir()
        self.trainers = TrainerStore(self.data_dir)
        self.worlds = WorldStore(self.data_dir)
        self._locks: dict[str, asyncio.Lock] = {}
        self._scheduler_task: asyncio.Task | None = None
        self._last_notified_day = 0

    async def initialize(self):
        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(self._scheduler())
        logger.info(
            "宝可梦世界已加载:%d 地区 / %d 地点 / %d 缩略图",
            len(WorldMap().regions_with_data()),
            len(WorldMap()._index),
            _sprite_count(),
        )

    async def terminate(self):
        # 取消后台调度并**等它真正退出**:只 cancel 不 await 会在重载插件时
        # 留下 "Task was destroyed but it is pending" 的悬挂任务。
        task, self._scheduler_task = self._scheduler_task, None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # ══════════════════════════════════════════════════════════════
    # 基础设施
    # ══════════════════════════════════════════════════════════════
    def _cfg(self, key: str, default=None):
        if self.config is None:
            return default
        try:
            val = self.config.get(key, default)
        except (AttributeError, TypeError):
            return default
        return val if val is not None else default

    def _cfg_bool(self, key: str, default: bool = False) -> bool:
        """读布尔配置。

        不能直接 `if self._cfg(key, True):` —— 配置来自 YAML/WebUI,可能是字符串,
        而**非空字符串恒为真**,写成 "false" 依然会被当成开启(开关"失效")。
        """
        return coerce_bool(self._cfg(key, default), default)

    def _scope(self, event: AstrMessageEvent) -> str:
        gid = str(event.get_group_id() or "")
        if gid:
            return f"g{gid}"
        return f"u{event.get_sender_id()}"

    def _uid(self, event: AstrMessageEvent) -> str:
        return str(event.get_sender_id() or "unknown")

    def _lock(self, scope: str) -> asyncio.Lock:
        lock = self._locks.get(scope)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[scope] = lock
        return lock

    def _load(self, event: AstrMessageEvent) -> Trainer | None:
        scope, uid = self._scope(event), self._uid(event)
        data = self.trainers.load(scope, uid)
        if not data:
            return None
        return Trainer(data, uid=uid, scope=scope)

    def _require(self, event: AstrMessageEvent) -> tuple[Trainer | None, str]:
        t = self._load(event)
        if t is None:
            return None, "❌ 你还没有开始旅程。发送 `/开始 名字 御三家` 成为训练家吧!"
        return t, ""

    def _save(self, t: Trainer) -> None:
        self.trainers.save(t.scope, t.uid, t.data)

    def _state(self, scope: str) -> WorldState:
        return WorldState(self.worlds.load(scope), scope)

    def _save_state(self, state: WorldState) -> None:
        self.worlds.save(state.scope, state.data)

    def _players(self, scope: str, *, current: Trainer | None = None) -> list[Trainer]:
        out: list[Trainer] = []
        for uid in self.trainers.list_players(scope):
            if current is not None and uid == current.uid:
                out.append(current)
                continue
            data = self.trainers.load(scope, uid)
            if data:
                out.append(Trainer(data, uid=uid, scope=scope))
        return out

    async def _llm(self, system: str, user: str) -> str:
        pid = str(self._cfg("provider_id", "") or "").strip()
        if not pid:
            raise RuntimeError("未配置 provider_id")
        resp = await self.context.llm_generate(
            chat_provider_id=pid,
            system_prompt=system,
            contexts=[],
            prompt=user,
        )
        return (getattr(resp, "completion_text", "") or "").strip()

    def _narrator(self) -> Narrator:
        if not self._cfg_bool("narrate_enable", True):
            return Narrator(None)
        if not str(self._cfg("provider_id", "") or "").strip():
            return Narrator(None)
        return Narrator(self._llm)

    async def _narrate(self, title: str, facts: list[str], fallback: str, *, extra: str = "") -> str:
        nar = self._narrator()
        if not nar.available():
            return fallback
        rep = await nar.say(
            NARRATE_SCENE_SYSTEM,
            narration_facts(title, facts, extra),
            fallback=fallback,
        )
        return rep.text or fallback

    # ── 每日刷新 ──
    async def _ensure_day(self, event: AstrMessageEvent, trainer: Trainer) -> list[str]:
        """确保世界已推进到今天;返回需要展示的今日事件行。"""
        scope = trainer.scope
        state = self._state(scope)
        state.touch_player(trainer.uid, ts=now_ts())
        state.data["umo"] = event.unified_msg_origin
        lines: list[str] = []
        day = game_day()
        res = await D.roll_day(
            scope=scope,
            state=state,
            players=self._players(scope, current=trainer),
            day=day,
            narrator=self._narrator() if self._cfg_bool("event_enable", True) else Narrator(None),
        )
        if self._cfg_bool("quest_enable", True):
            try:
                new_q = await QT.roll_daily_async(
                    trainer, day,
                    self._narrator() if self._cfg_bool("quest_llm", True) else None,
                )
                if new_q:
                    self._save(trainer)
                    lines.append(
                        "📋 新的委托:" + "、".join(
                            f"{q.get('giver')}「{q.get('title')}」" for q in new_q
                        )
                        + "(用 `/任务` 查看)"
                    )
            except Exception:  # 委托生成失败不影响其它每日流程
                logger.exception("委托生成失败")
        if res["rolled"]:
            wl = [
                f"🌍 世界事件:{EV.event_text(e)}"
                for e in res["world_events"]
            ]
            if wl and self._cfg_bool("announce_events", True):
                lines.append(f"📅 第 {day} 天({game_day_str(day)})开始了。")
                lines += wl
        pe = D.deliver_player_events(trainer, state)
        if pe:
            lines.append("📨 今日个人事件:")
            lines += pe
        # 主线:达标即完成的章节自动推进(boa需战斗,league需冠军)
        for st in story.progress(trainer, world=WorldMap(), day=day):
            lines.append(f"📜 主线推进:{st['title']} —— {st['desc']}")
        self._save_state(state)
        self._save(trainer)
        return lines

    # ══════════════════════════════════════════════════════════════
    # 指令
    # ══════════════════════════════════════════════════════════════
    @filter.command("开始", alias={"start", "成为训练家"})
    async def cmd_start(self, event: AstrMessageEvent):
        """/开始 [名字] [御三家] —— 创建训练家,踏上旅程"""
        args = self._args(event, ("开始", "start", "成为训练家"))
        scope, uid = self._scope(event), self._uid(event)
        async with self._lock(scope):
            if self.trainers.exists(scope, uid):
                yield event.plain_result(
                    "你已经开始过旅程了。查看 `/状态`,或管理员用 `/重置世界` 重新开始。"
                )
                return
            tokens = [t for t in args.split() if t]
            name = ""
            starter = ""
            for tok in tokens:
                if get_dex().resolve_species(tok):
                    starter = tok
                elif not name:
                    name = tok
            starters = self._cfg("starter_choices", "") or ""
            pool = [s.strip() for s in str(starters).split(",") if s.strip()] or DEFAULT_STARTERS
            if not starter:
                yield event.plain_result(self._starter_menu(pool, name))
                return
            name = name or f"训练家{uid[-4:]}"
            starter_key = starter
            t = new_trainer(
                uid, scope, name, starter=starter_key, day=game_day(), now=now_ts()
            )
            self._save(t)
            world = WorldMap()
            mon = t.party[0] if t.party else {}
            starter_line = (
                f"🐾 初始伙伴:{mon.get('nickname') or _sp_zh(mon.get('species'))} "
                f"Lv{mon.get('level')}"
                if mon
                else "🐾 初始伙伴:无(可去野外收服第一只)"
            )
            yield event.plain_result(
                WELCOME_TEMPLATE.format(
                    name=t.name,
                    place=world.where_am_i(t),
                    money=fmt_money(t.money),
                    starter_line=starter_line,
                )
            )
            yield event.plain_result(HELP_TEXT)

    @filter.command("状态", alias={"status", "训练家", "档案"})
    async def cmd_status(self, event: AstrMessageEvent):
        """/状态 —— 训练家档案"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        async with self._lock(t.scope):
            today = await self._ensure_day(event, t)
        text = self._status_card(t, today)
        async for r in self._emit_ui(
            event, "card",
            lambda: UI.render_trainer_card(self._card_payload(t), scale=self._img_scale()),
            text=text,
        ):
            yield r

    @filter.command("队伍", alias={"team", "宝可梦队伍"})
    async def cmd_team(self, event: AstrMessageEvent):
        """/队伍 —— 查看队伍"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        if not t.party:
            yield event.plain_result("队伍是空的,去野外收服一只吧:`/探索`")
            return
        async with self._lock(t.scope):
            await self._ensure_day(event, t)
        text = B.team_status(t)
        text += f"\n\n库存:电脑 {len(t.box)} 只 · 徽章 {t.badge_count()} 枚 · {fmt_money(t.money)}"
        async for r in self._emit_ui(
            event, "party",
            lambda: UI.render_party(
                self._party_payload(t),
                title=f"{t.name} 的队伍",
                money=t.money,
                box_count=len(t.box),
                badges=t.badge_count(),
                sprites=self._cfg_bool("sprite_enable", True),
                scale=self._img_scale(),
            ),
            text=text,
        ):
            yield r

    @filter.command("背包", alias={"bag", "道具"})
    async def cmd_bag(self, event: AstrMessageEvent):
        """/背包 —— 查看背包"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        items = t.bag_items()
        if not items:
            yield event.plain_result("背包是空的。")
            return
        lines = [f"🎒 {t.name} 的背包({fmt_money(t.money)})"]
        for _k, entry, n in items:
            lines.append(f"· {entry['zh']} ×{n} —— {entry.get('desc', '')}")
        payload = self._bag_payload(t, self._args(event, ("背包", "bag", "道具")))
        async for r in self._emit_ui(
            event, "bag",
            lambda: UI.render_bag(
                payload["items"], money=t.money, active_pocket=payload["pocket"],
                selected=payload["selected"], scale=self._img_scale(),
            ),
            text="\n".join(lines),
        ):
            yield r

    @filter.command("地图", alias={"map", "地区", "地点"})
    async def cmd_map(self, event: AstrMessageEvent):
        """/地图 [地区] —— 查看地图与相邻地点"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        world = WorldMap()
        arg = self._args(event, ("地图", "map", "地区", "地点")).strip()
        region = world.resolve_region(arg) if arg else t.region
        if arg and not region:
            yield event.plain_result(f"❌ 没有「{arg}」这个地区。")
            return
        async with self._lock(t.scope):
            await self._ensure_day(event, t)
        text = self._map_text(t, region, own=region == t.region)
        nodes = [{**node, "key": key} for key, node in world.nodes(region).items()]
        async for r in self._emit_ui(
            event, "map",
            lambda: UIM.render_map(
                world.region_zh(region), nodes,
                current=t.location, visited=t.data.get("visited") or [],
                gyms=world.gyms(region), next_goal=self._next_goal(t),
                region_order=int(world.regions.get(region, {}).get("order") or 0),
                scale=self._img_scale(),
            ),
            text=text,
        ):
            yield r

    @filter.command("前往", alias={"go", "移动", "去"})
    async def cmd_go(self, event: AstrMessageEvent):
        """/前往 [飞行] <地点> —— 移动"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        arg = self._args(event, ("前往", "go", "移动", "去")).strip()
        by_fly = False
        for prefix in ("飞行", "fly", "飞", "坐飞机"):
            if arg.startswith(prefix):
                by_fly = True
                arg = arg[len(prefix) :].strip()
                break
        if not arg:
            yield event.plain_result("❌ 用法:`/前往 <地点>` 或 `/前往 飞行 <城镇>`")
            return
        if B.in_battle(t):
            yield event.plain_result("⚠️ 对战中不能移动。")
            return
        async with self._lock(t.scope):
            await self._ensure_day(event, t)
            world = WorldMap()
            key = world.find_location(arg, t.region) or world.find_location(arg)
            if not key:
                yield event.plain_result(f"❌ 找不到地点「{arg}」。用 `/地图` 看看能去哪。")
                return
            state = self._state(t.scope)
            ok, msg = world.travel_check(
                t, key, locked_until=state.data.get("locks") or {}, by_fly=by_fly
            )
            if not ok:
                yield event.plain_result(msg)
                return
            if by_fly and not t.spend_money(FLY_COST):
                yield event.plain_result("❌ 钱不够买机票。")
                return
            if world.region_of(key) != t.region:
                t.data["region"] = world.region_of(key)
            is_new = key not in (t.data.get("visited") or [])
            t.data["location"] = key
            visited = t.data.setdefault("visited", [])
            if is_new:
                visited.append(key)
            t.data["steps"] = int(t.data.get("steps", 0)) + 120
            qlines = QT.note(t, "travel", location=key, new=is_new)
            qlines += QT.note(t, "steps", steps=120)
            self._save(t)
            ev = state.event_at(key)
            tail = f"\n📍 这里正发生:{EV.event_text(ev)}" if ev else ""
            msg = (
                f"🚶 你来到了 {world.region_zh(t.region)}·{world.node_zh(key)}。"
                f"\n危险度:{world.tier_label(key)} · 可用服务:"
                f"{'、'.join(_service_zh(world.services(key))) or '无'}{tail}"
            )
            if qlines:
                msg += "\n" + "\n".join(qlines)
            yield event.plain_result(msg)

    @filter.command("探索", alias={"explore", "遭遇", "搜索"})
    async def cmd_explore(self, event: AstrMessageEvent):
        """/探索 —— 在当前地点探索"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        if B.in_battle(t):
            yield event.plain_result("⚠️ 先把眼前的战斗打完:`/对战 <招式>`")
            return
        async with self._lock(t.scope):
            today = await self._ensure_day(event, t)
            if t.all_fainted():
                for line in today:
                    yield event.plain_result(line)
                yield event.plain_result("❌ 队伍全部失去战斗能力,去 `/治疗` 吧。")
                return
            state = self._state(t.scope)
            world = WorldMap()
            loc = t.location
            rng = stable_rng("explore", t.uid, state.day, t.data.get("steps", 0))
            mods = state.modifiers
            ev = state.event_at(loc)
            notice = [*today]
            t.data["steps"] = int(t.data.get("steps", 0)) + 40
            notice += QT.note(t, "steps", steps=40)
            # 0) 神兽定点:条件满足且就在此地时,优先遭遇
            site = (legendary.ready(t, world=world, day=state.day) or [None])[0]
            if site:
                if t.all_fainted():
                    yield event.plain_result("❌ 队伍全部失去战斗能力,先 `/治疗`。")
                    return
                meta = legendary.legendary_meta(t, site)
                log = B.start(
                    t, meta["team"], kind="legend", wild=True, meta=meta,
                    weather=state.weather_for(t.region), day=state.day,
                )
                self._save(t)
                notice.append(f"🐉 传说的宝可梦出现了:{site['zh']} Lv{site['level']}!")
                async for r in self._emit_battle(
                    event, t, meta, log,
                    text="\n".join(notice) + "\n" + self._battle_hint(t),
                ):
                    yield r
                return
            # 1) 本地事件:火箭队 / 稀有宝可梦
            if ev and ev.get("kind") == "rocket":
                meta = npc.rocket_battle(t, ev)
                log = B.start(t, meta["team"], kind="rocket", meta=meta, day=state.day)
                self._save(t)
                notice.append(f"🚀 {EV.event_text(ev)}")
                async for r in self._emit_battle(
                    event, t, meta, log, text="\n".join(notice) + "\n" + self._battle_intro(meta, log)
                ):
                    yield r
                return
            # 2) 普通探索掷骰
            roll = rng.random()
            wild_p = 0.55 * float(mods.get("encounter_mult", 1.0))
            npc_p = 0.15
            if ev and ev.get("kind") == "swarm":
                wild_p += 0.2
            if roll < min(0.85, wild_p):
                env = _environment_of(world, loc)
                hit = B.roll_wild(t, rng=rng, environment=env)
                if not hit:
                    yield event.plain_result(
                        "\n".join([*notice, "这里似乎什么也没有发生……"])
                    )
                    return
                if ev and ev.get("kind") == "rare" and rng.random() < 0.35:
                    legend = npc.legendary_at(t, ev)
                    if legend:
                        hit = legend
                level = hit["level"]
                _entry = get_dex().species.get(str(hit.get("species")) or "") or {}
                meta = {
                    "kind": "wild",
                    "title": f"野生的{hit['zh']}",
                    "location": loc,
                    "region": t.region,
                    # 任务系统/结算卡需要的结构化信息。
                    # 注意:遭遇结果是 methods(复数,列表)而不是 method —— 早期按
                    # method 取值永远是空串,导致"钓鱼捕获"类委托无法推进。
                    "species": str(hit.get("species") or ""),
                    "types": list(hit.get("types") or _entry.get("types") or []),
                    "method": _primary_method(hit.get("methods")),
                    "new_species": not t.seen(str(hit.get("species")) or ""),
                }
                log = B.start(
                    t,
                    [{"species": hit["species"], "level": level}],
                    kind="wild",
                    wild=True,
                    meta=meta,
                    weather=state.weather_for(t.region),
                    day=state.day,
                )
                self._save(t)
                async for r in self._emit_battle(
                    event, t, meta, log,
                    text="\n".join(notice) + "\n" + self._battle_intro(meta, log)
                    + f"\n\n{self._battle_hint(t)}",
                ):
                    yield r
                return
            if roll < min(0.95, wild_p + npc_p):
                npcs = npc.route_trainers(t, loc, day=state.day)
                if npcs:
                    cand = npcs[0]
                    yield event.plain_result(
                        "\n".join(notice)
                        + f"\n👀 你看到一位训练家:{cand['name']}。"
                        f"\n用 `/训练家战` 发起挑战。"
                    )
                    return
            item = rng.choice(EXPLORE_ITEM_POOL)
            n = rng.randint(1, 2)
            t.add_item(item, n)
            self._save(t)
            zh = (BAG_ITEMS.get(item) or {}).get("zh", item)
            yield event.plain_result(
                "\n".join([*notice, f"🔍 你在草丛里发现了 {zh} ×{n}!"])
            )

    @filter.command("对战", alias={"battle", "出招", "move"})
    async def cmd_battle(self, event: AstrMessageEvent):
        """/对战 <行动> —— 出招 / 换人 / 道具 / 逃跑"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        arg = self._args(event, ("对战", "battle", "出招", "move")).strip()
        if not B.in_battle(t):
            yield event.plain_result("❌ 当前没有对战。用 `/探索` 或 `/道馆 挑战` 开战。")
            return
        if not arg:
            yield event.plain_result(B.status_text(t))
            return
        async with self._lock(t.scope):
            state = self._state(t.scope)
            meta = dict(B.session(t).get("meta") or {})
            _sess = B.session(t)
            res = B.take_turn(
                t,
                arg,
                daytime=B.daytime_of(),
                money_mult=float(state.modifiers.get("money_mult", 1.0)),
                weather=state.weather_for(t.region),
                day=state.day,
            )
            if not res.error and arg.strip().lower().startswith(("item", "道具")):
                # 只有真的用出去了才算(换人失败/道具无效时 take_turn 会给 error)
                _sess["used_item"] = True
            self._save(t)
            if res.error:
                yield event.plain_result(res.error + "\n\n" + B.status_text(t))
                return
            text = "\n".join(res.lines)
            if res.finished:
                text += "\n\n" + self._result_text(t, res)
                text = self._after_battle(t, meta, res, state.day) + text
                text = await self._narrate(
                    "对战结束", res.lines + res.rewards + res.growth, text
                )
                async for r in self._emit_battle(event, t, meta, res.lines, text=text):
                    yield r
                async for r in self._emit_result_cards(event, t, meta, res):
                    yield r
                return
            async for r in self._emit_battle(event, t, meta, res.lines, text=text):
                yield r
            if res.awaiting_switch:
                yield event.plain_result(
                    "⚠️ 你的宝可梦倒下了,必须换人:\n" + B.team_status(t)
                )
                return
            if self._cfg_bool("narrate_every_turn", False):
                yield event.plain_result(
                    await self._narrate("对战回合", res.lines, text)
                )
            yield event.plain_result(B.status_text(t))

    @filter.command("捕捉", alias={"catch", "投球"})
    async def cmd_catch(self, event: AstrMessageEvent):
        """/捕捉 <精灵球> —— 投球捕获"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        ball = self._args(event, ("捕捉", "catch", "投球")).strip() or "精灵球"
        event.message_str = f"/对战 catch {ball}"
        async for r in self.cmd_battle(event):
            yield r
        _ = t  # t 仅用于校验存档存在

    @filter.command("训练家战", alias={"npc战", "训练家对战"})
    async def cmd_npc(self, event: AstrMessageEvent):
        """/训练家战 [序号] —— 挑战本地点训练家"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        if B.in_battle(t):
            yield event.plain_result("⚠️ 先结束当前对战。")
            return
        async with self._lock(t.scope):
            state = self._state(t.scope)
            npcs = npc.route_trainers(t, t.location, day=state.day)
            if not npcs:
                yield event.plain_result("这附近没有训练家。")
                return
            idx = coerce_int(self._args(event, ("训练家战", "npc战", "训练家对战")).strip(), 1) or 1
            if not 1 <= idx <= len(npcs):
                yield event.plain_result(
                    "可选对手:\n"
                    + "\n".join(f"{i}. {n['name']}" for i, n in enumerate(npcs, 1))
                )
                return
            meta = npc.build_route_battle(t, npcs[idx - 1], day=state.day)
            log = B.start(
                t,
                meta["team"],
                kind="trainer",
                meta=meta,
                weather=state.weather_for(t.region),
                day=state.day,
            )
            self._save(t)
            yield event.plain_result(self._battle_intro(meta, log))

    @filter.command("道馆", alias={"gym", "馆主"})
    async def cmd_gym(self, event: AstrMessageEvent):
        """/道馆 [挑战] —— 查看/挑战道馆"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        world = WorldMap()
        gym = world.gym_at(t.region, t.location)
        sub = self._args(event, ("道馆", "gym", "馆主")).strip()
        if not gym:
            nxt = world.next_gym(t.region, t.badges)
            if nxt:
                yield event.plain_result(
                    f"这里没有道馆。下一个道馆在 "
                    f"{world.node_zh(nxt.get('location'))}({nxt.get('leader')},"
                    f"{_type_zh(nxt.get('type'))}属性)。"
                )
            else:
                yield event.plain_result(
                    f"你已经拿下{world.region_zh(t.region)}全部徽章!去 "
                    f"{world.node_zh(world.gateway(t.region))} 挑战联盟吧。"
                )
            return
        if not gym or not gym.get("team"):
            yield event.plain_result("⚠️ 该道馆数据缺失。")
            return
        if sub in ("挑战", "challenge", "打", "fight"):
            if B.in_battle(t):
                yield event.plain_result("⚠️ 先结束当前对战。")
                return
            async with self._lock(t.scope):
                state = self._state(t.scope)
                meta = npc.build_gym_battle(t, gym)
                log = B.start(
                    t,
                    meta["team"],
                    kind=meta["kind"],
                    meta=meta,
                    weather=meta.get("weather") or state.weather_for(t.region),
                    day=state.day,
                )
                self._save(t)
                yield event.plain_result(self._battle_intro(meta, log))
            return
        done = f"{t.region}:{int(gym.get('order', 0))}" in t.badges
        text = (
            f"🏛️ {gym.get('title')} —— {gym.get('leader')}({_type_zh(gym.get('type'))})\n"
            f"徽章:{gym.get('badge')}"
            f"{'(已获得)' if done else ''}\n"
            f"队伍:{_team_brief(gym.get('team'))}\n"
            + ("已击败,可再次切磋。" if done else "输入 `/道馆 挑战` 开始对战。")
        )
        async for r in self._emit_ui(
            event, "gym",
            lambda: UIM.render_gym(
                self._with_zh(gym), region_zh=world.region_zh(t.region),
                location_zh=world.node_zh(t.location), owned=done,
                scale=self._img_scale(),
            ),
            text=text,
        ):
            yield r

    @filter.command("联盟", alias={"league", "四天王", "冠军"})
    async def cmd_league(self, event: AstrMessageEvent):
        """/联盟 [挑战] —— 挑战四天王与冠军"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        world = WorldMap()
        region = t.region
        need = len(world.gyms(region)) or 8
        if t.badge_count(region) < need:
            yield event.plain_result(
                f"❌ 需要 {need} 枚{world.region_zh(region)}徽章,"
                f"你现在有 {t.badge_count(region)} 枚。"
            )
            return
        if not world.is_gateway(t.location):
            yield event.plain_result(
                f"❌ 请先前往 {world.node_zh(world.gateway(region))}。"
            )
            return
        e4 = world.elite4(region)
        champ = world.champion(region)
        if not e4 or not champ:
            yield event.plain_result("⚠️ 该地区联盟数据缺失。")
            return
        sub = self._args(event, ("联盟", "league", "四天王", "冠军")).strip()
        if sub not in ("挑战", "challenge", "打"):
            pending = [
                e for e in e4 if not t.flag(f"elite:{region}:{int(e.get('order', 1))}")
            ]
            lines = [f"🏆 {world.region_zh(region)}联盟"]
            for e in e4:
                mark = "✅" if t.flag(f"elite:{region}:{int(e.get('order', 1))}") else "⬜"
                lines.append(f"{mark} 四天王 {e.get('name')}({_type_zh(e.get('type'))})")
            lines.append(
                ("✅" if t.flag(f"champion:{region}") else "⬜")
                + f" 冠军 {champ.get('name')}"
            )
            lines.append(
                "输入 `/联盟 挑战` 依次挑战"
                + (f"(下一位:{pending[0].get('name')})" if pending else "(冠军)")
            )
            done_flags = [
                str(e.get("order", 1))
                for e in e4
                if t.flag(f"elite:{region}:{int(e.get('order', 1))}")
            ]
            async for r in self._emit_ui(
                event, "league",
                lambda: UIM.render_league(
                    world.region_zh(region),
                    [self._with_zh(e) for e in e4], self._with_zh(champ),
                    done=done_flags,
                    champion_done=bool(t.flag(f"champion:{region}")),
                    scale=self._img_scale(),
                ),
                text="\n".join(lines),
            ):
                yield r
            return
        if B.in_battle(t):
            yield event.plain_result("⚠️ 先结束当前对战。")
            return
        async with self._lock(t.scope):
            state = self._state(t.scope)
            pending = [
                e for e in e4 if not t.flag(f"elite:{region}:{int(e.get('order', 1))}")
            ]
            if pending:
                meta = npc.build_elite4_battle(t, pending[0])
            elif not t.flag(f"champion:{region}"):
                meta = npc.build_champion_battle(t, champ)
            else:
                meta = npc.build_champion_battle(t, champ)
                meta["title"] += "(再战)"
            log = B.start(
                t,
                meta["team"],
                kind=meta["kind"],
                meta=meta,
                weather=state.weather_for(region),
                day=state.day,
            )
            self._save(t)
            yield event.plain_result(self._battle_intro(meta, log))

    @filter.command("商店", alias={"shop", "购买"})
    async def cmd_shop(self, event: AstrMessageEvent):
        """/商店 [买|卖 <道具> [数量]] —— 商店"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        world = WorldMap()
        if "mart" not in world.services(t.location):
            yield event.plain_result("❌ 这里没有商店,去城镇(宝可梦中心所在地)吧。")
            return
        state = self._state(t.scope)
        discount = float(state.modifiers.get("shop_discount", 1.0))
        arg = self._args(event, ("商店", "shop", "购买")).strip()
        if not arg:
            text = self._shop_text(t, discount)
            async for r in self._emit_ui(
                event, "shop",
                lambda: UIM.render_shop(
                    self._shop_payload(t, discount), money=t.money,
                    location_zh=world.node_zh(t.location), discount=discount,
                    scale=self._img_scale(),
                ),
                text=text,
            ):
                yield r
            return
        tokens = arg.split()
        action = tokens[0]
        if (action in ("买", "buy") and len(tokens) >= 2) or (action in ("卖", "sell") and len(tokens) >= 2):
            name = tokens[1]
            n = coerce_int(tokens[2], 1) if len(tokens) > 2 else 1
        else:
            yield event.plain_result("❌ 用法:`/商店 买 伤药 3` 或 `/商店 卖 精灵球 2`")
            return
        n = int(clamp(n or 1, 1, 99))
        stock = set(world.shop_stock(t.location)) | set(EVO_STONE_STOCK)
        if action in ("买", "buy"):
            key = _resolve_stock(name, stock)
            if not key:
                yield event.plain_result(f"❌ 商店没有「{name}」。")
                return
            price = item_price(key, badge_count=t.badge_count(), discount=discount) * n
            if t.money < price:
                yield event.plain_result(
                    f"❌ 需要 {fmt_money(price)},你只有 {fmt_money(t.money)}。"
                )
                return
            t.spend_money(price)
            t.add_item(key, n)
            qlines = QT.note(t, "shop", amount=price)
            self._save(t)
            msg = (
                f"🛒 买下 {(BAG_ITEMS.get(key) or {}).get('zh', key)} ×{n}"
                f",花费 {fmt_money(price)}。余额 {fmt_money(t.money)}。"
            )
            if qlines:
                msg += "\n" + "\n".join(qlines)
            yield event.plain_result(msg)
            return
        key = _resolve_stock(name, set(t.bag))
        if not key or t.count(key) < n:
            yield event.plain_result(f"❌ 你没有足够的「{name}」。")
            return
        t.take_item(key, n)
        # 卖价必须与买价用**同一个基准**(同样吃徽章折扣与世界事件折扣)。
        # 原来的写法是不带折扣的 item_price(...) // 2,而买价会先打折再四舍五入到
        # 10 的整数倍 —— 3 徽章 + 事件打五折时精灵球买价 90₽、卖价 95₽,
        # 于是"买了立刻卖"就能白赚 5₽,可无限刷钱。
        gain = max(1, item_price(key, badge_count=t.badge_count(),
                                 discount=discount) // 2) * n
        t.add_money(gain)
        self._save(t)
        yield event.plain_result(
            f"💰 卖出 {(BAG_ITEMS.get(key) or {}).get('zh', key)} ×{n}"
            f",获得 {fmt_money(gain)}。余额 {fmt_money(t.money)}。"
        )

    @filter.command("治疗", alias={"heal", "恢复"})
    async def cmd_heal(self, event: AstrMessageEvent):
        """/治疗 —— 在宝可梦中心恢复"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        world = WorldMap()
        if "center" not in world.services(t.location):
            yield event.plain_result("❌ 这里没有宝可梦中心,去城镇吧。")
            return
        if B.in_battle(t):
            yield event.plain_result("⚠️ 对战中不能治疗。")
            return
        n = t.heal_party()
        self._save(t)
        yield event.plain_result(
            f"🏥 乔伊小姐为你的 {n} 只宝可梦做了治疗 —— 全部恢复如初!"
        )

    @filter.command("学招", alias={"learn", "学招式"})
    async def cmd_learn(self, event: AstrMessageEvent):
        """/学招 <队伍序号> <招式> [替换 <序号|招式>]"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        arg = self._args(event, ("学招", "learn", "学招式")).strip()
        tokens = arg.split()
        if len(tokens) < 2:
            yield event.plain_result("❌ 用法:`/学招 1 十万伏特` 或 `/学招 1 十万伏特 替换 3`")
            return
        idx = coerce_int(tokens[0], 1) or 1
        mon = t.mon(idx - 1)
        if mon is None:
            yield event.plain_result("❌ 队伍序号不对。")
            return
        mr = get_dex().resolve_move(tokens[1])
        if not mr:
            yield event.plain_result(f"❌ 未收录招式「{tokens[1]}」。")
            return
        replace = ""
        if len(tokens) >= 4 and tokens[2] in ("替换", "replace", "忘掉"):
            replace = tokens[3]
        pending = list(t.party[idx - 1].get("pending") or [])
        if len(mon.moves) >= 4 and not replace:
            yield event.plain_result(
                f"⚠️ {mon.display} 已会 4 个招式,请指定替换哪一个:\n"
                + "\n".join(f"{i}. {growth.move_zh(m)}" for i, m in enumerate(mon.moves, 1))
                + f"\n可用:`/学招 {idx} {tokens[1]} 替换 <序号|招式>`"
            )
            return
        if replace:
            old = mon.moves[int(replace) - 1] if replace.isdigit() and 0 < int(replace) <= len(mon.moves) else ""
            if not old:
                r2 = get_dex().resolve_move(replace)
                old = r2[0] if r2 and r2[0] in mon.moves else ""
            if not old:
                yield event.plain_result(f"❌ 找不到要替换的招式「{replace}」。")
                return
            growth.replace_move(mon, old, mr[0])
        elif not growth.learn_move(mon, mr[0]):
            yield event.plain_result("❌ 学不会(招式已满或已会)。")
            return
        t.commit(idx - 1, mon)
        if mr[0] in pending:
            pending.remove(mr[0])
        t.party[idx - 1]["pending"] = pending
        self._save(t)
        yield event.plain_result(f"✅ {mon.display} 学会了「{mr[1].get('zh')}」!")
        if pending:
            yield event.plain_result(
                "仍待决定:" + "、".join(growth.move_zh(m) for m in pending)
            )

    @filter.command("进化", alias={"evolve"})
    async def cmd_evolve(self, event: AstrMessageEvent):
        """/进化 <队伍序号> [道具] —— 查看/执行进化"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        dex = get_dex()
        arg = self._args(event, ("进化", "evolve")).strip()
        tokens = arg.split()
        if not tokens:
            lines = ["🧬 可进化一览:"]
            for i, _p in enumerate(t.party, 1):
                mon = t.mon(i - 1)
                opts = dex.evolution_options(
                    mon.species,
                    level=mon.level,
                    moves=set(mon.moves),
                    item=None,
                    friendship=mon.friendship,
                    gender=mon.gender,
                    stats=mon.stats,
                )
                if not opts:
                    lines.append(f"{i}. {mon.display} —— 无法进化")
                    continue
                desc = "、".join(
                    f"{growth.species_zh(o['target'])}"
                    f"({'可进化' if o.get('met') else '条件不足:' + _kind_zh(o.get('kind'))})"
                    for o in opts[:3]
                )
                lines.append(f"{i}. {mon.display} → {desc}")
            lines.append("用法:`/进化 <序号>` 或 `/进化 <序号> <进化石>`")
            yield event.plain_result("\n".join(lines))
            return
        idx = coerce_int(tokens[0], 1) or 1
        mon = t.mon(idx - 1)
        if mon is None:
            yield event.plain_result("❌ 队伍序号不对。")
            return
        item = tokens[1] if len(tokens) > 1 else ""
        if item:
            from .pw.items import resolve_bag_item

            r = resolve_bag_item(item)
            if not r or t.count(r[0]) <= 0:
                yield event.plain_result(f"❌ 背包里没有「{item}」。")
                return
            key, entry = r
            opts = dex.use_item_evolutions(mon.species, key)
            if not opts:
                yield event.plain_result(f"⚠️ {mon.display} 对 {entry['zh']} 没有反应。")
                return
            target = opts[0]["target"] if isinstance(opts[0], dict) else opts[0]
            t.take_item(key, 1)
            old = mon.species
            growth.apply_evolution(mon, target)
            t.commit(idx - 1, mon)
            qlines = QT.note(t, "evolve", species=old)
            self._save(t)
            msg = (
                f"✨ {growth.species_zh(old)} 使用了 {entry['zh']},"
                f"进化成了 {growth.species_zh(target)}!"
            )
            if qlines:
                msg += "\n" + "\n".join(qlines)
            yield event.plain_result(msg)
            return
        opts = dex.evolution_options(
            mon.species,
            level=mon.level,
            moves=set(mon.moves),
            item=mon.item or None,
            friendship=mon.friendship,
            gender=mon.gender,
            stats=mon.stats,
        )
        met = [o for o in opts if o.get("met")]
        if not met:
            yield event.plain_result(
                f"⚠️ {mon.display} 暂时无法进化。"
                + (
                    "条件:" + "、".join(_kind_zh(o.get("kind")) for o in opts)
                    if opts
                    else ""
                )
            )
            return
        target = met[0]["target"]
        old = mon.species
        growth.apply_evolution(mon, target)
        t.commit(idx - 1, mon)
        self._save(t)
        yield event.plain_result(
            f"✨ 咦……?{growth.species_zh(old)} 进化成了 {growth.species_zh(target)}!"
        )

    @filter.command("图鉴", alias={"dex", "宝可梦图鉴"})
    async def cmd_dex(self, event: AstrMessageEvent):
        """/图鉴 <名字> —— 图鉴资料"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        dex = get_dex()
        name = self._args(event, ("图鉴", "dex", "宝可梦图鉴")).strip()
        if not name:
            yield event.plain_result(
                f"📖 图鉴进度:已见到 {len(t.data['dex_seen'])} 种,"
                f"已捕获 {len(t.data['dex_caught'])} 种。"
            )
            return
        r = dex.resolve_species(name)
        if not r:
            yield event.plain_result(f"❌ 未收录宝可梦「{name}」。")
            return
        key, entry = r
        world = WorldMap()
        locs = world.locations_with_species(key, limit=8)
        types = "/".join(dex.type_label(x) for x in (entry.get("types") or []))
        bs = entry.get("baseStats") or {}
        lines = [
            f"📖 No.{entry.get('num', '?')} {entry.get('zh')} / {entry.get('name')}",
            f"属性:{types} · 种族值 {sum(int(bs.get(k, 0)) for k in ('hp','atk','def','spa','spd','spe'))}"
            f" (HP{bs.get('hp')} 攻{bs.get('atk')} 防{bs.get('def')} "
            f"特攻{bs.get('spa')} 特防{bs.get('spd')} 速{bs.get('spe')})",
            f"捕获率:{entry.get('captureRate')} · 成长:{entry.get('growthRate')}",
        ]
        flavor = entry.get("flavor")
        if flavor:
            lines.append(f"说明:{flavor}")
        evos = [
            f"{growth.species_zh(x)}"
            for x in (entry.get("evos") or [])
        ]
        if evos:
            lines.append("可进化为:" + "、".join(evos))
        if locs:
            lines.append("野外分布:")
            lines += [
                f"　· {world.region_zh(loc['region'])}·{loc['zh']} "
                f"Lv{loc['min']}-{loc['max']}"
                for loc in locs
            ]
        if t.caught(key):
            lines.append("✅ 已捕获")
        elif t.seen(key):
            lines.append("👁️ 已见到")
        else:
            lines.append("❔ 尚未见到")
        dex_entry = dict(entry)
        dex_entry["_key"] = key
        async for r in self._emit_ui(
            event, "dex",
            lambda: UI.render_dex(
                dex_entry, caught=t.caught(key), seen=t.seen(key),
                locations=locs, scale=self._img_scale(),
            ),
            text="\n".join(lines),
        ):
            yield r

    @filter.command("今日", alias={"today", "事件", "世界动态"})
    async def cmd_today(self, event: AstrMessageEvent):
        """/今日 —— 今日世界与个人事件"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        async with self._lock(t.scope):
            lines = await self._ensure_day(event, t)
            state = self._state(t.scope)
        body = D.today_brief(state, region=t.region, location=t.location)
        text = "\n".join([*lines, body])
        nar = self._narrator()
        if nar.available() and self._cfg_bool("announce_events", True):
            facts = [EV.event_text(e) for e in state.active_events()]
            rep = await nar.say(
                NARRATE_EVENT_SYSTEM,
                narration_facts("今日世界速报", facts, extra=f"第 {state.day} 天"),
                fallback="",
            )
            if rep.text and not rep.used_fallback:
                text += "\n\n📰 " + rep.text
        state2 = self._state(t.scope)
        locks = [
            f"{WorldMap().node_zh(k)}(第{int(v)}天解除)"
            for k, v in (state2.data.get("locks") or {}).items()
        ]
        pe_lines = [
            f"{e.get('title') or e.get('kind')}:{e.get('desc') or ''}".strip(":")
            for uid, arr in (state2.data.get("player_events") or {}).items()
            if uid == t.uid
            for e in arr
        ]
        async for r in self._emit_ui(
            event, "news",
            lambda: UII.render_news(
                state2.day,
                world_events=[EV.event_text(e) for e in state2.active_events()],
                player_events=pe_lines,
                weather_zh=state2.weather_for(t.region),
                region_zh=WorldMap().region_zh(t.region),
                location_zh=WorldMap().node_zh(t.location),
                locks=locks, scale=self._img_scale(),
            ),
            text=text,
        ):
            yield r

    @filter.command("任务", alias={"委托", "quest", "quests"})
    async def cmd_quest(self, event: AstrMessageEvent):
        """/任务 —— 查看/放弃支线委托"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        arg = self._args(event, ("任务", "委托", "quest", "quests")).strip()
        async with self._lock(t.scope):
            await self._ensure_day(event, t)
            parts = arg.split()
            if parts and parts[0] in ("放弃", "drop", "取消"):
                idx = coerce_int(parts[1], 0) if len(parts) > 1 else 0
                q = QT.abandon(t, idx)
                self._save(t)
                if not q:
                    yield event.plain_result("❌ 没有这个序号的委托。用 `/任务` 看看列表。")
                    return
                yield event.plain_result(f"🗑️ 你撇下了「{q.get('title')}」这条委托。")
                return
        text = QT.panel_text(t)
        acts = QT.active(t)
        async for r in self._emit_ui(
            event, "quest",
            lambda: UIQ.render_quests(
                acts,
                progress=[
                    (int(q.get("progress") or 0),
                     max(1, int((q.get("objective") or {}).get("count") or 1)))
                    for q in acts
                ],
                day=self._state(t.scope).day,
                region_zh=WorldMap().region_zh(t.region),
                done_count=len(QT.completed_ids(t)),
                max_active=QT.MAX_ACTIVE,
                scale=self._img_scale(),
            ),
            text=text,
        ):
            yield r

    @filter.command("重置世界", alias={"reset_world", "删除存档"})
    async def cmd_reset(self, event: AstrMessageEvent):
        """/重置世界 —— 删除自己的存档(管理员可 -all 清全群)"""
        scope, uid = self._scope(event), self._uid(event)
        arg = self._args(event, ("重置世界", "reset_world", "删除存档")).strip()
        admins = [a.strip() for a in str(self._cfg("admin_uids", "") or "").split(",") if a.strip()]
        if arg in ("all", "-all", "全部"):
            if admins and uid not in admins:
                yield event.plain_result("❌ 只有管理员能清空全群存档。")
                return
            n = self.trainers.delete_scope(scope)
            self.worlds.delete(scope)
            yield event.plain_result(f"🗑️ 已清空本会话的 {n} 份存档。")
            return
        if not self.trainers.exists(scope, uid):
            yield event.plain_result("❌ 你没有存档。")
            return
        self.trainers.delete(scope, uid)
        yield event.plain_result("🗑️ 你的存档已删除,可以重新 `/开始`。")

    @filter.command("帮助", alias={"help", "说明"})
    async def cmd_help(self, event: AstrMessageEvent):
        """/帮助 —— 指令一览"""
        yield event.plain_result(HELP_TEXT)

    # ══════════════════════════════════════════════════════════════
    # 文本渲染
    # ══════════════════════════════════════════════════════════════
    def _args(self, event: AstrMessageEvent, names: tuple[str, ...]) -> str:
        raw = str(getattr(event, "message_str", "") or "")
        s = raw.strip()
        # 去掉可能的指令前缀(/ ! ~ 等)后匹配命令名
        i = 0
        while i < len(s) and not s[i].isalnum() and not ("\u4e00" <= s[i] <= "\u9fff"):
            i += 1
        s = s[i:]
        for name in sorted(names, key=len, reverse=True):
            if s.startswith(name):
                return s[len(name) :].strip()
        return ""

    def _starter_menu(self, pool: list[str], name: str) -> str:
        """初始宝可梦选择菜单(默认参考《宝可梦 朱/紫》的御三家)。"""
        dex = get_dex()
        lines = [
            "🐾 选择你的初始宝可梦",
            "(默认参考《宝可梦 朱/紫》——新叶喵 / 呆火鳄 / 润水鸭,",
            " 也可从下列更广的名单中挑选)",
            "",
        ]
        for i, name_or_key in enumerate(pool, 1):
            r = dex.resolve_species(name_or_key)
            if not r:
                continue
            _key, entry = r
            types = "/".join(dex.type_label(x) for x in (entry.get("types") or []))
            bs = entry.get("baseStats") or {}
            total = sum(int(bs.get(k, 0)) for k in ("hp", "atk", "def", "spa", "spd", "spe"))
            lines.append(
                f"{i}. {entry.get('zh')}({types})种族值 {total} —— "
                f"Lv5 起步,亲密度较高"
            )
        lines += [
            "",
            "发送:`/开始 <你的名字> <宝可梦名>`",
            f"例如:`/开始 {name or '小智'} 新叶喵`",
            "初始还会获得:精灵球 ×5、伤药 ×3、解毒药 ×1、3000₽。",
        ]
        return "\n".join(lines)

    def _status_card(self, t: Trainer, today: list[str]) -> str:
        world = WorldMap()
        lines = [
            f"🧢 {t.name} 的训练家档案",
            f"📍 {world.where_am_i(t)} · 危险度 {world.tier_label(t.location)}",
            f"💰 {fmt_money(t.money)} · 🏅 徽章 {t.badge_count()}/{len(world.gyms(t.region)) or 8}"
            f" · 🎒 {sum(t.bag.values())} 件道具",
            f"📖 图鉴:见到 {len(t.data['dex_seen'])} / 捕获 {len(t.data['dex_caught'])}"
            f" · 电脑 {len(t.box)} 只",
            f"👟 步数 {t.data.get('steps', 0)} · 第 {game_day()} 天({game_day_str()})",
        ]
        cur = story.current_stage(t)
        if cur:
            lines.append(f"📜 主线:{cur['title']} —— {cur['desc']}")
        else:
            lines.append("📜 主线:本地区已完成(可用 `/大赛` 挑战世界大赛)")
        party = t.party_mon()
        if party:
            lines.append("── 队伍 ──")
            for i, mon in enumerate(party, 1):
                lines.append(
                    f"{i}. {mon.display} Lv{mon.level} "
                    f"HP {mon.cur_hp}/{mon.max_hp} [{bar(mon.cur_hp, mon.max_hp, 8)}]"
                )
        badges = _badges_by_region(t)
        if badges:
            lines.append("── 徽章 ──")
            lines += badges
        if today:
            lines += ["── 今日 ──", *today]
        return "\n".join(lines)

    def _map_text(self, t: Trainer, region: str, *, own: bool) -> str:
        world = WorldMap()
        nodes = world.nodes(region)
        if not nodes:
            return f"❌ {world.region_zh(region)}暂无地图数据。"
        lines = [f"🗺️ {world.region_zh(region)}(第 {world.regions[region].get('order', '?')} 地区)"]
        if own:
            lines.append(
                f"你在:{world.node_zh(t.location)} · 相邻:"
                + (
                    "、".join(
                        f"{world.node_zh(n)}(危险度{world.tier_label(n)})"
                        for n in world.neighbors(t.location)
                    )
                    or "无"
                )
            )
            g = world.gym_at(region, t.location)
            if g:
                lines.append(f"这里有道馆:{g.get('leader')}({_type_zh(g.get('type'))})")
        else:
            locked = region not in (t.data.get("unlocked_regions") or [])
            if locked:
                lines.append("🚧 该地区尚未开放(需先成为上一地区冠军)。")
        towns = world.list_towns(region)
        lines.append("── 城镇 ──")
        for k in towns:
            mark = "📍" if k == t.location else ("✅" if k in t.data.get("visited", []) else "⬜")
            gym = world.gym_at(region, k)
            tail = f" 🏛️{gym.get('leader')}" if gym else ""
            lines.append(f"{mark} {world.node_zh(k)}{tail}")
        if own:
            lines.append(
                "── 服务 ──\n"
                + "、".join(_service_zh(world.services(t.location)))
                + f"\n下一目标:{self._next_goal(t)}"
            )
        return "\n".join(lines)

    def _next_goal(self, t: Trainer) -> str:
        world = WorldMap()
        g = world.next_gym(t.region, t.badges)
        if g:
            return f"挑战 {world.node_zh(g.get('location'))} 的 {g.get('leader')}"
        if t.badge_count() >= (len(world.gyms(t.region)) or 8):
            return f"前往 {world.node_zh(world.gateway(t.region))} 挑战联盟"
        return "自由探索"

    def _shop_text(self, t: Trainer, discount: float) -> str:
        world = WorldMap()
        lines = [
            f"🛒 商店({world.node_zh(t.location)})· 余额 {fmt_money(t.money)}"
            + (f" · 折扣 {int((1 - discount) * 100)}%" if discount < 1 else "")
        ]
        for key in world.shop_stock(t.location):
            entry = BAG_ITEMS.get(key)
            if not entry:
                continue
            lines.append(
                f"· {entry['zh']} —— {fmt_money(item_price(key, badge_count=t.badge_count(), discount=discount))}"
                f"({entry.get('desc', '')})"
            )
        lines.append("── 进化石 ──")
        for key in EVO_STONE_STOCK:
            entry = BAG_ITEMS.get(key)
            if not entry:
                continue
            lines.append(
                f"· {entry['zh']} —— {fmt_money(item_price(key, badge_count=t.badge_count(), discount=discount))}"
            )
        lines.append("用法:`/商店 买 伤药 3` · `/商店 卖 精灵球 2`")
        return "\n".join(lines)

    def _battle_intro(self, meta: dict, log: list[str]) -> str:
        title = meta.get("title") or "对战"
        return f"⚔️ {title}!\n" + "\n".join(f"· {x}" for x in log[-8:])

    def _battle_hint(self, t: Trainer) -> str:
        mon = t.mon(0)
        if not mon:
            return ""
        return (
            "行动示例:`/对战 1`(招式序号)、`/捕捉 精灵球`、`/对战 switch 2`、"
            "`/对战 item 伤药`、`/对战 run`"
        )

    def _result_text(self, t: Trainer, res: B.TurnResult) -> str:
        head = {
            "win": "🎉 战斗胜利!",
            "caught": "🎉 捕获成功!",
            "escaped": "🏃 脱离了战斗",
            "loss": "😵 战斗失败……",
            "forfeit": "🏳️ 你认输了",
        }.get(res.outcome, "战斗结束")
        lines = [head, *res.rewards]
        if res.growth:
            lines.append("── 成长 ──")
            lines += res.growth
        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════════
    # 凌晨 4 点调度
    # ══════════════════════════════════════════════════════════════
    async def _scheduler(self) -> None:
        while True:
            try:
                await asyncio.sleep(60)
                if not self._cfg_bool("event_enable", True):
                    continue
                hour = int(coerce_int(self._cfg("event_hour", 4), 4) or 4)
                now = datetime.now()
                if now.hour != hour:
                    continue
                day = game_day(now)
                if self._last_notified_day == day:
                    continue
                await self._roll_all(day)
                self._last_notified_day = day
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("宝可梦世界: 每日调度出错: %s", e)

    async def _roll_all(self, day: int) -> None:
        for scope in self.worlds.list_scopes():
            try:
                players = self._players(scope)
                if not players:
                    continue
                state = self._state(scope)
                res = await D.roll_day(
                    scope=scope,
                    state=state,
                    players=players,
                    day=day,
                    narrator=self._narrator(),
                )
                if not res["rolled"]:
                    continue
                self._save_state(state)
                await self._notify(scope, state, res)
            except Exception as e:
                logger.debug("宝可梦世界: %s 每日刷新失败: %s", scope, e)

    async def _notify(self, scope: str, state: WorldState, res: dict) -> None:
        umo = str(state.data.get("umo") or "")
        if not umo or not res.get("world_events"):
            return
        send = getattr(self.context, "send_message", None)
        if send is None:
            return
        head = f"🌅 第 {state.day} 天开始了({game_day_str(state.day)})"
        body = "\n".join(f"· {EV.event_text(e)}" for e in res["world_events"])
        try:
            await send(umo, [Plain(f"{head}\n{body}\n\n输入 `/今日` 查看详情。")])
        except Exception as e:
            logger.debug("宝可梦世界: 每日通知失败: %s", e)


    @filter.command("主线", alias={"story", "剧情", "主线剧情"})
    async def cmd_story(self, event: AstrMessageEvent):
        """/主线 [挑战] —— 主线与敌对组织剧情"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        sub = self._args(event, ("主线", "story", "剧情", "主线剧情")).strip()
        async with self._lock(t.scope):
            state = self._state(t.scope)
            for st in story.progress(t, world=WorldMap(), day=state.day):
                self._save(t)
                yield event.plain_result(f"📜 主线推进:{st['title']}")
            cur = story.current_stage(t)
            if sub not in ("挑战", "challenge", "打", "开战"):
                info = story.STORY.get(t.region) or {}
                stages = story.region_stages(t.region)
                done = list(t.flag(f"story:{t.region}", []) or [])
                cur0 = story.current_stage(t)
                async for r in self._emit_ui(
                    event, "story",
                    lambda: UIM.render_story(
                        WorldMap().region_zh(t.region),
                        str(info.get("org") or "敌人"), str(info.get("leader") or "?"),
                        stages, done=done,
                        current_key=str((cur0 or {}).get("key") or ""),
                        badges=t.badge_count(),
                        total_gyms=len(WorldMap().gyms(t.region)) or 8,
                        scale=self._img_scale(),
                    ),
                    text=story.chapter_text(t),
                ):
                    yield r
                return
            if not cur:
                yield event.plain_result("本地区主线已经完成了。")
                return
            reason = story.stage_locked(t, cur)
            if reason:
                yield event.plain_result(f"❌ {reason}。\n{cur['desc']}")
                return
            if B.in_battle(t):
                yield event.plain_result("⚠️ 先结束当前对战。")
                return
            if t.all_fainted():
                yield event.plain_result("❌ 队伍全部失去战斗能力,先 `/治疗`。")
                return
            meta = story.boss_meta(t, cur)
            log = B.start(
                t, meta["team"], kind="rocket", meta=meta,
                weather=state.weather_for(t.region), day=state.day,
            )
            self._save(t)
            async for r in self._emit_battle(
                event, t, meta, log, text=self._battle_intro(meta, log)
            ):
                yield r

    @filter.command("神兽", alias={"legend", "传说", "传说宝可梦"})
    async def cmd_legend(self, event: AstrMessageEvent):
        """/神兽 [挑战 <名字>] —— 传说宝可梦定点遭遇"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        sub = self._args(event, ("神兽", "legend", "传说", "传说宝可梦")).strip()
        async with self._lock(t.scope):
            state = self._state(t.scope)
            world = WorldMap()
            if sub.startswith(("挑战", "challenge", "打", "捕捉", "catch")):
                name = sub
                for prefix in ("挑战", "challenge", "打", "捕捉", "catch"):
                    if name.startswith(prefix):
                        name = name[len(prefix) :].strip()
                        break
                here = legendary.ready(t, world=world, day=state.day)
                if not here:
                    yield event.plain_result(
                        "❌ 这里没有可挑战的神兽。用 `/神兽` 查看已知栖息地。"
                    )
                    return
                site = None
                if name:
                    for cand in here:
                        if name in (cand["zh"], cand["species"]):
                            site = cand
                            break
                    if site is None:
                        yield event.plain_result(
                            f"❌ 此地没有「{name}」。可选:"
                            + "、".join(c["zh"] for c in here)
                        )
                        return
                else:
                    site = here[0]
                if B.in_battle(t):
                    yield event.plain_result("⚠️ 先结束当前对战。")
                    return
                meta = legendary.legendary_meta(t, site)
                log = B.start(
                    t, meta["team"], kind="legend", wild=True, meta=meta,
                    weather=state.weather_for(t.region), day=state.day,
                )
                self._save(t)
                async for r in self._emit_battle(
                    event, t, meta, log,
                    text=self._battle_intro(meta, log)
                    + f"\n\n{self._battle_hint(t)}",
                ):
                    yield r
                return
            sites = legendary.sites_for(world.region_of(t.location) or t.region,
                                        world=world)
            if not sites:
                sites = legendary.sites_for(t.region, world=world)
            ready_keys = [s["species"] for s in legendary.ready(t, world=world, day=state.day)]
            caught_keys = [s["species"] for s in legendary.sites_for(t.region, world=world)
                           if legendary.caught(t, s["species"])]
            known_keys = {s["species"] for s in legendary.known(t, world=world)}
            locked_keys = [
                s["species"] for s in legendary.sites_for(t.region, world=world)
                if s["species"] not in known_keys and s["species"] not in caught_keys
            ]
            async for r in self._emit_ui(
                event, "legend",
                lambda: UII.render_legendaries(
                    world.region_zh(t.region), sites,
                    caught=caught_keys, ready=ready_keys, locked=locked_keys,
                    badges=t.badge_count(), total_gyms=len(world.gyms(t.region)) or 8,
                    champion=bool(t.flag(f"champion:{t.region}")), day=state.day,
                    scale=self._img_scale(),
                ),
                text=legendary.panel_text(t, world=world, day=state.day),
            ):
                yield r

    @filter.command("大赛", alias={"tournament", "世界大赛", "世界锦标赛"})
    async def cmd_tournament(self, event: AstrMessageEvent):
        """/大赛 [挑战] —— 冠军后解锁的世界大赛"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        world = WorldMap()
        if not story.tournament_unlocked(t, world=world):
            yield event.plain_result(
                "❌ 世界大赛只对冠军开放 —— 先成为任意地区的冠军吧。"
            )
            return
        sub = self._args(event, ("大赛", "tournament", "世界大赛", "世界锦标赛")).strip()
        rnd = int(t.flag("tournament_round", 0) or 0)
        best = int(t.flag("tournament_best", 0) or 0)
        if sub not in ("挑战", "challenge", "打", "开战"):
            lines = [
                "🏆 世界大赛",
                f"最佳战绩:{story.TOURNAMENT_ROUNDS[min(best, 2)][0] if best else '未参赛'}",
                "",
            ]
            for i, (name, _order) in enumerate(story.TOURNAMENT_ROUNDS):
                mark = "✅" if best > i else ("▶️" if rnd == i else "⬜")
                lines.append(f"{mark} {name}")
            lines.append("")
            lines.append(
                "输入 `/大赛 挑战` 开始"
                + (f"(下一场:{story.TOURNAMENT_ROUNDS[min(rnd, 2)][0]})" if rnd < 3 else "(已夺冠,可再次挑战)")
            )
            async for r in self._emit_ui(
                event, "tournament",
                lambda: UII.render_tournament(
                    [name for name, _o in story.TOURNAMENT_ROUNDS],
                    best=best, current=rnd, titles=story.TOURNAMENT_TITLES,
                    last_foe=str(t.flag("tournament_last_foe", "") or ""),
                    is_champion=bool(t.flag("world_champion")), scale=self._img_scale(),
                ),
                text="\n".join(lines),
            ):
                yield r
            return
        if B.in_battle(t):
            yield event.plain_result("⚠️ 先结束当前对战。")
            return
        if rnd >= len(story.TOURNAMENT_ROUNDS):
            rnd = 0
        async with self._lock(t.scope):
            state = self._state(t.scope)
            if t.all_fainted():
                yield event.plain_result("❌ 队伍全部失去战斗能力,先 `/治疗`。")
                return
            meta = story.tournament_meta(
                t, rnd, world=world, rng=stable_rng("tour", t.uid, state.day, rnd)
            )
            log = B.start(
                t, meta["team"], kind="tournament", meta=meta,
                weather=state.weather_for(t.region), day=state.day,
            )
            self._save(t)
            async for r in self._emit_battle(
                event, t, meta, log, text=self._battle_intro(meta, log)
            ):
                yield r

    # ── 战斗结果钩子 / 图片输出 ──
    def _with_zh(self, obj: dict) -> dict:
        """给队伍条目补上中文名(渲染层优先用 zh)。"""
        dex = get_dex()
        out = dict(obj or {})
        team = []
        for m in out.get("team") or []:
            if not isinstance(m, dict):
                continue
            item = dict(m)
            entry = dex.species.get(str(m.get("species") or "")) or {}
            item.setdefault("zh", entry.get("zh") or m.get("species") or "")
            team.append(item)
        if team:
            out["team"] = team
        return out

    def _shop_payload(self, t: Trainer, discount: float) -> list[dict]:
        world = WorldMap()
        out = []
        for key in [*world.shop_stock(t.location), *EVO_STONE_STOCK]:
            entry = BAG_ITEMS.get(key)
            if not entry:
                continue
            out.append(
                {
                    "key": key,
                    "zh": entry.get("zh") or key,
                    "price": item_price(key, badge_count=t.badge_count(),
                                        discount=discount),
                    "desc": entry.get("desc") or "",
                    "kind": entry.get("kind") or "",
                    "count": t.count(key),
                }
            )
        return out

    async def _emit_result_cards(self, event: AstrMessageEvent, t: Trainer, meta: dict,
                                 res: B.TurnResult):
        """战斗结束后追加:捕获 / 成长 / 战报卡片。"""
        if not self._cfg_bool("ui_image", True) or not res.finished:
            return
        view = B.view(t)
        mon = view.get("my") or {}
        if res.outcome == "caught" and res.rewards:
            _ball_key = str(res.item_key or "poke-ball")
            caught = B.dict_to_mon(t.party[-1]) if t.party else None
            if caught is not None:
                view_c = B._mon_view(caught)  # 复用内部视图构造
                async for r in self._emit_ui(
                    event, "gotcha",
                    lambda: UII.render_gotcha(
                        view_c,
                        ball_zh=(BAG_ITEMS.get(_ball_key) or {}).get("zh") or "精灵球",
                        ball_key=_ball_key,
                        dex_line=f"图鉴已记录:{len(t.data.get('dex_caught') or [])} 种",
                        scale=self._img_scale(),
                    ),
                    text="",
                ):
                    yield r
            return
        if res.growth:
            learned = [ln.split("「")[1].rstrip("」!") for ln in res.growth if "学会了" in ln]
            pending = [ln.split("「")[1].split("」")[0] for ln in res.growth if "想学" in ln]
            evo_to = ""
            evo_from = ""
            for ln in res.growth:
                if "进化了!" in ln:
                    part = ln.split("进化了!")[-1]
                    if "→" in part:
                        evo_from, evo_to = (x.strip() for x in part.split("→", 1))
            async for r in self._emit_ui(
                event, "growth",
                lambda: UII.render_growth(
                    mon, before_level=int(mon.get("level") or 0) - 1,
                    after_level=int(mon.get("level") or 0), learned=learned,
                    pending=pending, evolved_from_zh=evo_from, evolved_to_zh=evo_to,
                    scale=self._img_scale(),
                ),
                text="",
            ):
                yield r
            return
        if res.outcome in ("win", "loss", "forfeit", "escaped"):
            async for r in self._emit_ui(
                event, "result",
                lambda: UII.render_battle_result(
                    outcome=res.outcome, title=str(meta.get("title") or ""),
                    lines=res.lines, rewards=res.rewards, growth=res.growth, mon=mon,
                    scale=self._img_scale(),
                ),
                text="",
            ):
                yield r

    def _temp_image(self, data: bytes, prefix: str) -> str:
        """把渲染好的图片落盘到临时文件并返回路径。

        文件名必须**唯一**:AstrBot 是在指令返回之后才去读这个图片文件的,而旧实现
        用 `hash(text)%100000` / 图片字节长度当文件名 —— 两个不同玩家(或同一玩家的
        不同回合)撞上同一个名字时,先发出去的那张会被后写的覆盖,玩家就看到
        别人的/上一回合的画面。
        另外顺手清理过期临时图:旧实现只写不删,长时间运行会攒出成千上万个文件。
        """
        tmp = tempfile.gettempdir()
        path = os.path.join(tmp, f"{prefix}_{uuid4().hex}.png")
        with open(path, "wb") as f:
            f.write(data)
        _prune_temp_images(tmp)
        return path

    def _img_scale(self) -> int:
        return int(coerce_int(self._cfg("battle_image_scale", 3), 3) or 3)

    async def _emit_ui(self, event: AstrMessageEvent, label: str, builder, *,
                       text: str = ""):
        """按配置输出界面图片(仿 GBA 菜单);失败或未开启则回退文本。"""
        if self._cfg_bool("ui_image", True):
            try:
                data = builder()
                if data:
                    path = self._temp_image(data, f"pw_ui_{label}")
                    comps = [Image.fromFileSystem(path)]
                    comps.append(Plain(text) if text else Plain(" "))
                    yield event.chain_result(comps)
                    return
            except Exception as e:  # 渲染失败必须回退文本
                logger.debug("宝可梦世界: %s 界面渲染失败,回退文本: %s", label, e)
        if text:
            yield event.plain_result(text)

    def _party_payload(self, t: Trainer) -> list[dict]:
        """队伍界面数据。"""
        dex = get_dex()
        out = []
        for p in t.party:
            mon = B.dict_to_mon(p)
            species_data = dex.species.get(mon.species) or {}
            out.append(
                {
                    "species": mon.species,
                    "name": mon.nickname or species_data.get("zh") or mon.species,
                    "level": mon.level,
                    "cur_hp": mon.cur_hp,
                    "max_hp": mon.max_hp,
                    "status": mon.status,
                    "gender": mon.gender,
                    "item": mon.item,
                    "exp_pct": B.exp_progress(mon),
                }
            )
        return out

    def _bag_payload(self, t: Trainer, pocket_arg: str = "") -> dict:
        """背包界面数据:按口袋分组,返回当前口袋的条目。"""
        groups: dict[str, list[dict]] = {}
        for key, entry, n in t.bag_items():
            pk = UI.KIND_TO_POCKET.get(str(entry.get("kind") or ""), "items")
            groups.setdefault(pk, []).append(
                {
                    "key": key,
                    "zh": entry.get("zh") or key,
                    "count": n,
                    "desc": entry.get("desc") or "",
                    "kind": entry.get("kind") or "",
                }
            )
        want = (pocket_arg or "").strip()
        pocket = ""
        for pk, label in UI.POCKETS:
            if want and want in (pk, label):
                pocket = pk
        if not pocket:
            for pk, _label in UI.POCKETS:
                if groups.get(pk):
                    pocket = pk
                    break
            pocket = pocket or "items"
        return {
            "items": groups.get(pocket, []),
            "pocket": pocket,
            "selected": 0,
            "groups": groups,
        }

    def _card_payload(self, t: Trainer) -> dict:
        """训练家卡数据:`id_no` 由 uid 稳定派生,徽章按当前地区顺序排列。"""
        world = WorldMap()
        region = t.region
        got = {int(b.split(":")[1]) for b in t.badges if str(b).startswith(f"{region}:")}
        badges = [
            ((g.get("badge") or g.get("title") or "")[:3], int(g.get("order", 0)) in got)
            for g in world.gyms(region)[:8]
        ]
        while len(badges) < 8:
            badges.append(("", False))
        cur = story.current_stage(t)
        prog = f"{cur['title']}:{cur['desc']}" if cur else "本地区主线已完成"
        created = int(t.data.get("created_day") or 0)
        play_day = max(1, game_day() - created + 1) if created else 1
        return {
            "name": t.name,
            "id_no": f"{hash_int('idno', t.uid) % 90000 + 10000}",
            "money": t.money,
            "region": world.region_zh(region),
            "location": world.node_zh(t.location),
            "play_day": play_day,
            "steps": t.data.get("steps", 0),
            "party": len(t.party),
            "box": len(t.box),
            "seen": len(t.data.get("dex_seen") or []),
            "caught": len(t.data.get("dex_caught") or []),
            "badges": badges,
            "story_progress": prog,
            "best": "◆ /帮助 查看全部指令",
        }

    def _after_battle(self, t: Trainer, meta: dict, res: B.TurnResult, day: int) -> str:
        """结算主线/神兽/大赛的额外结果,返回要显示的前置文本。"""
        lines: list[str] = []
        stage = meta.get("story")
        if stage:
            if res.outcome == "win":
                if story.mark_stage(t, t.region, stage["key"]):
                    lines.append(f"📜 主线推进:{stage['title']} —— 你击退了{stage.get('org') or '敌方'}!")
            elif res.outcome in ("loss", "forfeit"):
                lines.append("📜 敌方暂时退去了……整理好队伍后再来 `/主线 挑战`。")
        site = meta.get("legend")
        if site:
            if res.outcome == "caught":
                legendary.mark_caught(t, site["species"])
                lines.append(f"🐉 传说的宝可梦 {site['zh']} 成为了你的伙伴!")
            elif res.outcome == "win":
                legendary.mark_fled(t, site["species"], day)
                lines.append(
                    f"🐉 {site['zh']} 被击退了,它逃走了 —— 明天再来或许还能遇到。"
                )
        rnd = meta.get("tournament_round")
        if rnd is not None:
            if res.outcome == "win":
                nxt = int(rnd) + 1
                if nxt > int(t.flag("tournament_best", 0) or 0):
                    t.set_flag("tournament_best", nxt)
                if nxt >= len(story.TOURNAMENT_ROUNDS):
                    if not t.flag("world_champion"):
                        t.set_flag("world_champion", True)
                        t.add_item("master-ball", 1)
                        t.add_item("rare-candy", 3)
                        t.add_money(20000)
                        lines.append(
                            "🏆 你成为了世界冠军!获得大师球 ×1、神奇糖果 ×3、20000₽。"
                        )
                    t.set_flag("tournament_round", 0)
                else:
                    t.set_flag("tournament_round", nxt)
                    lines.append(
                        f"🏆 晋级:{story.TOURNAMENT_ROUNDS[min(nxt, 2)][0]}!输入 `/大赛 挑战` 继续。"
                    )
            else:
                t.set_flag("tournament_round", 0)
                lines.append("🏆 你被淘汰了,大赛之旅结束。可以再次 `/大赛 挑战`。")
        lines += self._quests_after_battle(t, meta, res)
        self._save(t)
        return ("\n".join(lines) + "\n\n") if lines else ""

    def _quests_after_battle(self, t: Trainer, meta: dict, res: B.TurnResult) -> list[str]:
        """把战斗结果折算成任务进度。"""
        try:
            kind = str(meta.get("kind") or "")
            species = str(meta.get("species") or "")
            types = [str(x) for x in (meta.get("types") or [])]
            if not species:
                # 训练家战:用对手首发的物种(道馆/联盟/主线都适用)
                team = meta.get("team") or []
                if team and isinstance(team[0], dict):
                    species = str(team[0].get("species") or "")
                entry = get_dex().species.get(species) or {}
                types = [str(x) for x in (entry.get("types") or [])]
            sess = B.session(t) or {}
            used_item = bool(sess.get("used_item"))
            out: list[str] = []
            if res.outcome == "caught":
                out += QT.note(
                    t, "catch", species=species, types=types,
                    method=str(meta.get("method") or ""),
                )
                if species and meta.get("new_species", True) and t.seen(species):
                    out += QT.note(t, "dex", species=species)
            elif res.outcome == "win":
                out += QT.note(
                    t, "win", kind=kind, species=species, types=types,
                    is_trainer=kind in QT.TRAINER_KINDS, used_item=used_item,
                )
                levels = sum(1 for ln in res.growth if "升到了" in ln)
                if levels:
                    out += QT.note(t, "level_up", levels=levels)
            return out
        except Exception:  # 任务推进失败绝不能影响战斗结算
            logger.exception("任务进度推进失败")
            return []

    async def _emit_battle(
        self,
        event: AstrMessageEvent,
        t: Trainer,
        meta: dict,
        log: list[str],
        *,
        res: B.TurnResult | None = None,
        text: str = "",
    ):
        """按配置输出战斗画面:优先图片(仿经典对战界面),失败自动回退文本。"""
        if self._cfg_bool("battle_image", True):
            try:
                from .pw import battle_render

                if battle_render.available():
                    v = B.view(t)
                    data = battle_render.render_battle(
                        v.get("my") or {},
                        v.get("foe") or {},
                        list(log)[-3:],
                        title=(meta.get("title") or v.get("title") or ""),
                        weather=v.get("weather") or "",
                        terrain=v.get("terrain") or "",
                        location=WorldMap().node_zh(t.location),
                        my_party=v.get("party") or [],
                        turn=int(v.get("turn") or 0),
                        scale=int(coerce_int(self._cfg("battle_image_scale", 3), 3) or 3),
                    )
                    if data:
                        path = self._temp_image(data, f"pw_battle_{t.uid}")
                        comps = [Image.fromFileSystem(path)]
                        if text:
                            comps.append(Plain(text))
                        else:
                            comps.append(Plain(" "))
                        yield event.chain_result(comps)
                        return
            except Exception as e:  # 渲染失败必须回退文本
                logger.debug("宝可梦世界: 战斗图片渲染失败,回退文本: %s", e)
        if text:
            yield event.plain_result(text)

# ── 模块级小工具 ──────────────────────────────────────────────────
def _sp_zh(species: str | None) -> str:
    if not species:
        return "?"
    return ((get_dex().species.get(species) or {}).get("zh")) or species


def _type_zh(t: str | None) -> str:
    return get_dex().type_label(str(t or "")) if t else "?"


def _sprite_count() -> int:
    from .pw.sprites import available_count

    return available_count()


def _service_zh(services: list[str]) -> list[str]:
    return [
        {"center": "宝可梦中心", "mart": "商店", "gym": "道馆", "league": "联盟"}.get(s, s)
        for s in services
    ]


def _prune_temp_images(tmp: str, keep_seconds: int = 1800) -> None:
    """删掉我方的过期临时图(只碰 pw_ui_/pw_battle_ 前缀,不动别人的文件)。

    故意写成模块级函数而不是 `@staticmethod`:测试宿主对象 `_Cmd` 会用
    `getattr(Plugin, name)` 把类属性拷到自己身上,`staticmethod` 拷过去会退化成
    普通函数、多绑一个 self,导致调用签名错位。
    """
    now = time.time()
    for pat in ("pw_ui_*.png", "pw_battle_*.png"):
        for name in glob.glob(os.path.join(tmp, pat)):
            if not _older_than(name, now, keep_seconds):
                continue
            with contextlib.suppress(OSError):
                os.remove(name)


def _older_than(path: str, now: float, seconds: float) -> bool:
    """文件是否比 seconds 更旧(取不到 mtime 时视为"不旧",不删)。"""
    try:
        return now - os.path.getmtime(path) > seconds
    except OSError:
        return False


def _primary_method(methods) -> str:
    """把遭遇方式列表归一成一个代表值(任务系统用)。

    钓鱼类委托必须能识别出"是钓上来的":rod/fish 归一到 "fish",
    冲浪归一到 "surf";其余取第一个方式。
    """
    ms = [str(m).lower() for m in (methods or [])]
    if any("rod" in m or "fish" in m for m in ms):
        return "fish"
    if any("surf" in m for m in ms):
        return "surf"
    return ms[0] if ms else ""


def _environment_of(world: WorldMap, loc: str) -> str:
    methods = set(world.encounter_methods(loc))
    if methods & {"surf", "old-rod", "good-rod", "super-rod"}:
        return "water" if "surf" in methods else "fish"
    return "land"


def _resolve_stock(name: str, stock: set[str]) -> str:
    from .pw.items import resolve_bag_item

    r = resolve_bag_item(name)
    if r and r[0] in stock:
        return r[0]
    k = str(name or "").strip().lower()
    for key in stock:
        if key == k:
            return key
    return ""


def _team_brief(team) -> str:
    out = [
        f"{_sp_zh(m['species'])} Lv{m.get('level', '?')}"
        for m in (team or [])
        if isinstance(m, dict) and m.get("species")
    ]
    return "、".join(out) or "?"


def _kind_zh(kind: str | None) -> str:
    return {
        "level": "升级",
        "levelFriendship": "亲密度",
        "levelMove": "携带/学会指定招式",
        "levelHold": "携带道具升级",
        "useItem": "使用道具",
        "trade": "通信交换",
        "levelExtra": "特殊条件",
    }.get(str(kind or ""), str(kind or "未知"))


def _badges_by_region(t: Trainer) -> list[str]:
    world = WorldMap()
    out = []
    for region in world.regions_with_data():
        ids = {int(b.split(":")[1]) for b in t.badges if str(b).startswith(f"{region}:")}
        if not ids:
            continue
        gyms = {int(g.get("order", 0)): g for g in world.gyms(region)}
        names = [gyms[i].get("badge") or f"第{i}枚" for i in sorted(ids) if i in gyms]

        out.append(f"{world.region_zh(region)}({len(ids)}):" + "、".join(names))
    return out
