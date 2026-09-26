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
from datetime import datetime

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
from .pw import growth, npc
from .pw.dex import get_dex
from .pw.items import BAG_ITEMS
from .pw.narrate import Narrator
from .pw.player import Trainer, TrainerStore, new_trainer
from .pw.sprites import sprite_path
from .pw.util import (
    bar,
    clamp,
    coerce_int,
    fmt_money,
    game_day,
    game_day_str,
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
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()

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
        if not self._cfg("narrate_enable", True):
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
            narrator=self._narrator() if self._cfg("event_enable", True) else Narrator(None),
        )
        if res["rolled"]:
            wl = [
                f"🌍 世界事件:{EV.event_text(e)}"
                for e in res["world_events"]
            ]
            if wl and self._cfg("announce_events", True):
                lines.append(f"📅 第 {day} 天({game_day_str(day)})开始了。")
                lines += wl
        pe = D.deliver_player_events(trainer, state)
        if pe:
            lines.append("📨 今日个人事件:")
            lines += pe
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
        yield event.plain_result(self._status_card(t, today))

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
        heads = t.badge_count()
        text += f"\n\n库存:电脑 {len(t.box)} 只 · 徽章 {heads} 枚 · {fmt_money(t.money)}"
        yield event.plain_result(text)
        if self._cfg("sprite_enable", True):
            comps = [Plain(f"🖼️ {t.name} 的队伍")]
            for p in t.party[:6]:
                path = sprite_path(p.get("species") or "")
                if path:
                    comps.append(Image.fromFileSystem(path))
            if len(comps) > 1:
                yield event.chain_result(comps)

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
        yield event.plain_result("\n".join(lines))

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
        yield event.plain_result(self._map_text(t, region, own=region == t.region))

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
            t.data["location"] = key
            visited = t.data.setdefault("visited", [])
            if key not in visited:
                visited.append(key)
            t.data["steps"] = int(t.data.get("steps", 0)) + 120
            self._save(t)
            ev = state.event_at(key)
            tail = f"\n📍 这里正发生:{EV.event_text(ev)}" if ev else ""
            yield event.plain_result(
                f"🚶 你来到了 {world.region_zh(t.region)}·{world.node_zh(key)}。"
                f"\n危险度:{world.tier_label(key)} · 可用服务:"
                f"{'、'.join(_service_zh(world.services(key))) or '无'}{tail}"
            )

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
            # 1) 本地事件:火箭队 / 稀有宝可梦
            if ev and ev.get("kind") == "rocket":
                meta = npc.rocket_battle(t, ev)
                log = B.start(t, meta["team"], kind="rocket", meta=meta, day=state.day)
                self._save(t)
                notice.append(f"🚀 {EV.event_text(ev)}")
                yield event.plain_result("\n".join(notice))
                yield event.plain_result(self._battle_intro(meta, log))
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
                    yield event.plain_result("这里似乎什么也没有发生……")
                    return
                if ev and ev.get("kind") == "rare" and rng.random() < 0.35:
                    legend = npc.legendary_at(t, ev)
                    if legend:
                        hit = legend
                level = hit["level"]
                meta = {
                    "kind": "wild",
                    "title": f"野生的{hit['zh']}",
                    "location": loc,
                    "region": t.region,
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
                if notice:
                    yield event.plain_result("\n".join(notice))
                yield event.plain_result(
                    self._battle_intro(meta, log)
                    + f"\n\n{self._battle_hint(t)}"
                )
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
            t.data["steps"] = int(t.data.get("steps", 0)) + 40
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
            res = B.take_turn(
                t,
                arg,
                daytime=B.daytime_of(),
                money_mult=float(state.modifiers.get("money_mult", 1.0)),
                weather=state.weather_for(t.region),
                day=state.day,
            )
            self._save(t)
            if res.error:
                yield event.plain_result(res.error + "\n\n" + B.status_text(t))
                return
            text = "\n".join(res.lines)
            if res.finished:
                text += "\n\n" + self._result_text(t, res)
                text = await self._narrate(
                    "对战结束", res.lines + res.rewards + res.growth, text
                )
                yield event.plain_result(text)
                return
            yield event.plain_result(text)
            if res.awaiting_switch:
                yield event.plain_result(
                    "⚠️ 你的宝可梦倒下了,必须换人:\n" + B.team_status(t)
                )
                return
            if self._cfg("narrate_every_turn", False):
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
        yield event.plain_result(
            f"🏛️ {gym.get('title')} —— {gym.get('leader')}({_type_zh(gym.get('type'))})\n"
            f"徽章:{gym.get('badge')}"
            f"{'(已获得)' if done else ''}\n"
            f"队伍:{_team_brief(gym.get('team'))}\n"
            + ("已击败,可再次切磋。" if done else "输入 `/道馆 挑战` 开始对战。")
        )

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
            yield event.plain_result("\n".join(lines))
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
            yield event.plain_result(self._shop_text(t, discount))
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
            self._save(t)
            yield event.plain_result(
                f"🛒 买下 {(BAG_ITEMS.get(key) or {}).get('zh', key)} ×{n}"
                f",花费 {fmt_money(price)}。余额 {fmt_money(t.money)}。"
            )
            return
        key = _resolve_stock(name, set(t.bag))
        if not key or t.count(key) < n:
            yield event.plain_result(f"❌ 你没有足够的「{name}」。")
            return
        t.take_item(key, n)
        gain = max(1, item_price(key, badge_count=t.badge_count()) // 2) * n
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
            self._save(t)
            yield event.plain_result(
                f"✨ {growth.species_zh(old)} 使用了 {entry['zh']},"
                f"进化成了 {growth.species_zh(target)}!"
            )
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
        yield event.plain_result("\n".join(lines))

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
        if nar.available() and self._cfg("announce_events", True):
            facts = [EV.event_text(e) for e in state.active_events()]
            rep = await nar.say(
                NARRATE_EVENT_SYSTEM,
                narration_facts("今日世界速报", facts, extra=f"第 {state.day} 天"),
                fallback="",
            )
            if rep.text and not rep.used_fallback:
                text += "\n\n📰 " + rep.text
        yield event.plain_result(text)

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
                if not self._cfg("event_enable", True):
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
