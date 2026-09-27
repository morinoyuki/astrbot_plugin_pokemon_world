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
import re
import tempfile
import time
from datetime import datetime
from typing import TYPE_CHECKING
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
    TUTORIAL_TEXT,
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
from .pw.items import BAG_ITEMS, effect_text, max_pp
from .pw.narrate import Narrator
from .pw.player import Trainer, TrainerStore, mon_to_dict, new_trainer
from .pw.sqlite_store import SqliteBackend
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

if TYPE_CHECKING:  # 仅用于类型注解(运行时不需要)
    from .pw.engine import Pokemon
from .pw.world import (
    FLY_COST,
    WorldMap,
    item_price,
)
from .pw.worldstate import WorldState, WorldStore

DEFAULT_STARTERS = ["新叶喵", "呆火鳄", "润水鸭", "皮卡丘", "伊布", "小火龙", "杰尼龟", "妙蛙种子"]
EXPLORE_ITEM_POOL = ["potion", "poke-ball", "antidote", "oran-berry", "super-potion"]


# `/探索` 的目标别名:值里的第一项是规范模式名
_EXPLORE_ALIASES: dict[str, frozenset[str]] = {
    "all": frozenset({"", "全部", "任意", "随便", "都行", "all", "any"}),
    "wild": frozenset({"野生", "野怪", "宝可梦", "精灵", "wild", "pokemon"}),
    "trainer": frozenset({"训练家", "训练师", "npc", "trainer"}),
    "item": frozenset({"道具", "物品", "东西", "捡", "item", "items"}),
    "event": frozenset({"事件", "情报", "状况", "动态", "event", "news"}),
}

_EXPLORE_USAGE = """❌ 用法:`/探索 [目标]`
· `/探索` 或 `/探索 全部` —— 什么都可能碰上(默认)
· `/探索 野生` —— **只**找野生宝可梦
· `/探索 属性 水` —— 只找水属性的野生宝可梦(也可直接写 `/探索 水`)
· `/探索 训练家` —— 只找路边训练家(看到后用 `/训练家战` 挑战)
· `/探索 道具` —— 只捡道具
· `/探索 事件` —— 只看这里今天有什么事件与世界动态
每次探索固定消耗 40 步。"""


def _next_step(t: Trainer, state=None) -> str:
    """根据当前进度给一句"下一步该做什么"(新手引导的核心)。

    优先级刻意如此(先救命 → 先出门 → 就地能做的事 → 再去赶路):
    队伍危险 → 还没出门 → 还没抓到宝可梦 → 站在道馆城镇 → 徽章够了打联盟
    → 能进化/有没学的招 → 去下一个道馆 → 委托/探索。
    这样"就地的动作"总排在"赶路"前面,玩家不会被一句"去某某镇"牵着走一辈子。
    """
    try:
        world = WorldMap()
        dex = get_dex()
        party = t.data.get("party") or []
        if not party:
            return "🎯 下一步:去 `/探索` 找一只宝可梦收服它。"
        # ① 先救命
        hurt = [m for m in party
                if int(m.get("cur_hp") or 0) <= int(m.get("max_hp") or 1) // 4]
        if t.all_fainted() or len(hurt) >= max(1, len(party) // 2):
            if any(k.startswith(("potion", "super", "hyper", "max"))
                   for k in t.data.get("bag") or {}):
                return "🎯 下一步:队伍有点惨 —— 用 `/使用 伤药` 回血,或回宝可梦中心 `/治疗`。"
            return "🎯 下一步:队伍有点惨 —— 去宝可梦中心 `/治疗`(免费,顺带回满 PP)。"
        # ② 还没出过门
        if int(t.data.get("steps") or 0) <= 0:
            return ("🎯 下一步:发 `/探索` 出门看看(只想遇宝可梦就 `/探索 野生`,"
                    "只想捡道具就 `/探索 道具`)。")
        # ③ 图鉴才刚开始 → 教怎么抓
        if len(t.data.get("dex_caught") or []) <= 1 and t.count("poke-ball") > 0:
            return "🎯 下一步:遇到野生宝可梦时用 `/捕捉 精灵球` 收服(`/对战 1` 出招削弱它)。"
        # ④ 就地在下一个道馆城镇 → 直接挑战
        try:
            gym = world.next_gym(t.region, t.badges)
        except Exception as e:
            logger.debug("宝可梦世界: 下一道馆查询失败: %s", e)
            gym = None
        if gym and str(gym.get("location") or "") == t.location:
            return (f"🎯 下一步:{world.node_zh(t.location)} 的道馆就在眼前 —— "
                    f"`/道馆 挑战` 打 {gym.get('leader') or '馆主'}"
                    f"(现在 {t.badge_count()} 枚徽章)。")
        # ⑤ 本地区徽章齐了 → 联盟 / 大赛
        total_gym = len(world.gyms(t.region)) or 8
        if t.badge_count() >= total_gym and not t.flag(f"champion:{t.region}", False):
            gate = world.gateway(t.region)
            if gate and t.location == gate:
                return "🎯 下一步:`/联盟 挑战` —— 四天王与冠军在等你!"
            if gate:
                return (f"🎯 下一步:徽章齐了,`/地图` 走到 "
                        f"{world.node_zh(gate)},那里能 `/联盟 挑战`。")
            return "🎯 下一步:`/联盟 挑战` 打四天王与冠军!"
        if t.flag(f"champion:{t.region}", False):
            return "🎯 下一步:你已是本地区冠军 —— `/大赛 挑战` 冲击世界冠军。"
        # ⑥ 就地培养:能进化 / 有没学的招
        for i, p in enumerate(party, 1):
            mon = B.dict_to_mon(p)
            try:
                opts = dex.evolution_options(
                    mon.species, level=mon.level, moves=mon.moves, item=mon.item,
                    friendship=mon.friendship, gender=mon.gender, daytime="day",
                    party=party,
                )
            except Exception as e:
                logger.debug("宝可梦世界: 进化条件查询失败: %s", e)
                opts = []
            if any(o.get("kind") in ("level", "levelFriendship", "levelMove", "levelHold")
                   for o in opts):
                return f"🎯 下一步:第 {i} 只 {mon.display} 可以 `/进化` 了(变强不少)。"
        for i, p in enumerate(party, 1):
            mon = B.dict_to_mon(p)
            try:
                new = [m for m in dex.learnable(mon.species, mon.level)
                       if m.get("move") not in mon.moves]
            except Exception as e:
                logger.debug("宝可梦世界: 可学招式查询失败: %s", e)
                new = []
            if new:
                zh = growth.move_zh(new[0].get("move") or "")
                return f"🎯 下一步:第 {i} 只 {mon.display} 有没学的招(`/学招 {i}`,例如 {zh})。"
        # ⑦ 赶路:去下一个道馆
        if gym:
            town = str(gym.get("location") or "")
            if town:
                return (f"🎯 下一步:`/地图` 看路线、`/前往 <相邻地点>` 一路走到 "
                        f"{world.node_zh(town)},那里有道馆。")
        return "🎯 下一步:`/任务` 看今日委托,或 `/探索` 继续变强。"
    except Exception as e:      # 引导文案绝不能把指令搞崩,但留日志便于排查
        logger.warning("宝可梦世界: 下一步提示生成失败: %s", e)
        return "🎯 下一步:`/探索` 出门看看,或 `/帮助` 看全部指令。"


def _explore_target(arg: str) -> tuple[str, str]:
    """解析 `/探索` 的目标,返回 `(模式, 属性英文名或空串)`。

    模式:`all` / `wild` / `trainer` / `item` / `event`;无法识别返回 `("", "")`。
    属性支持 `/探索 属性 水` 与 `/探索 水` 两种写法。
    """
    raw = str(arg or "").strip()
    if not raw:
        return "all", ""
    parts = raw.replace(",", " ").replace("、", " ").split()
    head = parts[0] if parts else ""
    dex = get_dex()
    if head in ("属性", "type", "系"):
        if len(parts) < 2:
            return "", ""
        t = dex.resolve_type(parts[1])
        return ("wild", t) if t else ("", "")
    low = raw.lower()
    for mode, names in _EXPLORE_ALIASES.items():
        if low in names or head.lower() in names:
            return mode, ""
    t = dex.resolve_type(raw)          # 直接写属性名
    if t:
        return "wild", t
    return "", ""


def _species_types(species: str | None) -> list[str]:
    """图鉴里的属性(英文)。"""
    if not species:
        return []
    entry = get_dex().species.get(str(species)) or {}
    return [str(x) for x in (entry.get("types") or [])]


def _available_types(world, location: str) -> list[str]:
    """该地点野生宝可梦的属性集合(中文名,去重排序,给提示用)。"""
    dex = get_dex()
    out: set[str] = set()
    for row in world.wild_pools(location):
        for t in _species_types(row.get("species")):
            out.add(dex.type_label(t))
    return sorted(out)



class PokemonWorldPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        self.data_dir = StarTools.get_data_dir()
        # 存档后端:默认 SQLite(单文件、事务原子、写入不必全量重写);
        # 配置 storage=json 可退回旧的"每 uid 一个 JSON 文件"实现。
        # 首次打开数据库时会自动导入磁盘上的旧 JSON 存档(改名为 *.imported 保留)。
        self._storage = str(self._cfg("storage", "sqlite") or "sqlite").strip().lower()
        self._db = SqliteBackend(self.data_dir) if self._storage != "json" else None
        if self._db is not None:
            self._db.import_legacy()
        self.trainers = TrainerStore(self.data_dir, backend=self._db)
        self.worlds = WorldStore(self.data_dir, backend=self._db)
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

    def _require(self, event: AstrMessageEvent, *,
                 in_battle_ok: bool = False) -> tuple[Trainer | None, str]:
        """取玩家存档,并做"对战锁定"检查。

        `in_battle_ok=True` 用于对战/捕捉/纯查看类指令。其余指令默认在
        对战中拒绝 —— 否则玩家可以在战斗中途 `/探索`、`/前往`、`/治疗`,
        战斗状态与世界状态会互相踩(用户要求:战斗时其他行动禁用)。
        """
        t = self._load(event)
        if t is None:
            return None, (
                "❌ 你还没有开始旅程。\n"
                "· 直接开始:`/开始 小智 皮卡丘`(名字 + 御三家)\n"
                "· 不知道选什么:发 `/新手` 看 5 步上手引导"
            )
        if not in_battle_ok and B.in_battle(t):
            return None, _BATTLE_LOCKED_MSG
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
        try:
            res = await D.roll_day(
                scope=scope,
                state=state,
                players=self._players(scope, current=trainer),
                day=day,
                narrator=self._narrator()
                if self._cfg_bool("event_enable", True)
                else Narrator(None),
            )
        except Exception:
            # 每日滚动内部已按事件逐条降级,这里只兜住意外。
            # 必须把当天标记为"已滚动":否则 last_roll_day 一直没写,
            # 之后**每条指令**都会重试并再次抛异常(把一次故障放大成整体不可用)。
            logger.exception("每日世界滚动失败")
            state.data["last_roll_day"] = day
            res = {"rolled": False, "world_events": [], "player_events": {}}
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
                # 展示用**相对天数**:game_day() 是 ordinal,直接显示会出现
                # "第 739885 天"这种鬼数字(用户报告)。内部逻辑仍用绝对序号。
                lines.append(
                    f"📅 世界第 {state.day_no(day)} 天({game_day_str(day)})开始了。"
                )
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
                if self.trainers.load(scope, uid):
                    yield event.plain_result(
                        "你已经开始过旅程了。查看 `/状态`,或管理员用 `/重置世界` 重新开始。"
                    )
                    return
                # 文件在但读不出来 → 备份后允许重开(否则玩家被"卡死")
                bak = self.trainers.backup_corrupt(scope, uid)
                logger.warning("宝可梦世界: %s/%s 存档损坏,已备份为 %s", scope, uid, bak)
                yield event.plain_result(
                    f"⚠️ 你的存档已损坏(备份为 `{bak or '未知'}`),现在重新开始一段旅程。"
                )
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
            # 初始宝可梦必须来自候选池,否则玩家能直接选超梦/阿尔宙斯开局
            if starter:
                pool_keys = set()
                for key in pool:
                    hit = get_dex().resolve_species(key)
                    if hit:
                        pool_keys.add(str(hit[0]))
                hit0 = get_dex().resolve_species(starter)
                if hit0 and str(hit0[0]) not in pool_keys:
                    yield event.plain_result(
                        f"❌ 「{starter}」不在初始宝可梦候选里。"
                        "用 `/开始 <名字>` 不带宝可梦会弹出选择菜单。"
                    )
                    return
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
        t, err = self._require(event, in_battle_ok=True)
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
            hint=self._status_hint(t, today),
        ):
            yield r

    @filter.command("队伍", alias={"team", "宝可梦队伍"})
    async def cmd_team(self, event: AstrMessageEvent):
        """/队伍 [存入|取出|换位|放生|电脑] <序号> —— 队伍与仓库管理"""
        t, err = self._require(event, in_battle_ok=True)
        if err:
            yield event.plain_result(err)
            return
        parts = self._args(event, ("队伍", "team", "宝可梦队伍")).split()
        sub = parts[0] if parts else ""
        args = parts[1:]
        # 管理类操作(存/取/换位/放生)在对战中一律禁止:否则可以把队伍
        # 换成满血队在战斗途中"续命",也能把出战的那只存进电脑。
        managing = sub in ("存入", "取下", "取出", "换位", "交换", "放生",
                           "deposit", "withdraw", "swap", "release")
        if managing and B.in_battle(t):
            yield event.plain_result(
                "⚠️ 对战中不能整理队伍 —— 先打完这场(出招/捕捉/逃跑)。"
            )
            return
        if sub in ("存入", "deposit"):
            idx = coerce_int(args[0] if args else "0", 0)
            mon = t.mon(idx - 1) if idx >= 1 else None
            if mon is None:
                yield event.plain_result(
                    f"❌ 用法:`/队伍 存入 <队伍序号>`(现在是 1~{max(1, len(t.party))})"
                )
                return
            if len(t.party) <= 1:
                yield event.plain_result("❌ 队伍里至少要留一只宝可梦。")
                return
            if not t.deposit(idx - 1):
                yield event.plain_result("❌ 存入失败(序号越界或队伍只剩一只)。")
                return
            self._save(t)
            yield event.plain_result(
                f"📦 {mon.display} 已存入电脑(队伍 {len(t.party)} 只 / 电脑 {len(t.box)} 只)。"
            )
            return
        if sub in ("取出", "withdraw"):
            ident = args[0] if args else ""
            from .pw.player import MAX_PARTY

            if not ident:
                yield event.plain_result("❌ 用法:`/队伍 取出 <电脑序号>`(用 `/电脑` 看列表)")
                return
            hit = t.box_find(ident)
            if hit is None:
                yield event.plain_result(f"❌ 电脑里没有「{ident}」。")
                return
            if len(t.party) >= MAX_PARTY:
                yield event.plain_result(
                    f"❌ 队伍已经满 {MAX_PARTY} 只了,先用 `/队伍 存入 <序号>` 腾个位置。"
                )
                return
            mon_zh = _sp_zh(str(hit[1].get("species")))
            if not t.withdraw(str(hit[1].get("id") or "")):
                yield event.plain_result("❌ 取出失败。")
                return
            self._save(t)
            yield event.plain_result(
                f"🎒 {mon_zh} 加入了队伍(队伍 {len(t.party)} 只 / 电脑 {len(t.box)} 只)。"
            )
            return
        if sub in ("换位", "交换", "swap"):
            if len(args) < 2:
                yield event.plain_result("❌ 用法:`/队伍 换位 <序号A> <序号B>`")
                return
            a, b = coerce_int(args[0], 0), coerce_int(args[1], 0)
            if not t.swap_party(a, b):
                yield event.plain_result("❌ 换位失败(序号要在 1~6 之间且不相同)。")
                return
            self._save(t)
            yield event.plain_result(
                f"🔀 已交换第 {a} 只与第 {b} 只 —— 第 1 只是首发。"
            )
            return
        if sub in ("放生", "release"):
            if len(args) < 2 or args[1] not in ("确认", "confirm", "yes"):
                yield event.plain_result(
                    "⚠️ 放生不可撤销。确认请输入:`/队伍 放生 <序号> 确认`"
                )
                return
            mon = t.release_pokemon(args[0], where="party")
            if mon is None:
                yield event.plain_result(
                    "❌ 放生失败(序号越界,或者队伍只剩这一只)。"
                )
                return
            self._save(t)
            yield event.plain_result(
                f"👋 你放生了 {mon.display}(Lv{mon.level})。它回到了野外。"
                + ("(队伍已空,已自动从电脑补一只)" if not t.party else "")
            )
            return
        if sub in ("电脑", "box", "仓库"):
            async for r in self.cmd_box(event):
                yield r
            return
        if not t.party:
            yield event.plain_result("队伍是空的,去野外收服一只吧:`/探索`")
            return
        async with self._lock(t.scope):
            await self._ensure_day(event, t)
        text = B.team_status(t)
        text += f"\n\n库存:电脑 {len(t.box)} 只 · 徽章 {t.badge_count()} 枚 · {fmt_money(t.money)}"
        text += (
            "\n\n管理:`/队伍 存入 <序号>`、`/队伍 取出 <序号>`、"
            "`/队伍 换位 <A> <B>`、`/队伍 放生 <序号> 确认`"
        )
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
            hint=(
                f"管理:`/宝可梦 <1-{max(1, len(t.party))}>` 看资料、"
                f"`/队伍 存入 <序号>` 存电脑、`/队伍 取出 <序号>` 取出、"
                "`/队伍 换位 <A> <B>` 调首发"
            ),
        ):
            yield r

    def _current_mon_index(self, t: Trainer) -> int:
        """当前"关注"的宝可梦:对战中 = 出战的那只,否则 = 队伍第一只。"""
        if B.in_battle(t):
            side = ((B.session(t) or {}).get("battle") or {}).get("player") or {}
            idx = int(side.get("active") or 0)
            if 0 <= idx < len(t.party):
                return idx
        return 0

    def _move_line(self, key: str, *, index: int = 0, pp: int | None = None,
                   pp_max: int | None = None) -> str:
        """招式的一行摘要:名字 + 属性/分类 + 威力/命中 + PP + 一句效果。"""
        dex = get_dex()
        e = dex.moves.get(str(key)) or {}
        zh = e.get("zh") or growth.move_zh(key)
        cat = dex.move_category_zh(e.get("category"))
        tlabel = dex.type_label(str(e.get("type") or ""))
        base = int(e.get("basePower") or 0)
        acc = e.get("accuracy")
        head = f"{index}. {zh} [{tlabel}/{cat}]"
        if base:
            head += f" 威力{base}"
        a = _accuracy_zh(acc)
        if a not in ("必中", "—"):
            head += f" 命中{a}"
        if pp is not None:
            head += f" PP {int(pp)}/{int(pp_max if pp_max is not None else pp)}"
        eff = dex.move_short_desc(key) or "—"
        return f"{head}\n　　{eff}"

    def _move_detail(self, key: str, *, mon=None) -> str:
        """单个招式的完整资料。"""
        dex = get_dex()
        e = dex.moves.get(str(key)) or {}
        zh = e.get("zh") or growth.move_zh(key)
        cat = dex.move_category_zh(e.get("category"))
        tlabel = dex.type_label(str(e.get("type") or ""))
        acc = e.get("accuracy")
        head = f"◆ {zh}(No.{int(e.get('num') or 0):03d})"
        if mon is not None:
            dex2 = get_dex()
            owner = str(mon.nickname or (dex2.species.get(mon.species) or {}).get("zh")
                        or mon.species)
            head += f" · {owner} Lv{mon.level}"
        lines = [head]
        rows = [
            f"属性:{tlabel} · 分类:{cat}",
            f"威力:{int(e.get('basePower') or 0) or '—'} · 命中:{_accuracy_zh(acc)}",
            f"PP:{int(e.get('pp') or 0)} · 优先度:{int(e.get('priority') or 0)}"
            + (" · 容易会心" if e.get("critRatio") else ""),
        ]
        if mon is not None:
            cur = int(mon.pp.get(key, 0) or 0)
            mx = int((e.get("pp") or 0) or 0)
            rows.append(f"它当前 PP:{cur}/{mx or cur}")
        lines += rows
        eff = dex.move_effect_text(key)
        if eff:
            lines.append(f"⚙️ 实际效果:{eff}")
        desc = str(e.get("desc") or "").replace("\n", " ").strip()
        if "无法使用这个招式" in desc:
            lines.append("⚠️ 这是非标准招式,当前世代(Gen9)已无法使用")
        elif desc:
            lines.append(f"📖 图鉴说明:{desc}")
        if mon is not None:
            row = dex.learnable(mon.species, 100).__iter__()
            hit = next((it for it in row if it["move"] == key), None)
            if key in (mon.moves or []):
                lines.append("✅ 你的宝可梦已经学会这招")
            elif hit:
                lines.append(f"📗 它可以学会({_learn_zh(hit.get('methods'))})")
            else:
                lines.append("🚫 这只宝可梦学不了这招")
        return "\n".join(lines)

    @filter.command("招式", alias={"技能", "招式表", "move", "moves"})
    async def cmd_move(self, event: AstrMessageEvent):
        """/招式 [序号|名字] —— 查看招式说明与效果(对战中可随时查,不消耗回合)"""
        t, err = self._require(event, in_battle_ok=True)
        if err:
            yield event.plain_result(err)
            return
        parts = self._args(event, ("招式", "技能", "招式表", "move", "moves")).split()
        where, mon_dict = "party", None
        move_arg = ""
        if parts and parts[0] in ("电脑", "box", "仓库"):
            hit = t.box_find(parts[1] if len(parts) > 1 else "")
            if hit is None:
                yield event.plain_result(
                    f"❌ 电脑里没有「{parts[1] if len(parts) > 1 else ''}」。用 `/电脑` 看列表。"
                )
                return
            where, mon_dict = "box", hit[1]
            move_arg = parts[2] if len(parts) > 2 else ""
        elif len(parts) >= 2 and parts[0].isdigit():
            # `/招式 <队伍序号> <招式序号>`:指定某只宝可梦
            idx = int(parts[0])
            if not 1 <= idx <= len(t.party):
                yield event.plain_result(f"❌ 队伍里没有第 {idx} 只。")
                return
            where, mon_dict = "party", t.party[idx - 1]
            move_arg = parts[1]
        elif parts:
            where, move_arg = ("party", parts[0]) if not B.in_battle(t) else ("battle", parts[0])
        if mon_dict is None:
            idx = self._current_mon_index(t)
            if not t.party:
                yield event.plain_result("队伍是空的,先去 `/探索` 收服一只吧。")
                return
            where, mon_dict = "party", t.party[idx]
        mon = B.dict_to_mon(mon_dict)
        dex = get_dex()
        raw = str(mon.nickname or (dex.species.get(mon.species) or {}).get("zh")
                  or mon.species)
        head = f"◆ {raw} Lv{mon.level} 的招式"
        if where == "box":
            head = f"◆ {raw}(电脑)Lv{mon.level} 的招式"
        if not mon.moves:
            yield event.plain_result(head + ":没有招式。")
            return
        # 有招式参数 → 看详情
        if move_arg:
            key = ""
            if move_arg.isdigit():
                n = int(move_arg)
                if not 1 <= n <= len(mon.moves):
                    yield event.plain_result(
                        f"❌ 它只有 {len(mon.moves)} 个招式(1~{len(mon.moves)})。"
                    )
                    return
                key = mon.moves[n - 1]
            else:
                # 先在自己会的招式里按名字找,再退到全图鉴查询
                want = str(move_arg).strip().lower()
                key = next(
                    (m for m in mon.moves
                     if want in (str(m).lower(),
                                 str((dex.moves.get(m) or {}).get("zh") or "").lower(),
                                 str((dex.moves.get(m) or {}).get("name") or "").lower())),
                    "",
                )
                if not key:
                    # resolve_move 返回 (key, entry) 或 None(注意别把元组当 key)
                    hit = dex.resolve_move(move_arg)
                    if hit and str(hit[0]) not in (mon.moves or []):
                        yield event.plain_result(
                            self._move_detail(str(hit[0]))
                            + f"\n(注意:{raw} 没有学会这招)"
                        )
                        return
                    key = str(hit[0]) if hit else ""
                if not key:
                    yield event.plain_result(f"❌ 找不到招式「{move_arg}」。")
                    return
            yield event.plain_result(self._move_detail(key, mon=mon))
            return
        # 无参数 → 招式列表
        lines = [head, "（看详情:`/招式 <序号>`;对战中查招式**不消耗回合**）"]
        for i, key in enumerate(mon.moves[:4], 1):
            lines.append(self._move_line(
                key, index=i, pp=int(mon.pp.get(key, 0) or 0),
                pp_max=max_pp(mon, key),
            ))
        yield event.plain_result("\n".join(lines))

    @filter.command("宝可梦", alias={"精灵", "查看", "资料", "mon", "pokemon"})
    async def cmd_mon(self, event: AstrMessageEvent):
        """/宝可梦 [序号|名字] —— 查看单只宝可梦的详细资料(电脑里的要加前缀)"""
        t, err = self._require(event, in_battle_ok=True)
        if err:
            yield event.plain_result(err)
            return
        parts = self._args(event, ("宝可梦", "精灵", "查看", "资料", "mon", "pokemon")).split()
        where, ident = "party", ""
        if parts and parts[0] in ("电脑", "box", "仓库"):
            where = "box"
            ident = parts[1] if len(parts) > 1 else ""
        elif parts:
            ident = parts[0]
        # 不带参数:对战中看当前出战的那只,平时看队伍第一只
        if not ident and where == "party":
            if B.in_battle(t):
                side = ((B.session(t) or {}).get("battle") or {}).get("player") or {}
                ident = str(int(side.get("active") or 0) + 1)
            else:
                ident = "1"
        if where == "box":
            hit = t.box_find(ident)
            if hit is None:
                yield event.plain_result(
                    f"❌ 电脑里没有「{ident}」。用 `/电脑` 看仓库列表。"
                )
                return
            idx, md, total = hit[0], hit[1], len(t.box)
        else:
            found = t.find(ident)
            if found is None:
                yield event.plain_result(
                    f"❌ 队伍里没有「{ident}」。用 `/队伍` 看队伍、`/电脑` 看仓库。"
                )
                return
            idx, mon_obj, total = found[0], found[1], len(t.party)
            md = t.party[idx]
        if not isinstance(md, dict):
            md = (mon_obj.to_dict() if hasattr(mon_obj, "to_dict") else {})
        payload = self._mon_payload(t, md, index=idx + 1, party_size=total)
        if where == "box":
            payload["name"] = f"(仓库){payload['name']}"
        text = self._mon_text(t, payload, where=where)
        async for r in self._emit_ui(
            event, "mon",
            lambda: UI.render_mon_summary(
                payload, index=idx + 1, party_size=total, scale=self._img_scale(),
            ),
            text=text,
            # 特性说明放这里而不是画进图里:图片底部留给四行资料更清爽,
            # 而且聊天里的正常字号比 6px 的图内小字好读得多
            hint=self._mon_hint(payload),
        ):
            yield r

    def _mon_hint(self, payload: dict) -> str:
        """资料页图片附带的文本:特性说明 + 招式效果 + 管理入口。

        图里只能画下招式名(4 行 8.8px 的行距),所以"这招是干嘛的"放在文本里 ——
        用户反馈"招式大部分情况下只显示了名称 没办法查看说明和效果"。
        """
        lines = []
        ab = str(payload.get("ability_zh") or "")
        desc = str(payload.get("ability_desc") or "")
        if ab:
            lines.append(f"◆ 特性 {ab}:{desc}" if desc else f"◆ 特性 {ab}")
        lines.extend(
            f"◆ 待学「{growth.move_brief(mv)}」"
            f" —— 用 `/学招 {int(payload.get('index') or 1)} <招式> 替换 <序号>`"
            for mv in (payload.get("pending") or [])
        )
        moves = payload.get("moves") or []
        if moves:
            lines.append("◆ 招式")
            for i, mv in enumerate(moves, 1):
                key = str(mv.get("key") or "")
                dex = get_dex()
                e = dex.moves.get(key) or {}
                cat = dex.move_category_zh(e.get("category"))
                base = int(e.get("basePower") or 0)
                bits = f"[{dex.type_label(str(e.get('type') or ''))}/{cat}"
                if base:
                    bits += f" 威力{base}"
                if str(e.get("accuracy")) != "True" and isinstance(
                    e.get("accuracy"), (int, float)
                ):
                    bits += f" 命中{int(e['accuracy'])}%"
                bits += "]"
                eff = dex.move_short_desc(key)
                lines.append(
                    f"{i}. {mv.get('zh')} {bits}" + (f" —— {eff}" if eff else "")
                )
        lines.append("详情:`/招式 <序号>` · 管理:`/队伍` 查看")
        return "\n".join(lines)

    @filter.command("电脑", alias={"仓库", "箱子", "box", "storage"})
    async def cmd_box(self, event: AstrMessageEvent):
        """/电脑 —— 查看电脑仓库里的宝可梦"""
        t, err = self._require(event, in_battle_ok=True)
        if err:
            yield event.plain_result(err)
            return
        from .pw.player import MAX_PARTY

        parts = self._args(event, ("电脑", "仓库", "箱子", "box", "storage")).split()
        if parts and parts[0] in ("放生", "release"):
            if B.in_battle(t):
                yield event.plain_result(
                    "⚠️ 对战中不能放生 —— 先打完这场(出招/捕捉/逃跑)。"
                )
                return
            if len(parts) < 3 or parts[2] not in ("确认", "confirm", "yes"):
                yield event.plain_result(
                    "⚠️ 放生不可撤销。确认请输入:`/电脑 放生 <序号> 确认`"
                )
                return
            mon = t.release_pokemon(parts[1], where="box")
            if mon is None:
                yield event.plain_result(f"❌ 电脑里没有「{parts[1]}」。")
                return
            self._save(t)
            yield event.plain_result(
                f"👋 你放生了 {mon.display}(Lv{mon.level})。它回到了野外。"
            )
            return
        mons = self._box_payload(t)
        text = self._box_text(t, mons)
        async for r in self._emit_ui(
            event, "box",
            lambda: UI.render_box(mons, capacity=len(mons) + 0 or 30,
                                  money=t.money, scale=self._img_scale()),
            text=text,
            hint=f"取出:`/队伍 取出 <序号>`(队伍满 {MAX_PARTY} 只时先存一只)",
        ):
            yield r

    @filter.command("背包", alias={"bag", "道具"})
    async def cmd_bag(self, event: AstrMessageEvent):
        """/背包 —— 查看背包"""
        t, err = self._require(event, in_battle_ok=True)
        if err:
            yield event.plain_result(err)
            return
        items = t.bag_items()
        if not items:
            yield event.plain_result("背包是空的。")
            return
        pocket_arg, want_index, want_page = _parse_page_args(
            self._args(event, ("背包", "bag", "道具")), numeric_is_page=False
        )
        async with self._lock(t.scope):
            payload = self._bag_payload(t, pocket_arg, index=want_index, page=want_page)
            # 记住"正在看的分类"(写进存档):翻页/看第 N 件不带分类时继续用它。
            # 要落盘,否则重启后"上次看的分类"又丢了。
            if str(t.flag("bag_pocket", "") or "") != payload["pocket"]:
                t.set_flag("bag_pocket", payload["pocket"])
                self._save(t)
        # 文本回退:**按口袋过滤**(以前不管写哪个口袋都列全部,切分类等于没做),
        # 并在最上面列出各口袋数量,让"怎么切分类"一眼可见。
        groups = payload["groups"]
        counts = " · ".join(
            f"{label} {len(groups.get(pk) or [])}"
            + (f"({len(groups.get(pk) or [])} 种)" if False else "")
            for pk, label in UI.POCKETS
        )
        lines = [
            f"🎒 {t.name} 的背包({fmt_money(t.money)})",
            f"分类:{counts}",
            f"── {dict(UI.POCKETS).get(payload['pocket'], payload['pocket'])} ──",
        ]
        rows = payload["items"]
        if not rows:
            lines.append("这个分类是空的。")
        for i, row in enumerate(rows, 1):
            mark = "▶" if i - 1 == payload["selected"] else " "
            lines.append(
                f"{mark}{i}. {row['zh']} ×{row['count']}"
                + (f" —— {row['effect']}" if row.get("effect") else "")
                + (f"({row['desc']})" if row.get("desc") else "")
            )
        sel_row = rows[payload["selected"]] if rows else None
        if sel_row and (sel_row.get("effect") or sel_row.get("desc")):
            lines.append(
                f"详情:{sel_row['zh']} —— "
                + (sel_row.get("effect") or sel_row.get("desc") or "")
            )

        async for r in self._emit_ui(
            event, "bag",
            lambda: UI.render_bag(
                payload["items"], money=t.money, active_pocket=payload["pocket"],
                selected=payload["selected"], scale=self._img_scale(),
            ),
            text="\n".join(lines),
            hint="分类:`/背包 <分类>`(道具/精灵球/回复/招式机/重要)· "
                 "看第 N 件:`/背包 <分类> <序号>` · 翻页:`/背包 <分类> 页 <N>`",
        ):
            yield r

    @filter.command("地图", alias={"map", "地区", "地点"})
    async def cmd_map(self, event: AstrMessageEvent):
        """/地图 [地区] —— 查看地图与相邻地点"""
        t, err = self._require(event, in_battle_ok=True)
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
            hint=self._map_hint(t),
        ):
            yield r

    def _map_hint(self, t: Trainer) -> str:
        """地图图片附带的文本:相邻地点(带危险度)+ 本地服务 + 操作方式。

        图片画的是"整张地区图 + 当前位置 + 下一目标",**没有**相邻地点清单和
        城镇服务 —— 之前为了去掉重复文本把这两项一起删了(用户反馈
        "地图的城镇 服务 ... 额外的信息也给弄没了")。
        """
        world = WorldMap()
        ns = world.neighbors(t.location)
        near = "、".join(
            f"{world.node_zh(n)}(危险度{world.tier_label(n)})" for n in ns
        ) or "无"
        svc = "、".join(_service_zh(world.services(t.location))) or "无"
        return (
            f"◆ 相邻地点:{near}"
            f"\n◆ 本地服务:{svc}"
            "\n移动:`/前往 <地点>`(只能去相邻)、`/前往 飞行 <城镇>`"
            "(同地区 3 徽章解锁,500₽)"
        )

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
        """/探索 [野生|属性 <属性>|训练家|道具|事件] —— 有指向性地探索"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        if B.in_battle(t):
            yield event.plain_result("⚠️ 先把眼前的战斗打完:`/对战 <招式>`")
            return
        mode, want_type = _explore_target(
            self._args(event, ("探索", "explore", "遭遇", "搜索"))
        )
        if not mode:
            yield event.plain_result(_EXPLORE_USAGE)
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

            # ── 只想知道今天有什么事件:不掷骰、不开战 ──
            if mode == "event":
                self._save(t)
                yield event.plain_result(
                    "\n".join(
                        [
                            *notice,
                            f"📍 {world.region_zh(t.region)}·{world.node_zh(loc)}"
                            f"(危险度:{world.tier_label(loc)})",
                            D.today_brief(state, region=t.region, location=loc),
                        ]
                    )
                )
                return

            # ── 神兽定点:只有"想找野生"的目标才会被触发 ──
            # 想捡道具/看事件却被神兽拦住会很烦,所以按目标过滤。
            if mode in ("all", "wild"):
                site = (legendary.ready(t, world=world, day=state.day) or [None])[0]
                if site and want_type and want_type not in _species_types(site.get("species")):
                    site = None
                if site:
                    meta = legendary.legendary_meta(t, site)
                    log = B.start(
                        t, meta["team"], kind="legend", wild=True, meta=meta,
                        weather=_battle_weather(state, t.region), day=state.day,
                    )
                    self._save(t)
                    notice.append(f"🐉 传说的宝可梦出现了:{site['zh']} Lv{site['level']}!")
                    _hint = self._battle_hint(t)
                    async for r in self._emit_battle(
                        event, t, meta, log,
                        text="\n".join(notice) + "\n" + _hint,
                        keep="\n".join(notice) + "\n" + self._battle_intro(meta, log)
                        + "\n" + _hint,
                        status=True,
                    ):
                        yield r
                    return

            # ── 本地事件:火箭队(算训练家战);想找野生的目标不触发 ──
            if mode in ("all", "trainer") and ev and ev.get("kind") == "rocket":
                meta = npc.rocket_battle(t, ev)
                log = B.start(t, meta["team"], kind="rocket", meta=meta, day=state.day)
                self._save(t)
                notice.append(f"🚀 {EV.event_text(ev)}")
                _hint = self._battle_hint(t)
                async for r in self._emit_battle(
                    event, t, meta, log,
                    text="\n".join(notice) + "\n" + self._battle_intro(meta, log),
                    keep="\n".join(notice) + "\n" + self._battle_intro(meta, log)
                    + "\n" + _hint,
                    status=True,
                ):
                    yield r
                return

            # ── 普通探索掷骰 ──
            roll = rng.random()
            wild_p = 0.55 * float(mods.get("encounter_mult", 1.0))
            npc_p = 0.15
            if ev and ev.get("kind") == "swarm":
                wild_p += 0.2
            wild_p = min(0.85, wild_p)

            # ── 只想捡道具 ──
            if mode == "item":
                item = rng.choice(EXPLORE_ITEM_POOL)
                n = rng.randint(1, 2)
                t.add_item(item, n)
                self._save(t)
                zh = (BAG_ITEMS.get(item) or {}).get("zh", item)
                yield event.plain_result(
                    "\n".join([*notice, f"🔍 你在草丛里发现了 {zh} ×{n}!"])
                )
                return

            # ── 只想找训练家 ──
            if mode == "trainer":
                npcs = npc.route_trainers(t, loc, day=state.day)
                self._save(t)
                if npcs:
                    cand = npcs[0]
                    yield event.plain_result(
                        "\n".join(notice)
                        + f"\n👀 你看到一位训练家:{cand['name']}。"
                        f"\n用 `/训练家战` 发起挑战。"
                    )
                else:
                    yield event.plain_result(
                        "\n".join(notice)
                        + "\n👀 这条路上今天没有遇到训练家,换个地方或明天再来。"
                    )
                return

            # ── 只想找野生(可指定属性) ──
            if mode == "wild":
                env = _environment_of(world, loc)
                hit = self._roll_wild_typed(t, rng, env, want_type) if roll < wild_p else None
                if hit is None:
                    self._save(t)
                    head = f"🌿 附近没有{get_dex().type_label(want_type)}属性的宝可梦" if want_type else ""
                    tail = f"(这里能遇到:{'、'.join(_available_types(world, loc)) or '未知'})" if want_type else ""
                    yield event.plain_result(
                        "\n".join(
                            [*notice,
                             f"👀 你在附近转了一圈,什么也没遇到……{head}{tail}",
                             "可以再来一次 `/探索 野生`(每次都算 40 步)。"]
                        )
                    )
                    return
                hit = self._maybe_rare(t, state, ev, rng, hit)
                async for r in self._emit_wild(event, t, state, world, loc, hit, notice):
                    yield r
                return

            # ── 默认:什么都可能碰上(与以前完全一致) ──
            if roll < wild_p:
                env = _environment_of(world, loc)
                hit = B.roll_wild(t, rng=rng, environment=env)
                if not hit:
                    self._save(t)
                    yield event.plain_result(
                        "\n".join([*notice, "这里似乎什么也没有发生……"])
                    )
                    return
                hit = self._maybe_rare(t, state, ev, rng, hit)
                async for r in self._emit_wild(event, t, state, world, loc, hit, notice):
                    yield r
                return
            if roll < min(0.95, wild_p + npc_p):
                npcs = npc.route_trainers(t, loc, day=state.day)
                if npcs:
                    cand = npcs[0]
                    self._save(t)
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

    def _roll_wild_typed(self, t: Trainer, rng, environment: str,
                         want_type: str, *, tries: int = 12) -> dict | None:
        """掷一只野生宝可梦;**指定属性**时最多重掷 tries 次直到属性匹配。

        不做"过滤分布表"是有意的:地点分布是按真实版本数据来的,
        重掷等价于"在这片草丛里多找一会儿",命中率为分布的属性占比。
        """
        if not want_type:
            return B.roll_wild(t, rng=rng, environment=environment)
        for _ in range(tries):
            hit = B.roll_wild(t, rng=rng, environment=environment)
            if hit and want_type in _species_types(hit.get("species")):
                return hit
        return None

    def _maybe_rare(self, t: Trainer, state, ev, rng, hit: dict) -> dict:
        """罕见现身事件:有概率把普通遭遇换成稀有/传说宝可梦。"""
        if ev and ev.get("kind") == "rare" and rng.random() < 0.35 * float(
            state.modifiers.get("rare_mult", 1.0)
        ):
            legend = npc.legendary_at(t, ev)
            if legend:
                return legend
        return hit

    async def _emit_wild(self, event: AstrMessageEvent, t: Trainer, state, world,
                         loc: str, hit: dict, notice: list[str]):
        """野生遭遇开战(并把结构化信息交给委托系统)。"""
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
            weather=_battle_weather(state, t.region),
            day=state.day,
        )
        self._save(t)
        _hint = self._battle_hint(t)
        async for r in self._emit_battle(
            event, t, meta, log,
            text="\n".join(notice) + "\n" + self._battle_intro(meta, log)
            + f"\n\n{_hint}",
            keep="\n".join(notice) + "\n" + self._battle_intro(meta, log)
            + f"\n{_hint}",
            status=True,
        ):
            yield r

    @filter.command("对战", alias={"battle", "出招", "move"})
    async def cmd_battle(self, event: AstrMessageEvent):
        """/对战 <行动> —— 出招 / 换人 / 道具 / 逃跑"""
        t, err = self._require(event, in_battle_ok=True)
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
                weather=_battle_weather(state, t.region),
                day=state.day,
            )
            if not res.error and arg.strip().lower().startswith(("item", "道具")):
                # 只有真的用出去了才算(换人失败/道具无效时 take_turn 会给 error)。
                # 必须记在 meta 上:战斗结束时 take_turn 会把 data["battle"] 置空,
                # 记在会话里的话 _after_battle 再取就读不到了(导致"不用道具取胜"
                # 委托用过道具也算完成)。
                meta["used_item"] = True
                # 会话里也留一份(跨回合有效)。注意要**重新取一次**会话:
                # take_turn 结束时会用新字典替换 data["battle"],早先抓到的
                # _sess 已经失效,写进去会被丢掉。
                _cur_sess = B.session(t)
                if isinstance(_cur_sess, dict):
                    _cur_sess["meta"] = meta
            self._save(t)
            if res.error:
                yield event.plain_result(res.error + "\n\n" + B.status_text(t))
                return
            text = "\n".join(res.lines)
            if res.finished:
                # 图片里会有对话框(日志)与结果卡(胜负/奖励),所以图片路径只保留
                # 图片画不出来的部分:主线/神兽/大赛推进 + 委托完成 + LLM 叙事。
                after = self._after_battle(t, meta, res, state.day)
                text = after + "\n\n" + self._result_text(t, res) + "\n\n" + text
                text = await self._narrate(
                    "对战结束", res.lines + res.rewards + res.growth, text
                )
                keep = await self._narrate(
                    "对战结束", res.lines + res.rewards + res.growth, after,
                )
                async for r in self._emit_battle(
                    event, t, meta, res.lines,
                    text=text,
                    keep="\n".join(f"· {x}" for x in res.lines) + "\n" + keep,
                ):
                    yield r
                async for r in self._emit_result_cards(event, t, meta, res):
                    yield r
                # 画面都发完了,这时才清掉"已结束的对战数据"(它在渲染期间要留着,
                # 否则败北画面会退化成 我方=队伍第一只 / 敌方="？")
                B.clear_finished(t)
                self._save(t)
                return
            # 战斗画面的对话框只放得下 4 行,长战报会被截断 —— 所以完整战报
            # 再用文本发一遍(用户反馈"避免过长导致省略一部分")。
            log_text = "\n".join(f"· {x}" for x in res.lines)
            async for r in self._emit_battle(
                event, t, meta, res.lines,
                text=text, keep=log_text + "\n" + self._battle_hint(t), status=True,
            ):
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

    @filter.command("捕捉", alias={"catch", "投球"})
    async def cmd_catch(self, event: AstrMessageEvent):
        """/捕捉 <精灵球> —— 投球捕获"""
        t, err = self._require(event, in_battle_ok=True)
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
            err = _start_err(t)
            if err:
                yield event.plain_result(err)
                return
            meta = npc.build_route_battle(t, npcs[idx - 1], day=state.day)
            log = B.start(
                t,
                meta["team"],
                kind="trainer",
                meta=meta,
                weather=_battle_weather(state, t.region),
                day=state.day,
            )
            self._save(t)
            _hint = self._battle_hint(t)
            async for r in self._emit_battle(
                event, t, meta, log,
                text=self._battle_intro(meta, log) + "\n" + _hint,
                keep=self._battle_intro(meta, log) + "\n" + _hint,
                status=True,
            ):
                yield r

    @filter.command("道馆", alias={"gym", "馆主"})
    async def cmd_gym(self, event: AstrMessageEvent):
        """/道馆 [挑战] —— 查看/挑战道馆"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        world = WorldMap()
        # 少数地点有两个道馆(数据构建时缺城市被并入同一节点)。gym_at 只返回
        # 第一个,会导致第二个道馆的徽章永远拿不到 → 联盟要求全部徽章 → 冠军拿不到
        # → 下一个地区永久锁死。这里优先取"还没拿到徽章"的那个。
        _here = world.gyms_at(t.region, t.location)
        gym = next(
            (
                g
                for g in _here
                if f"{t.region}:{int(g.get('order', 0))}" not in t.badges
            ),
            None,
        ) or (_here[0] if _here else world.gym_at(t.region, t.location))
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
                err = _start_err(t)
                if err:
                    yield event.plain_result(err)
                    return
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
                _hint = self._battle_hint(t)
                async for r in self._emit_battle(
                    event, t, meta, log,
                    text=self._battle_intro(meta, log) + "\n" + _hint,
                    keep=self._battle_intro(meta, log) + "\n" + _hint,
                    status=True,
                ):
                    yield r
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
            hint="挑战:`/道馆 挑战`(需要馆主在场)",
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
                hint="挑战:`/联盟 挑战`(四天王连胜才能进冠军杯)",
            ):
                yield r
            return
        if B.in_battle(t):
            yield event.plain_result("⚠️ 先结束当前对战。")
            return
        # 主线必须按序推进:不能跳过"击退敌对组织"等章节直接通关联盟。
        # 注意只拦"挑战",看板(纯信息展示)照常显示。
        cur = story.current_stage(t)
        if cur and cur.get("kind") != "league":
            need = int(cur.get("need") or 0)
            loc_key = str(cur.get("location") or "")
            if cur.get("kind") == "badge" and need > t.badge_count(region):
                todo = f"先获得 {need} 枚徽章(当前 {t.badge_count(region)} 枚)"
            elif loc_key:
                todo = f"前往 {world.node_zh(loc_key)} 用 `/主线 挑战`"
            else:
                todo = "用 `/主线 挑战` 开战"
            yield event.plain_result(
                f"❌ 主线还没推进到联盟 —— 当前章节「{cur.get('title')}」:{todo}。"
                "输入 `/主线` 查看下一步。"
            )
            return
        err = _start_err(t)
        if err:
            yield event.plain_result(err)
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
                weather=_battle_weather(state, region),
                day=state.day,
            )
            self._save(t)
            _hint = self._battle_hint(t)
            async for r in self._emit_battle(
                event, t, meta, log,
                text=self._battle_intro(meta, log) + "\n" + _hint,
                keep=self._battle_intro(meta, log) + "\n" + _hint,
                status=True,
            ):
                yield r

    @filter.command("商店", alias={"shop", "购买"})
    async def cmd_shop(self, event: AstrMessageEvent):
        """/商店 [买|卖 <道具> [数量]] —— 商店"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        # 对战中禁止买卖:战斗每回合会把开战时的队伍/背包快照写回存档
        # (_sync),战斗中的改动会被回滚 —— 买东西会"钱货两失",卖东西则是
        # 空手套白狼。和 /治疗 /前往 一样,这里直接拒绝。
        if B.in_battle(t):
            yield event.plain_result("⚠️ 对战中不能买卖,先结束当前对战。")
            return
        # 任何指令都应懒刷新游戏日(设计约定);否则只逛商店的玩家会一直用着
        # 已经过期的世界事件折扣。
        async with self._lock(t.scope):
            await self._ensure_day(event, t)
            t = self._load(event) or t
        world = WorldMap()
        if "mart" not in world.services(t.location):
            yield event.plain_result("❌ 这里没有商店,去城镇(宝可梦中心所在地)吧。")
            return
        state = self._state(t.scope)
        discount = float(state.modifiers.get("shop_discount", 1.0))
        arg = self._args(event, ("商店", "shop", "购买")).strip()
        tokens = arg.split()
        action = tokens[0] if tokens else ""
        # **先判断买/卖**:否则 `/商店 买 伤药 2` 里的数字会被当成"看第 2 件"
        if action in ("买", "buy", "卖", "sell") and len(tokens) >= 2:
            pass
        else:
            # 只看不买:`/商店 12`(第 12 件)或 `/商店 页 4` —— 满徽章时货架 80+ 种
            _, want_index, want_page = _parse_page_args(
                arg, numeric_is_page=(action in ("页", "page"))
            )
            if not arg:
                # 不带参数时沿用上次翻到的那一页(存玩家身上),别每次都跳回第 1 页
                want_page = max(1, coerce_int(t.flag("shop_page", 1), 1))
            entries = self._shop_payload(t, discount)
            per = max(1, int(UIM.SHOP_PER_PAGE))
            if want_index > 0:
                sel = min(want_index - 1, len(entries) - 1)
            elif want_page > 1:
                sel = min((want_page - 1) * per, len(entries) - 1)
            else:
                sel = 0
            # 记住这次看的页(按选中的那件反推),下次 `/商店` 直接续上
            page_now = max(1, sel // per + 1) if entries else 1
            if int(coerce_int(t.flag("shop_page", 1), 1)) != page_now:
                async with self._lock(t.scope):
                    t.set_flag("shop_page", page_now)
                    self._save(t)
            text = self._shop_text(t, discount)
            async for r in self._emit_ui(
                event, "shop",
                lambda: UIM.render_shop(
                    entries, money=t.money,
                    location_zh=world.node_zh(t.location), discount=discount,
                    selected=max(0, sel), scale=self._img_scale(),
                ),
                text=text,
                hint="买卖:`/商店 买 <道具> [数量]`、`/商店 卖 <道具> [数量]` · "
                     "看第 N 件:`/商店 <序号>` · 翻页:`/商店 页 <N>`",
            ):
                yield r
            return
        if (action in ("买", "buy") or action in ("卖", "sell")) and len(tokens) >= 2:
            name = tokens[1]
            n = coerce_int(tokens[2], 1) if len(tokens) > 2 else 1
        else:
            yield event.plain_result(
                "❌ 用法:`/商店 买 <道具> [数量]`、`/商店 卖 <道具> [数量]`、"
                "`/商店 <序号>`(看第 N 件)、`/商店 页 <N>`(翻页)"
            )
            return
        n = int(clamp(n or 1, 1, 99))
        stock = set(world.shop_stock(t.location, t.badge_count()))
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
        if B.in_battle(t):
            yield event.plain_result("⚠️ 对战中不能学招式,先结束当前对战。")
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
        # 必须查学习表:否则 Lv5 的鲤鱼王也能学会大字爆炎(绕过等级与可学表)。
        # 等级升级时待学的招式本来就在 learnable 里,所以这里一并覆盖。
        _mr0 = get_dex().resolve_move(tokens[1])
        _mr0 = str(_mr0[0]) if _mr0 else ""
        _known = {
            str(x.get("move") or x.get("key") or "")
            for x in get_dex().learnable(
                mon.species, mon.level, include_tm=True, include_tutor=True
            )
        }
        if not _mr0 or _mr0 not in _known:
            yield event.plain_result(
                f"❌ {mon.display} 学不会「{tokens[1]}」(不在它的可学表里,或等级还不够)。"
            )
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
                f"⚠️ {mon.display} 已会 4 个招式 —— 要学的「{growth.move_brief(mr[0])}」"
                "需替换掉下面一个:\n"
                + "\n".join(
                    f"{i}. {growth.move_brief(m)}" for i, m in enumerate(mon.moves, 1)
                )
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
            if not growth.replace_move(mon, old, mr[0]):
                yield event.plain_result(
                    f"❌ 「{growth.move_zh(mr[0])}」已经会了,不用替换。"
                )
                return
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
            # 待替换的招式也要能看出"这招做什么",否则玩家没法决定要不要换
            yield event.plain_result(
                "仍待决定(用 `/学招 "
                f"{idx} <招式> 替换 <序号>` 决定):\n"
                + "\n".join(f"· {growth.move_brief(m)}" for m in pending)
            )

    @filter.command("进化", alias={"evolve"})
    async def cmd_evolve(self, event: AstrMessageEvent):
        """/进化 <队伍序号> [道具] —— 查看/执行进化"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        if B.in_battle(t):
            yield event.plain_result("⚠️ 对战中不能进化,先结束当前对战。")
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
            opts = dex.use_item_evolutions(mon.species, key, gender=mon.gender)
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

    # ── 玩家间交换:报价的存与取 ──────────────────────────────
    def _trade_box(self, state) -> dict:
        """取出本群的交换报价表,顺手清掉过期/超量的。

        存在**世界状态**里(同群的两个人共享一份),而不是某个玩家的存档里 ——
        报价天然是"跨玩家"的数据。
        """
        box = state.data.setdefault("trades", {})
        if not isinstance(box, dict):
            box = {}
            state.data["trades"] = box
        now = time.time()
        for k, v in list(box.items()):
            if not isinstance(v, dict) or float(v.get("at") or 0) + TRADE_TTL < now:
                box.pop(k, None)
        if len(box) > TRADE_MAX_OFFERS:          # 只留最新的若干条,防刷
            for k in sorted(box, key=lambda x: float(box[x].get("at") or 0))[
                : len(box) - TRADE_MAX_OFFERS
            ]:
                box.pop(k, None)
        return box

    def _trade_offers_for(self, state, uid: str) -> list[dict]:
        """待某人回应的报价(新的在前)。"""
        box = self._trade_box(state)
        return sorted(
            [v for v in box.values() if isinstance(v, dict) and v.get("to") == uid],
            key=lambda v: float(v.get("at") or 0),
            reverse=True,
        )

    def _store_mon(self, trainer: Trainer, row: dict, mon: Pokemon) -> None:
        """把(可能刚进化过的)宝可梦写回它在**队伍或电脑**里的槽位。"""
        where, i = _find_mon_slot(trainer, str(row.get("id") or ""))
        if i < 0:
            return
        if where == "party":
            trainer.commit(i, mon)
        else:
            trainer.box[i] = mon_to_dict(mon, trainer.box[i])

    def _trade_evo(self, mon: Pokemon, party=()) -> str:
        """收到宝可梦时的通信进化(含消耗携带道具),返回新物种 key 或 ""。"""
        dex = get_dex()
        opts = [
            o
            for o in dex.evolution_options(
                mon.species, level=mon.level, moves=set(mon.moves),
                item=mon.item or None, friendship=mon.friendship,
                gender=mon.gender, stats=mon.stats, trade=True, party=party,
            )
            if o.get("kind") == "trade" and o.get("met")
        ]
        if not opts:
            return ""
        target = str(opts[0]["target"])
        req = str((dex.species.get(target) or {}).get("evoItem") or "")
        if mon.item and req and dex.item_matches(str(mon.item), req):
            mon.item = ""            # 通信进化消耗携带道具
        return growth.apply_evolution(mon, target)

    @filter.command("交换", alias={"trade", "连接交换", "通讯交换"})
    async def cmd_trade(self, event: AstrMessageEvent):
        """`/交换 <@对方> <序号>` 发起 · `/交换 接受 [序号]` · `/交换 拒绝` ·
        `/交换 <序号>` 与远方训练家连接交换(自连,只触发通信进化)"""
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        if B.in_battle(t):
            yield event.plain_result("⚠️ 对战中不能交换,先结束当前对战。")
            return
        world = WorldMap()
        arg = self._args(event, ("交换", "trade", "连接交换", "通讯交换")).strip()
        parts = arg.split()
        ats = _at_users(event)
        head = parts[0].lower() if parts else ""
        state = self._state(t.scope)

        # ── 接受 ──
        if head in ("接受", "accept", "同意", "ok", "yes"):
            async with self._lock(t.scope):
                offers = self._trade_offers_for(state, t.uid)
                if ats:
                    offers = [o for o in offers if o.get("from") == ats[0][0]]
                if not offers:
                    yield event.plain_result(
                        "❌ 没有等你回应的交换请求。"
                        "(对方要用 `/交换 @你 <他的宝可梦序号>` 发起)"
                    )
                    return
                offer = offers[0]
                idx = coerce_int(parts[1], 1) if len(parts) > 1 else 1
                if not 1 <= idx <= len(t.party):
                    yield event.plain_result(f"❌ 队伍里没有第 {idx} 只。")
                    return
                if "center" not in world.services(t.location):
                    yield event.plain_result(
                        "❌ 回应交换要在宝可梦中心进行。"
                    )
                    return
                box = self._trade_box(state)
                key = _trade_key(str(offer.get("from")), t.uid)
                box.pop(key, None)
                other_data = self.trainers.load(t.scope, str(offer.get("from")))
                if not other_data:
                    self._save_state(state)
                    yield event.plain_result("❌ 对方的存档已经不存在了,请求已取消。")
                    return
                other = Trainer(other_data, uid=str(offer.get("from")), scope=t.scope)
                where, oi = _find_mon_slot(other, str(offer.get("mon_id")))
                if oi < 0:
                    self._save_state(state)
                    yield event.plain_result(
                        "❌ 对方要交换的宝可梦已经不在 TA 的队伍/电脑里了,请求已取消。"
                    )
                    return
                their_rows = other.party if where == "party" else other.box
                mine = B.dict_to_mon(t.party[idx - 1])
                theirs = B.dict_to_mon(their_rows[oi])
                my_label = f"{mine.display} Lv{mine.level}"
                their_label = f"{theirs.display} Lv{theirs.level}"
                del their_rows[oi]
                del t.party[idx - 1]
                # 入队(队伍满 6 自动进电脑);返回的是**新 dict**(id 是新分配的)
                recv_for_me = t.add_pokemon(theirs, day=state.day)
                recv_for_them = other.add_pokemon(mine, day=state.day)
                # 通信进化要作用在**收到的那只 Pokemon 对象**上,再写回它所在的槽位 ——
                # 之前这里进化的是临时对象,commit 的又是另一个临时对象,进化被丢掉了。
                evo_mine = self._trade_evo(
                    theirs, party=tuple(p.get("species") for p in t.party)
                )
                self._store_mon(t, recv_for_me, theirs)
                evo_theirs = self._trade_evo(
                    mine, party=tuple(p.get("species") for p in other.party)
                )
                self._store_mon(other, recv_for_them, mine)
                my_slot = _find_mon_slot(t, str(recv_for_me.get("id") or ""))[0]
                self._save(t)
                self._save(other)
                self._save_state(state)
            lines = [f"🔁 交换成功!你送出了 {my_label},收到了 {their_label}。"]
            if evo_mine:
                lines.append(
                    f"　└ ✨ 通信进化的力量让 {their_label} 进化成了 "
                    f"{growth.species_zh(evo_mine)}!"
                )
            if evo_theirs:
                lines.append(
                    f"　└ ✨ 对方收到的 {my_label} 也进化成了 "
                    f"{growth.species_zh(evo_theirs)}!"
                )
            if my_slot == "box":
                lines.append("　└ 队伍满了,收到的宝可梦先进了电脑(`/队伍 取出`)。")
            yield event.plain_result("\n".join(lines))
            return

        # ── 拒绝 / 取消 ──
        if head in ("拒绝", "reject", "no", "取消", "cancel", "撤回"):
            async with self._lock(t.scope):
                box = self._trade_box(state)
                removed = []
                for k, v in list(box.items()):
                    if not isinstance(v, dict):
                        continue
                    mine_out = v.get("from") == t.uid
                    mine_in = v.get("to") == t.uid
                    if ats and not ((mine_out and v.get("to") == ats[0][0]) or
                                    (mine_in and v.get("from") == ats[0][0])):
                        continue
                    if mine_out or mine_in:
                        box.pop(k, None)
                        removed.append(v)
                self._save_state(state)
            if not removed:
                yield event.plain_result("❌ 没有可取消的交换请求。")
                return
            who = removed[0].get("from_name") or removed[0].get("from")
            act = "撤销了发给" if removed[0].get("from") == t.uid else "回绝了"
            yield event.plain_result(f"🚫 你已{act} {who} 的交换请求。")
            return

        # ── 发起:必须 @ 到人 ──
        if ats:
            nums = [p for p in parts if p.isdigit()]
            idx = coerce_int(nums[0], 0) if nums else 0
            target_uid, target_name = ats[0]
            if target_uid == t.uid:
                yield event.plain_result("⚠️ 不能和自己交换(自己练不就好了)。")
                return
            if not 1 <= idx <= len(t.party):
                yield event.plain_result(
                    "❌ 用法:`/交换 @对方 <你的宝可梦序号>` —— 序号是**你自己的**队伍序号。"
                )
                return
            if "center" not in world.services(t.location):
                yield event.plain_result("❌ 连接交换要在宝可梦中心进行。")
                return
            if not self.trainers.exists(t.scope, target_uid):
                yield event.plain_result(
                    "❌ 对方还没有在玩(用 `/开始` 建过存档),或者不在本群。"
                )
                return
            mon = t.mon(idx - 1)
            if mon is None:
                yield event.plain_result("❌ 队伍序号不对。")
                return
            async with self._lock(t.scope):
                box = self._trade_box(state)
                box[_trade_key(t.uid, target_uid)] = {
                    "from": t.uid,
                    "to": target_uid,
                    "from_name": t.name,
                    "mon_id": str(t.party[idx - 1].get("id") or ""),
                    "zh": mon.display,
                    "level": int(mon.level),
                    "item": self._held_zh(str(mon.item or "")),
                    "at": time.time(),
                }
                self._save_state(state)
            held = self._held_zh(str(mon.item or ""))
            yield event.plain_result(
                f"📨 已向 {target_name or '对方'} (@{target_uid}) 发起交换:"
                f"送出你的 **{mon.display} Lv{mon.level}**"
                + (f"(携带 {held})" if held else "")
                + f"。\n对方用 `/交换 接受 <TA 的宝可梦序号>` 即可完成"
                f"(请在宝可梦中心)。{TRADE_TTL // 60} 分钟内有效。"
            )
            return

        # ── 自连:纯序号,触发通信进化(老行为) ──
        if not arg:
            pending = self._trade_offers_for(state, t.uid)
            lines = [
                "用法:",
                "· `/交换 @对方 <你的序号>` 向同群玩家发起交换",
                "· `/交换 接受 <你的序号>` 回应请求、`/交换 拒绝` 回绝",
                "· `/交换 <序号>` 与远方训练家连接交换(只触发通信进化)",
            ]
            if pending:
                o = pending[0]
                lines.insert(
                    0,
                    f"📨 {o.get('from_name') or o.get('from')} 想用 "
                    f"{o.get('zh')} Lv{o.get('level')} 换你的宝可梦 —— "
                    f"`/交换 接受 <你的序号>`",
                )
            yield event.plain_result("\n".join(lines))
            return
        idx = coerce_int(parts[0], 1) or 1
        async with self._lock(t.scope):
            mon = t.mon(idx - 1)
            if mon is None:
                yield event.plain_result("❌ 队伍序号不对。")
                return
            if "center" not in world.services(t.location):
                yield event.plain_result("❌ 连接交换要在宝可梦中心进行。")
                return
            dex = get_dex()
            # **必须把携带物传进去**:以前漏了 item 又没看 met,导致"需要携带道具"
            # 的 16 种通信进化(大岩蛇→大钢蛇要金属膜等)空手也能进化,道具要求形同虚设;
            # 珍珠贝那种双分支也永远只走第一个(只能拿猎斑鱼,拿不到樱花鱼)。
            opts = [
                o
                for o in dex.evolution_options(
                    mon.species, level=mon.level, moves=set(mon.moves),
                    item=mon.item or None,
                    friendship=mon.friendship, gender=mon.gender, stats=mon.stats,
                    trade=True,
                )
                if o.get("kind") == "trade"
            ]
            ready = [o for o in opts if o.get("met")]
            if not opts:
                yield event.plain_result(
                    f"⚠️ {mon.display} 通过连接交换也不会进化"
                    "(通信进化只对胡地/耿鬼/怪力/大岩蛇这类有效)。"
                )
                return
            if not ready:
                # 差携带道具:直接告诉玩家要带什么(商店 5 徽章档有卖)
                needs: list[str] = []
                for o in opts:
                    req = str((dex.species.get(str(o.get("target"))) or {}).get("evoItem") or "")
                    if req:
                        zh = self._held_zh(req) or req
                        if zh not in needs:
                            needs.append(zh)
                tip = "或".join(needs) if needs else "特定道具"
                yield event.plain_result(
                    f"⚠️ {mon.display} 要携带 {tip} 才能通过连接交换进化"
                    f"(用 `/持有 {tip.split('或')[0]} {idx}` 装上再 `/交换 {idx}`)。"
                )
                return
            old = mon.species
            target = str(ready[0]["target"])
            # 通信进化会消耗掉那件携带道具(和原作一致)
            used_item = str(mon.item or "")
            req_item = str((dex.species.get(target) or {}).get("evoItem") or "")
            if used_item and req_item and dex.item_matches(used_item, req_item):
                mon.item = ""
            growth.apply_evolution(mon, target)
            t.commit(idx - 1, mon)
            self._save(t)
        yield event.plain_result(
            f"🔁 你与远方训练家完成了连接交换 —— {growth.species_zh(old)} 进化成了 "
            f"{growth.species_zh(target)}!"
        )

    @filter.command("使用", alias={"use", "用道具"})
    async def cmd_use(self, event: AstrMessageEvent):
        """`/使用 <道具> [队伍序号] [招式序号]` —— 战斗外使用道具。

        以前这里**只认神奇糖果**(`effect.level_up`),于是伤药/万灵药/活力碎片/
        PP 恢复/树果这些在战斗外一律被拒绝 —— 玩家只能跑宝可梦中心才能回血,
        背包里的 38 种回复类道具等于废纸。
        """
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        if B.in_battle(t):
            yield event.plain_result("⚠️ 对战中请用 `/对战 item <道具>`。")
            return
        arg = self._args(event, ("使用", "use", "用道具")).strip()
        tokens = arg.split()
        if not tokens:
            yield event.plain_result(
                "用法:`/使用 <道具> [队伍序号] [招式序号]`\n"
                "· 回复/状态/复活/PP:`/使用 伤药 1`、`/使用 万灵药 2`\n"
                "· 神奇糖果:`/使用 神奇糖果 1`\n"
                "· 其余:`/持有 <道具> [序号]` 装备、`/进化 <序号> <道具>` 进化、"
                "`/对战 item <道具>` 战斗中用药"
            )
            return
        from .pw.items import (
            ITEMS,
            apply_out_of_battle,
            item_needed,
            resolve_bag_item,
        )

        # 参数:名字 + (可选)序号 + (可选)招式序号
        nums = [p for p in tokens if p.isdigit()]
        name = " ".join(p for p in tokens if not p.isdigit())
        if not name:
            yield event.plain_result("❌ 请写道具名字,例如 `/使用 伤药 1`。")
            return
        r = resolve_bag_item(name)
        if not r or t.count(r[0]) <= 0:
            yield event.plain_result(f"❌ 背包里没有「{name}」。")
            return
        key, entry = r
        eff = entry.get("effect") or {}
        num = coerce_int(nums[0], 1) if nums else 0
        move_idx = coerce_int(nums[1], 0) if len(nums) > 1 else 0

        # ① 神奇糖果:直接补足到下一级所需经验
        if eff.get("level_up"):
            num = num or 1
            mon = t.mon(num - 1)
            if mon is None:
                yield event.plain_result(f"❌ 队伍里没有第 {num} 只。")
                return
            dex = get_dex()
            if mon.level >= 100:
                yield event.plain_result(f"⚠️ {mon.display} 已经是 Lv100 了。")
                return
            rate = dex.growth_of(mon.species)
            need = max(1, dex.exp_for_level(rate, mon.level + 1) - mon.exp)
            res = growth.gain_exp(mon, need, daytime=B.daytime_of())
            t.take_item(key, 1)
            t.commit(num - 1, mon)
            self._save(t)
            lines = [f"🍬 {mon.display} 使用了 {entry['zh']},升到了 Lv{mon.level}!"]
            # 每条都带名字与招式效果:只写名字看不出这招做什么,也不该让玩家
            # 去猜"这一行是谁学的"
            lines.extend(
                f"　└ {mon.display} 学会了「{growth.move_brief(mv)}」!"
                for mv in res.learned
            )
            lines.extend(
                f"　└ {mon.display} 想学「{growth.move_brief(mv)}」"
                "(招式已满,用 `/学招` 替换)"
                for mv in res.pending
            )
            if res.evolved_to:
                lines.append(
                    f"　└ ✨ {mon.display} 进化成了 {growth.species_zh(res.evolved_to)}!"
                )
            yield event.plain_result("\n".join(lines))
            return

        # ② 回复 / 状态 / 复活 / PP(战斗外也能用)
        healable = {
            "heal_hp", "heal_hp_frac", "heal_full", "cure_status",
            "revive", "revive_full", "pp_restore", "pp_restore_all", "pp_all",
        }
        if set(eff) & healable:
            # 没写序号时自动挑"最该治"的一只(记下标,后面要写回存档)
            picked: tuple[int, Pokemon] | None = None
            if num:
                cand = t.mon(num - 1)
                if cand is not None and item_needed(cand, eff):
                    picked = (num - 1, cand)
            else:
                for i in range(len(t.party)):
                    cand = t.mon(i)
                    if cand is not None and item_needed(cand, eff):
                        picked = (i, cand)
                        break
            if picked is None:
                wt = f"第 {num} 只" if num else "队伍里的宝可梦都"
                yield event.plain_result(
                    f"⚠️ {wt}用不上 {entry['zh']} —— 满血且状态正常时不必用。"
                )
                return
            slot, target = picked
            mv = ""
            if move_idx:
                if not target.moves or not 1 <= move_idx <= len(target.moves):
                    yield event.plain_result(
                        f"❌ {target.display} 没有第 {move_idx} 个招式。"
                    )
                    return
                mv = target.moves[move_idx - 1]
            ok, line = apply_out_of_battle(target, key, move=mv or None)
            if not ok:
                yield event.plain_result(f"⚠️ 现在用不了 {entry['zh']}。")
                return
            # **必须写回存档**:t.mon() 每次都是新解析出来的对象,
            # 只改这个临时对象等于没治(这个坑我今天已经踩第二次了)。
            t.commit(slot, target)
            t.take_item(key, 1)
            self._save(t)
            yield event.plain_result(
                f"{line}\n　└ 用掉了 {entry['zh']} ×1,还剩 {t.count(key)} 个。"
            )
            return

        # ③ PP 上限提升(PP 提升剂 / PP 极限提升剂)
        if eff.get("pp_up"):
            num = num or 1
            mon = t.mon(num - 1)
            if mon is None:
                yield event.plain_result(f"❌ 队伍里没有第 {num} 只。")
                return
            if not mon.moves:
                yield event.plain_result(f"⚠️ {mon.display} 还没有招式。")
                return
            if not move_idx:
                yield event.plain_result(
                    f"❓ 要给哪个招式提升?用法:`/使用 {entry['zh']} {num} <招式序号>`"
                    "(先 `/招式 " + str(num) + "` 看序号)"
                )
                return
            if not 1 <= move_idx <= len(mon.moves):
                yield event.plain_result(f"❌ {mon.display} 没有第 {move_idx} 个招式。")
                return
            from .pw.items import PP_UP_MAX

            mv = mon.moves[move_idx - 1]
            bonus = int((mon.pp_bonus or {}).get(mv, 0) or 0)
            base = max_pp(mon, mv) - bonus
            want_max = bool(eff.get("pp_up_all")) or int(eff.get("pp_up") or 1) > 1
            # PP 极限提升剂直接顶到 +3(上限),普通提升剂 +1
            step = PP_UP_MAX - bonus if want_max else 1
            if step <= 0 or (not want_max and bonus >= PP_UP_MAX):
                yield event.plain_result(
                    f"⚠️ {growth.move_zh(mv)} 的 PP 上限已经提升到极限了"
                    f"({base} → {base + bonus})。"
                )
                return
            add = min(step, PP_UP_MAX - bonus)
            mon.pp_bonus = dict(mon.pp_bonus or {})
            mon.pp_bonus[mv] = bonus + add
            mon.pp[mv] = int(mon.pp.get(mv, 0) or 0) + add   # 加的上限立刻可用
            t.take_item(key, 1)
            t.commit(num - 1, mon)
            self._save(t)
            yield event.plain_result(
                f"⚙️ {mon.display} 的「{growth.move_zh(mv)}」PP 上限 "
                f"{base + bonus} → {base + bonus + add}!"
                + (f"(已到上限 +{PP_UP_MAX})" if bonus + add >= PP_UP_MAX else "")
            )
            return

        # ④ 特性切换(特性胶囊 = 换成另一个普通特性 / 特性膏药 = 换成隐藏特性)
        if eff.get("ability_switch") or eff.get("ability_patch"):
            num = num or 1
            mon = t.mon(num - 1)
            if mon is None:
                yield event.plain_result(f"❌ 队伍里没有第 {num} 只。")
                return
            dex = get_dex()
            slots = dex.ability_options(mon.species)

            def _ab_key(name: str) -> str:
                """特性槽位存的是**显示名**(Run Away),而 mon.ability 是 key(runaway),
                不归一化的话"换个普通特性"会挑到同一个。"""
                r = dex.resolve_ability(name) if name else None
                return str(r[0]) if r else ""

            cur_raw = str(mon.ability or _ab_key(slots.get("0") or "") or "")
            cur = _ab_key(cur_raw) or cur_raw
            want_hidden = bool(eff.get("ability_patch"))
            if want_hidden:
                target_ab = _ab_key(slots.get("H") or "")
                if not target_ab:
                    yield event.plain_result(
                        f"⚠️ {mon.display}({dex.species.get(mon.species, {}).get('zh')})"
                        "没有隐藏特性。"
                    )
                    return
            else:
                normals = [_ab_key(slots[k]) for k in ("0", "1") if slots.get(k)]
                others = [a for a in normals if a and a != cur]
                if not others:
                    yield event.plain_result(
                        f"⚠️ {mon.display} 没有另一个普通特性可换。"
                    )
                    return
                target_ab = others[0]
            if target_ab == cur:
                yield event.plain_result(f"⚠️ {mon.display} 已经是这个特性了。")
                return
            old_ab, old_zh = cur, self._ability_zh(cur)
            mon.ability = target_ab
            t.take_item(key, 1)
            t.commit(num - 1, mon)
            self._save(t)
            yield event.plain_result(
                f"🧬 {mon.display} 的特性:{old_zh or old_ab} → "
                f"{self._ability_zh(target_ab) or target_ab}!"
            )
            return

        # ⑤ 其余:给出正确入口,而不是含糊地说"不能这样用"
        tips = []
        if set(eff) & {"evolve_stone", "evolve_item"}:
            tips.append("进化用 `/进化 <序号> <道具>`")
        if set(eff) & {"ball_master", "ball_bonus"} or str(entry.get("kind")) == "ball":
            tips.append("球类在 `/对战` 里用 `/捕捉 <球>` 投出")
        if set(eff) & {"stat_boost", "focus_energy", "guard_spec"}:
            tips.append("强化剂只能在战斗中 `/对战 item <道具>` 使用")
        if set(eff) & {"pp_up", "ability_switch", "ability_patch"}:
            tips.append("这件道具的效果本插件还没实现")
        if key in ITEMS:
            tips.append("这是持有道具,用 `/持有 <道具> [序号]` 装备")
        yield event.plain_result(
            f"⚠️ {entry.get('zh', key)} 不能在战斗外这样使用。"
            + ("(" + ";".join(tips) + ")" if tips else "")
        )

    @filter.command("持有", alias={"携带", "装备", "hold", "item", "持有物"})
    async def cmd_hold(self, event: AstrMessageEvent):
        """`/持有 <道具> [序号]` / `/持有 取下 [序号]` —— 给宝可梦携带道具。

        在此之前 `mon.item` **只被读取、从来没有被写入**:剩饭/讲究头带/
        进化奇石/气势披带这些持有道具效果引擎里都实现了,玩家却拿不到,
        `levelHold`(携带升级进化)也永远不可达。
        """
        t, err = self._require(event)
        if err:
            yield event.plain_result(err)
            return
        from .pw.items import ITEMS, resolve_bag_item, resolve_item

        arg = self._args(event, ("持有", "携带", "装备", "hold", "item", "持有物")).strip()
        parts = arg.split()
        if not arg:
            lines = ["🎒 队伍携带情况(用 `/持有 <道具> <序号>` 装备、`/持有 取下 <序号>` 取下)"]
            for i, p in enumerate(t.party, 1):
                mon = B.dict_to_mon(p)
                held = self._held_zh(str(mon.item or "")) or "无"
                lines.append(f"{i}. {mon.display} Lv{mon.level} —— 持有:{held}")
            yield event.plain_result("\n".join(lines))
            return
        async with self._lock(t.scope):
            # 取下
            if parts[0] in ("取下", "卸下", "无", "拿掉", "none", "remove"):
                num = coerce_int(parts[1], self._current_mon_index(t) + 1) if len(parts) > 1 \
                    else self._current_mon_index(t) + 1
                mon = t.mon(num - 1)
                if mon is None:
                    yield event.plain_result("❌ 队伍序号不对。")
                    return
                if not mon.item:
                    yield event.plain_result(f"⚠️ {mon.display} 没有携带任何道具。")
                    return
                back = str(mon.item)
                mon.item = ""
                t.commit(num - 1, mon)
                t.add_item(back, 1)
                self._save(t)
                yield event.plain_result(
                    f"🎒 已从 {mon.display} 取下 {self._held_zh(back)},放回背包。"
                )
                return
            r = resolve_bag_item(parts[0]) or resolve_item(parts[0])
            if not r:
                yield event.plain_result(f"❌ 没有找到道具「{parts[0]}」。")
                return
            key, entry = r
            # 只有"携带后真的有作用"的道具才允许装(持有类效果 / 携带进化)
            if key not in ITEMS:
                yield event.plain_result(
                    f"⚠️ {entry.get('zh', key)} 是消耗品,携带没有效果。"
                    "(回复/球类请在 `/对战` 里用,进化石用 `/进化 <序号> <道具>`)"
                )
                return
            num = coerce_int(parts[1], self._current_mon_index(t) + 1) if len(parts) > 1 \
                else self._current_mon_index(t) + 1
            mon = t.mon(num - 1)
            if mon is None:
                yield event.plain_result(f"❌ 队伍里没有第 {num} 只。")
                return
            if str(mon.item or "") == key:
                yield event.plain_result(f"⚠️ {mon.display} 已经携带了 {entry['zh']}。")
                return
            if mon.item:
                old_zh = self._held_zh(str(mon.item))
                yield event.plain_result(
                    f"⚠️ {mon.display} 正携带 {old_zh},先 `/持有 取下 {num}` 再换。"
                )
                return
            if not t.take_item(key, 1):
                # 不在背包里:可能在别的宝可梦身上
                holder = next(
                    (i for i, p in enumerate(t.party, 1)
                     if str(p.get("item") or "") == key),
                    None,
                )
                tip = f"(第 {holder} 只正携带它)" if holder else ""
                yield event.plain_result(f"❌ 背包里没有 {entry['zh']}{tip}。")
                return
            mon.item = key
            t.commit(num - 1, mon)
            self._save(t)
            eff = effect_text(key)
            yield event.plain_result(
                f"✅ {mon.display} 开始携带 {entry['zh']}"
                + (f" —— {eff}" if eff else "") + "。"
            )

    @filter.command("图鉴", alias={"dex", "宝可梦图鉴"})
    async def cmd_dex(self, event: AstrMessageEvent):
        """/图鉴 <名字> —— 图鉴资料"""
        t, err = self._require(event, in_battle_ok=True)
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
            hint=self._dex_hint(key, entry, locs, evos),
        ):
            yield r

    def _dex_hint(self, key: str, entry: dict, locs: list, evos: list) -> str:
        """图鉴图片附带的文本:进化与野外分布(图片里只写"已收录")。"""
        lines = []
        if evos:
            lines.append("可进化为:" + "、".join(str(x) for x in evos))
        if locs:
            world = WorldMap()
            names = [world.node_zh(str(x)) if not isinstance(x, str) else x
                     for x in locs[:8]]
            lines.append("野外分布:" + "、".join(names))
        evo_entry = entry.get("evos") or []
        if not evos and not evo_entry:
            lines.append("不会进化")
        lines.append("行动:`/探索` 前进、`/队伍` 查看队伍、`/帮助` 全指令")
        return "\n".join(lines)

    @filter.command("今日", alias={"today", "事件", "世界动态"})
    async def cmd_today(self, event: AstrMessageEvent):
        """/今日 —— 今日世界与个人事件"""
        t, err = self._require(event, in_battle_ok=True)
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
                narration_facts(
                    "今日世界速报", facts, extra=f"第 {state.day_no()} 天"
                ),
                fallback="",
            )
            if rep.text and not rep.used_fallback:
                text += "\n\n📰 " + rep.text
        state2 = self._state(t.scope)
        locks = [
            f"{WorldMap().node_zh(k)}(还有 {max(1, int(v) - state2.day)} 天解除)"
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
                state2.day_no(),
                world_events=[EV.event_text(e) for e in state2.active_events()],
                player_events=pe_lines,
                weather_zh=state2.weather_for(t.region),
                region_zh=WorldMap().region_zh(t.region),
                location_zh=WorldMap().node_zh(t.location),
                locks=locks, scale=self._img_scale(),
            ),
            text=text,
            hint="\n".join(
                x for x in (_next_step(t),
                            "行动:`/探索` 前进 · `/任务` 看委托 · `/新手` 上手引导")
                if x
            ),
        ):
            yield r

    @filter.command("任务", alias={"委托", "quest", "quests"})
    async def cmd_quest(self, event: AstrMessageEvent):
        """/任务 —— 查看/放弃支线委托"""
        t, err = self._require(event, in_battle_ok=True)
        if err:
            yield event.plain_result(err)
            return
        arg = self._args(event, ("任务", "委托", "quest", "quests")).strip()
        async with self._lock(t.scope):
            await self._ensure_day(event, t)
            parts = arg.split()
            if parts and parts[0] in ("放弃", "drop", "取消"):
                if B.in_battle(t):
                    yield event.plain_result(
                        "⚠️ 对战中不能放弃委托 —— 先打完这场(出招/捕捉/逃跑)。"
                    )
                    return
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
                day=t.day_no(self._state(t.scope).day),   # 展示用相对天数
                region_zh=WorldMap().region_zh(t.region),
                done_count=len(QT.completed_ids(t)),
                max_active=QT.MAX_ACTIVE,
                scale=self._img_scale(),
            ),
            text=text,
            hint="委托进度自动记录 —— 行动:`/探索` 前进、`/捕捉 <球>` 收服、"
                 "`/对战 <序号>` 出招",
        ):
            yield r

    @filter.command("重置世界", alias={"reset_world", "删除存档"})
    async def cmd_reset(self, event: AstrMessageEvent):
        """/重置世界 —— 删除自己的存档(管理员可 -all 清全群)"""
        scope, uid = self._scope(event), self._uid(event)
        arg = self._args(event, ("重置世界", "reset_world", "删除存档")).strip()
        admins = [a.strip() for a in str(self._cfg("admin_uids", "") or "").split(",") if a.strip()]
        if arg in ("all", "-all", "全部"):
            # 必须"默认拒绝":原来写的是 `if admins and uid not in admins`,
            # 于是**没配置管理员(默认情况)时任何玩家都能清空全群存档**。
            # 清空是破坏性操作,宁可拒绝也不能放开。
            if not admins:
                yield event.plain_result(
                    "❌ 未配置管理员(`admin_uids`),为避免误删,清空全群存档已禁用。"
                    "请先在插件配置里填写管理员 QQ。"
                )
                return
            if uid not in admins:
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

    @filter.command("新手", alias={"引导", "教程", "新手引导", "tutorial", "guide"})
    async def cmd_tutorial(self, event: AstrMessageEvent):
        """/新手 —— 5 步上手引导(附当前进度建议)"""
        lines = [TUTORIAL_TEXT]
        t = self._load(event)
        if t is not None:
            nxt = _next_step(t)
            if nxt:
                lines.append(nxt)
        yield event.plain_result("\n\n".join(lines))

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
            f"👟 步数 {t.data.get('steps', 0)}"
            f" · 旅程第 {t.day_no()} 天({game_day_str()})",
        ]
        if B.in_battle(t):
            lines.append("⚔️ **正在对战中** —— " + self._battle_state_brief(t))
        else:
            lines.append("🕊️ 当前没有在战斗。")
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

    def _legend_hint(self, t: Trainer, world, sites: list[dict],
                     caught: list[str], ready: list[str], locked: list[str]) -> str:
        """神兽面板图片附带的文本:说清**为什么**还没解锁 + 怎么挑战。

        图片里未解锁的格子只显示 "???"(还带徽章 0/8 的标题),玩家看不出
        差多少徽章或者要不要先当冠军。
        """
        lines = []
        gyms = len(world.gyms(t.region)) or 8
        if locked:
            need = [s for s in sites if s.get("species") in locked]
            need_badges = max((int(s.get("need_badges") or 0) for s in need), default=0)
            tail = ""
            if need_badges and t.badge_count() < need_badges:
                tail = f"(需要 {need_badges} 枚徽章,你现在 {t.badge_count()} 枚)"
            elif not t.flag(f"champion:{t.region}"):
                tail = "(需要先成为本地区冠军)"
            lines.append(f"❓ 还有 {len(locked)} 只传说的线索没出现{tail}")
        if ready:
            names = "、".join(
                str(s.get("zh") or s.get("species"))
                for s in sites if s.get("species") in ready
            )
            lines.append(f"🎯 现在就栖息在附近:{names} —— `/神兽 挑战 <名字>`")
        if caught:
            lines.append(f"✅ 已收服 {len(caught)} 只")
        lines.append(f"提示:集齐 {gyms} 枚徽章后,更强的传说会出现")
        return "\n".join(lines)

    def _status_hint(self, t: Trainer, today: list[str]) -> str:
        """训练家卡图片附带的文本:卡片上**没画**的那几项。

        卡片画的是 ID/金钱/地区/当前/旅程/步数/图鉴/徽章格/队伍数/电脑/主线阶段,
        但没有危险度、道具件数、今日事件,也不写"现在不在对战中"。
        """
        world = WorldMap()
        lines = [
            f"◆ {world.where_am_i(t)} · 危险度 {world.tier_label(t.location)}"
            f" · 道具 {sum(t.bag.values())} 件"
        ]
        if B.in_battle(t):
            lines.append("⚔️ " + self._battle_state_brief(t))
        else:
            lines.append("🕊️ 当前没有在战斗")
        today_bits = [
            str(x) for x in today if not str(x).lstrip().startswith(("📅", "🌅"))
        ]
        if today_bits:
            lines.append("◆ 今日:" + " / ".join(today_bits[:3]))
        nxt = _next_step(t)
        if nxt:
            lines.append(nxt)
        lines.append("行动:`/探索` 前进 · `/队伍` 看队伍 · `/新手` 上手引导")
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
        stock = world.shop_stock(t.location, t.badge_count())
        lines = [
            f"🛒 商店({world.node_zh(t.location)})· 余额 {fmt_money(t.money)}"
            + (f" · 折扣 {int((1 - discount) * 100)}%" if discount < 1 else "")
            + f" · 共 {len(stock)} 种商品"
        ]
        for key in world.shop_stock(t.location, t.badge_count()):
            entry = BAG_ITEMS.get(key)
            if not entry:
                continue
            eff = effect_text(key)
            lines.append(
                f"· {entry['zh']}"
                f" —— {fmt_money(item_price(key, badge_count=t.badge_count(), discount=discount))}"
                + (f" —— {eff}" if eff else "")
                + (f"({entry['desc']})" if entry.get("desc") else "")
            )
        lines.append("用法:`/商店 买 伤药 3` · `/商店 卖 精灵球 2`")
        return "\n".join(lines)

    def _battle_intro(self, meta: dict, log: list[str]) -> str:
        title = meta.get("title") or "对战"
        return f"⚔️ {title}!\n" + "\n".join(f"· {x}" for x in log[-8:])

    def _battle_hint(self, t: Trainer) -> str:
        """对战行动提示 —— **必须列出我方出战宝可梦的招式**,否则玩家不知道能出什么招。

        战斗画面是仿 GBA 的对话框 + 血条,画不出招式表;招式与 PP 只能靠这行文本。
        数值取自**对战内的队伍**(权威值:回合中 PP 会变),出战序号用
        `battle.player.active`,不能写死 party[0] —— 换人后 party[0] 不一定在场。
        """
        b = (B.session(t) or {}).get("battle") or {}
        side = b.get("player") or {}
        active = int(side.get("active") or 0)
        party = side.get("party") or []
        mon = B.dict_to_mon(party[active]) if 0 <= active < len(party) else t.mon(0)
        if mon is None:
            return ""
        dex = get_dex()
        zh = mon.nickname or (dex.species.get(mon.species) or {}).get("zh") or mon.species
        types = "/".join(dex.type_label(x) for x in (mon.types or []))
        moves = " ".join(
            f"{i}.{growth.move_zh(m)}({mon.pp.get(m, 0)})"
            for i, m in enumerate(mon.moves, 1)
        ) or "无"
        out = [f"🔵 {zh} Lv{mon.level} [{types}] 招式:{moves}"]
        out.append(
            f"行动:`/对战 <1-{max(1, len(mon.moves))}>` 出招、`/捕捉 精灵球`、"
            f"`/对战 switch <1-{max(1, len(t.party))}>` 换人、"
            "`/对战 item <道具>`、`/对战 run`"
        )
        out.append("(看威力与效果:`/招式 <序号>` —— 查招式不消耗回合)")
        if len(t.party) > 1:
            out.append(f"(出战:队伍第 {active + 1} 只)")
        return "\n".join(out)

    def _result_text(self, t: Trainer, res: B.TurnResult) -> str:
        head = {
            "win": "🎉 战斗胜利!",
            "caught": "🎉 捕获成功!",
            "escaped": "🏃 脱离了战斗",
            "stalled": "⌛ 战斗不了了之",
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
                # 必须与 _ensure_day 用同一把锁:否则"读-改-写"会交错,
                # 玩家指令刚滚出来的事件会被调度器的旧快照覆盖(丢更新)。
                async with self._lock(scope):
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
        head = (
            f"🌅 世界第 {state.day_no()} 天开始了({game_day_str(state.day)})"
        )
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
                weather=_battle_weather(state, t.region), day=state.day,
            )
            self._save(t)
            async for r in self._emit_battle(
                event, t, meta, log,
                text=self._battle_intro(meta, log),
                keep=self._battle_intro(meta, log) + "\n" + self._battle_hint(t),
                status=True,
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
                    weather=_battle_weather(state, t.region), day=state.day,
                )
                self._save(t)
                _hint = self._battle_hint(t)
                async for r in self._emit_battle(
                    event, t, meta, log,
                    text=self._battle_intro(meta, log) + f"\n\n{_hint}",
                    keep=self._battle_intro(meta, log) + f"\n{_hint}",
                    status=True,
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
                    champion=bool(t.flag(f"champion:{t.region}")),
                    day=t.day_no(state.day),
                    scale=self._img_scale(),
                ),
                text=legendary.panel_text(t, world=world, day=state.day),
                hint=self._legend_hint(t, world, sites, caught_keys, ready_keys,
                                       locked_keys),
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
                hint="挑战:`/大赛 挑战`(连胜 3 轮成为世界冠军)",
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
            t.set_flag("tournament_last_foe", str(meta.get("title") or ""))
            log = B.start(
                t, meta["team"], kind="tournament", meta=meta,
                weather=_battle_weather(state, t.region), day=state.day,
            )
            self._save(t)
            async for r in self._emit_battle(
                event, t, meta, log,
                text=self._battle_intro(meta, log),
                keep=self._battle_intro(meta, log) + "\n" + self._battle_hint(t),
                status=True,
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
        for key in world.shop_stock(t.location, t.badge_count()):
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
        _box_before = {str(m.get("id") or "") for m in (t.data.get("box") or [])}
        view = B.view(t)
        mon = view.get("my") or {}
        if res.outcome == "caught" and res.rewards:
            _ball_key = str(res.item_key or "poke-ball")
            # 满 6 只时捕获物进电脑,party[-1] 是旧成员 → 卡片会显示错宝可梦。
            # 用"战斗结算后的 box 增量"定位新捕获的那只。
            box_now = t.data.get("box") or []
            fresh = [m for m in box_now if str(m.get("id") or "") not in _box_before]
            caught = (
                B.dict_to_mon(fresh[-1])
                if fresh
                else (B.dict_to_mon(t.party[-1]) if t.party else None)
            )
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
                    hint="推进:`/主线 挑战` 击退敌方组织、`/主线` 查看剧情",
                ):
                    yield r
            return
        if res.growth:
            # 成长卡只讲**升级的那一只**。以前是从 res.growth 的所有文字行里
            # 刮 `「…」` 来拼 learned/pending,结果全队的"学会/待学"都堆到
            # 当前出战的那只头上(杰尼龟的"想学「缩入壳中」"出现在波波卡上)。
            detail = next((d for d in res.growth_detail if d.get("levels")), None)
            if detail is None and res.growth_detail:
                detail = res.growth_detail[-1]
            idx = int((detail or {}).get("index") or 0)
            md = t.party[idx] if 0 <= idx < len(t.party) else None
            view_g = B._mon_view(B.dict_to_mon(md)) if md else mon
            learned = list((detail or {}).get("learned") or [])
            pending = list((detail or {}).get("pending") or [])
            evo_from = growth.species_zh(str((detail or {}).get("evolved_from") or ""))
            evo_to = growth.species_zh(str((detail or {}).get("evolved_to") or ""))
            before_level = int((detail or {}).get("from_level") or 0)
            after_level = int((detail or {}).get("to_level") or 0)
            async for r in self._emit_ui(
                event, "growth",
                lambda: UII.render_growth(
                    view_g, before_level=before_level,
                    after_level=after_level, learned=learned,
                    pending=pending, evolved_from_zh=evo_from, evolved_to_zh=evo_to,
                    scale=self._img_scale(),
                ),
                text="",
            ):
                yield r
            return
        if res.outcome in ("win", "loss", "forfeit", "escaped", "stalled"):
            async for r in self._emit_ui(
                event, "result",
                lambda: UII.render_battle_result(
                    outcome=res.outcome, title=str(meta.get("title") or ""),
                    lines=res.lines,
                    # 战败/认输/逃跑不给奖励栏(渲染层也会再挡一次)
                    rewards=res.rewards if res.outcome in ("win", "caught") else [],
                    growth=res.growth, mon=mon, scale=self._img_scale(),
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
                       text: str = "", hint: str = ""):
        """按配置输出界面图片(仿 GBA 菜单);失败或未开启则回退文本。

        图片成功时**只**追加 `hint`(玩家接下来要敲什么指令这类图片里画不出来的
        信息);界面已经画出来的内容不再重复发一遍文本 —— 之前是"图片 + 整段
        同样的文本",看着很冗余。`text` 专门留给渲染失败时的文本回退。
        """
        if self._cfg_bool("ui_image", True):
            try:
                data = builder()
                if data:
                    path = self._temp_image(data, f"pw_ui_{label}")
                    comps = [Image.fromFileSystem(path)]
                    if hint:
                        comps.append(Plain(hint))
                    yield event.chain_result(comps)
                    return
            except Exception as e:  # 渲染失败必须回退文本
                logger.debug("宝可梦世界: %s 界面渲染失败,回退文本: %s", label, e)
        body = "\n".join(x for x in (text, hint) if x)
        if body:
            yield event.plain_result(body)

    def _ability_zh(self, key: str) -> str:
        """特性的中文名(查不到就返回空串)。"""
        if not key:
            return ""
        try:
            return str((get_dex().resolve_ability(key)[1] or {}).get("zh") or "")
        except Exception:
            return ""

    def _held_zh(self, key: str) -> str:
        """持有物中文名(持有道具不在 BAG_ITEMS 里,要走 resolve_item)。"""
        if not key:
            return ""
        from .pw.items import resolve_item

        r = resolve_item(key)
        if not r:
            return str(key)
        return str((r[1] or {}).get("zh") or key)

    def _mon_payload(self, t: Trainer, d: dict, *, index: int = 1,
                     party_size: int = 1) -> dict:
        """单只宝可梦资料页的数据(渲染层与文本层共用)。"""
        dex = get_dex()
        mon = B.dict_to_mon(d)
        entry = dex.species.get(mon.species) or {}
        ab = dex.resolve_ability(mon.ability)
        ab_entry = (ab[1] or {}) if ab else {}
        nat = dex.resolve_nature(mon.nature) or "hardy"
        nat_entry = dex.natures.get(nat) or {}
        # exp_for_level(growth_rate, level):第一个参数是成长曲线,不是等级
        rate = dex.growth_of(mon.species)
        lo = dex.exp_for_level(rate, mon.level)
        hi = max(lo + 1, dex.exp_for_level(rate, min(100, mon.level + 1)))
        exp_pct = B.exp_progress(mon)
        moves = []
        for key in mon.moves[:4]:
            mv = dex.moves.get(key) or {}
            moves.append(
                {
                    "key": key,
                    "zh": growth.move_zh(key),
                    "type": str(mv.get("type") or ""),
                    "pp": int(mon.pp.get(key, 0) or 0),
                    # 上限要含 PP 提升剂的加成
                    "pp_max": max_pp(mon, key),
                    "pp_bonus": int((mon.pp_bonus or {}).get(key, 0) or 0),
                }
            )
        evo = dex.evolution_options(
            mon.species, level=mon.level, moves=mon.moves, item=mon.item or None,
            friendship=mon.friendship, gender=mon.gender,
            stats=mon.stats, party=t.party_mon(),
        ) or []
        evo_hint = ""
        if evo:
            nxt = evo[0]
            to_zh = _sp_zh(str(nxt.get("target") or ""))
            how = _kind_zh(nxt.get("kind"))
            need = nxt.get("level")
            evo_hint = f"可进化 → {to_zh}" + (
                f"(Lv{int(need)})" if isinstance(need, (int, float)) else f"({how})"
            )
        return {
            "species": mon.species,
            "index": int(index),                       # 给"待学招式"提示里的 /学招 用
            "pending": list(d.get("pending") or []),    # 招式已满、还没决定替换的
            "name": mon.nickname or entry.get("zh") or mon.species,
            "level": int(mon.level),
            "gender": str(mon.gender or ""),
            "types": [dex.type_label(x) for x in (mon.types or [])],
            "cur_hp": int(mon.cur_hp),
            "max_hp": int(mon.max_hp),
            "status": str(mon.status or ""),
            "exp_pct": exp_pct,
            "exp_now": max(0, int(mon.exp) - lo),
            "exp_next": max(1, hi - lo),
            "stats": dict(mon.stats or {}),
            "base": dict(entry.get("baseStats") or {}),
            "nature_zh": str(nat_entry.get("zh") or mon.nature or ""),
            "ability_zh": str(ab_entry.get("zh") or mon.ability or ""),
            "ability_desc": str(ab_entry.get("desc") or ""),
            "item_zh": self._held_zh(str(mon.item or "")),
            "friendship": int(mon.friendship or 0),
            "moves": moves,
            "evo_hint": evo_hint,
            "dex_no": int(entry.get("num") or 0),
            "genus": str(entry.get("genus") or ""),
        }

    def _mon_text(self, t: Trainer, p: dict, *, where: str = "party") -> str:
        """资料页的文本回退(图片渲染失败时用)。"""
        dex = get_dex()
        head = f"{p.get('name')} Lv{p.get('level')}"
        if p.get("gender") in ("M", "F"):
            head += " " + ("♂" if p["gender"] == "M" else "♀")
        stats = p.get("stats") or {}
        base = p.get("base") or {}
        lines = [
            f"◆ {head}",
            f"属性:{'/'.join(p.get('types') or ['?'])}"
            + (f" · 编号 #{int(p.get('dex_no') or 0):04d}" if p.get("dex_no") else ""),
            f"HP {p.get('cur_hp')}/{p.get('max_hp')} [{bar(p.get('cur_hp'), p.get('max_hp'), 10)}]"
            + (f" · {p.get('status')}" if p.get("status") else ""),
            f"经验 {p.get('exp_now')}/{p.get('exp_next')}({float(p.get('exp_pct') or 0):.0f}%)",
            "── 能力值(括号内为种族值)──",
        ]
        for label, key in (("HP", "hp"), ("攻击", "atk"), ("防御", "def"),
                           ("特攻", "spa"), ("特防", "spd"), ("速度", "spe")):
            b = base.get(key)
            lines.append(
                f"· {label} {int(stats.get(key) or 0)}"
                + (f"({int(b)})" if isinstance(b, (int, float)) else "")
            )
        lines += [
            "── 详情 ──",
            f"特性:{p.get('ability_zh') or '?'}",
            f"性格:{p.get('nature_zh') or '?'}",
            f"亲密:{int(p.get('friendship') or 0)}",
            f"持有:{p.get('item_zh') or '无'}",
        ]
        if p.get("ability_desc"):
            lines.append(f"({p['ability_desc']})")
        lines.append("── 招式 ──")
        for i, mv in enumerate(p.get("moves") or [], 1):
            lines.append(
                f"{i}. {mv.get('zh')}({dex.type_label(str(mv.get('type') or ''))}) "
                f"PP {mv.get('pp')}/{mv.get('pp_max')}"
            )
        if not p.get("moves"):
            lines.append("(没有招式)")
        if p.get("evo_hint"):
            lines.append(p["evo_hint"])
        return "\n".join(lines)

    def _box_payload(self, t: Trainer) -> list[dict]:
        """仓库列表数据。"""
        dex = get_dex()
        out = []
        for p in t.box:
            mon = B.dict_to_mon(p)
            out.append(
                {
                    "species": mon.species,
                    "name": mon.nickname or (dex.species.get(mon.species) or {}).get("zh")
                    or mon.species,
                    "level": int(mon.level),
                    "gender": str(mon.gender or ""),
                    "cur_hp": int(mon.cur_hp),
                    "max_hp": int(mon.max_hp),
                    "status": str(mon.status or ""),
                }
            )
        return out

    def _box_text(self, t: Trainer, mons: list[dict]) -> str:
        if not mons:
            return "📦 电脑仓库是空的。队伍满 6 只后收服的宝可梦会存到这里。"
        lines = [f"📦 电脑仓库({len(mons)} 只)"]
        for i, m in enumerate(mons, 1):
            g = m.get("gender")
            lines.append(
                f"{i}. {m.get('name')} Lv{m.get('level')}"
                + ("♂" if g == "M" else "♀" if g == "F" else "")
                + f" HP {m.get('cur_hp')}/{m.get('max_hp')}"
                + (f" [{m.get('status')}]" if m.get("status") else "")
            )
        lines.append("资料:`/宝可梦 电脑 <序号>` · 放生:`/电脑 放生 <序号> 确认`")
        return "\n".join(lines)

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

    def _bag_payload(self, t: Trainer, pocket_arg: str = "", *,
                     index: int = 0, page: int = 0) -> dict:
        """背包界面数据:按口袋分组,返回当前口袋的条目。

        `index`(1 起)/ `page`(1 起)用来定位要**高亮并显示说明**的那一件 ——
        以前 `selected` 恒为 0,口袋一多就永远只看得到第一件的说明(用户反馈)。
        """
        groups: dict[str, list[dict]] = {}
        for key, entry, n in t.bag_items():
            pk = UI.KIND_TO_POCKET.get(str(entry.get("kind") or ""), "items")
            groups.setdefault(pk, []).append(
                {
                    "key": key,
                    "zh": entry.get("zh") or key,
                    "count": n,
                    "desc": entry.get("desc") or "",
                    # 结构化效果:每行都要展示"这件道具到底做什么"
                    "effect": effect_text(key),
                    "kind": entry.get("kind") or "",
                }
            )
        want = (pocket_arg or "").strip()
        pocket = ""
        for pk, label in UI.POCKETS:
            if want and want in (pk, label):
                pocket = pk
        if not pocket:
            # 没写分类时**沿用上次看的分类**(存在玩家身上):
            # 否则 `/背包 回复` 之后来一句 `/背包 页 2` 会突然跳回"道具/精灵球",
            # 玩家以为自己翻的是当前分类的下一页。
            last = str(t.flag("bag_pocket", "") or "")
            if any(last == pk for pk, _lb in UI.POCKETS):
                pocket = last
        if not pocket:
            # 头一次打开:挑第一个**非空**的分类(比停在空的"道具"友好)
            for pk, _label in UI.POCKETS:
                if groups.get(pk):
                    pocket = pk
                    break
            pocket = pocket or "items"
        rows = groups.get(pocket, [])
        per = max(1, int(UI.BAG_PER_PAGE))
        if index > 0:
            sel = min(index - 1, len(rows) - 1)
        elif page > 1:
            sel = min((page - 1) * per, len(rows) - 1)
        else:
            sel = 0
        return {
            "items": rows,
            "pocket": pocket,
            "selected": max(0, sel),
            "groups": groups,
        }

    def _card_payload(self, t: Trainer) -> dict:
        """训练家卡数据:`id_no` 由 uid 稳定派生,徽章按当前地区顺序排列。"""
        world = WorldMap()
        region = t.region
        got = {int(b.split(":")[1]) for b in t.badges if str(b).startswith(f"{region}:")}
        badges = [
            (
                (g.get("badge") or g.get("title") or "")[:3],
                int(g.get("order", 0)) in got,
                str(g.get("type") or ""),      # 属性 → 徽章里的徽记造型
            )
            for g in world.gyms(region)[:8]
        ]
        while len(badges) < 8:
            badges.append(("", False))
        cur = story.current_stage(t)
        prog = f"{cur['title']}:{cur['desc']}" if cur else "本地区主线已完成"
        # 注意:new_trainer 写的是 play_day(=创建那天的天数),从来没有 created_day 字段。
        # 旧写法读 created_day 恒为 0 → 训练家卡的"第几天"永远是 1。
        created = int(t.data.get("play_day") or 0)
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
            # 末行在平时是"看帮助",对战中改成对战状态 —— /状态 也能回答
            # "我是不是在战斗里"(用户要求)
            "best": self._card_battle_line(t) or "◆ /帮助 查看全部指令",
        }

    def _battle_state_brief(self, t: Trainer) -> str:
        """对战中一句话状态:"对手 波波 Lv5 · 第 2 回合"。

        对手与回合数都在 `session["battle"]` 里(session 顶层只有 kind/meta 等),
        读 `sess["foe"]` 会恒为空 —— 之前就显示出"对手 ?"(踩过)。
        """
        sess = B.session(t) or {}
        b = sess.get("battle") or {}
        if not b:
            return ""
        enemy = b.get("enemy") or {}
        party = enemy.get("party") or []
        active = int(enemy.get("active") or 0)
        foe = party[active] if 0 <= active < len(party) else {}
        dex = get_dex()
        name = str(foe.get("nickname") or (dex.species.get(str(foe.get("species") or ""))
                                          or {}).get("zh") or foe.get("species") or "?")
        lv = foe.get("level")
        turn = max(1, int(b.get("turn") or 0))
        head = f"对手 {name}" + (f" Lv{int(lv)}" if isinstance(lv, (int, float)) else "")
        me = t.mon(int(((b.get("player") or {}).get("active")) or 0))
        if me is not None:
            head += f" · 我方 {me.display} HP {me.cur_hp}/{me.max_hp}"
        return f"{head} · 第 {turn} 回合"

    def _card_battle_line(self, t: Trainer) -> str:
        """训练家卡末行:对战中显示对手与回合数。"""
        brief = self._battle_state_brief(t)
        return f"⚔️ 对战中:{brief}" if brief else ""

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
                # 主线"尾声"章节:在该地点收服传说即算完成。
                # 之前没有任何路径能标记 epilogue → 主线面板永远停在"当前目标"。
                cur = story.current_stage(t)
                if (
                    cur
                    and cur.get("kind") == "epilogue"
                    and cur.get("location") == (site.get("location") or "")
                    and story.mark_stage(t, t.region, cur["key"])
                ):
                    lines.append(f"📜 主线推进:{cur.get('title')} —— 传说与你建立了羁绊!")
            elif res.outcome == "win":
                legendary.mark_fled(t, site["species"], day)
                lines.append(
                    f"🐉 {site['zh']} 被击退了,它逃走了 —— 明天再来或许还能遇到。"
                )
            elif res.outcome in ("escaped", "loss", "forfeit", "stalled"):
                # 逃跑/战败也算"今天惊动过它":否则玩家可以当天无限重挑刷捕获
                legendary.mark_fled(t, site["species"], day)
                lines.append(f"🐉 {site['zh']} 失去了踪影 —— 明天再来找它吧。")
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
            used_item = bool(meta.get("used_item"))
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
                # 用结构化字段,不要解析 growth 的展示文案 —— battle.py 输出的是
                # "→ Lv6",而这里原来匹配的是"升到了",于是升级类委托永远推不动
                if int(getattr(res, "levels_gained", 0) or 0):
                    out += QT.note(t, "level_up", levels=int(res.levels_gained))
                for sp_key in getattr(res, "evolved", []) or []:
                    out += QT.note(t, "evolve", species=str(sp_key))
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
        keep: str = "",
        status: bool = False,
    ):
        """按配置输出战斗画面:优先图片(仿经典对战界面),失败自动回退文本。

        图片成功时**只**追加 `keep`:战斗日志已经画在对话框里了,不该再发一遍;
        但**招式提示**、主线/神兽/大赛推进、LLM 叙事这些图片里没有的信息必须保留。

        `status=True` 时文本回退用 `B.status_text()`(它本身就包含血条/招式/日志),
        这样图片路径不会再额外跟一条重复的状态文本 —— 之前
        `cmd_battle` 每回合都无条件再发一次 `status_text`,和图片内容完全重复。
        """
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
                        if keep:
                            comps.append(Plain(keep))
                        yield event.chain_result(comps)
                        return
            except Exception as e:  # 渲染失败必须回退文本
                logger.debug("宝可梦世界: 战斗图片渲染失败,回退文本: %s", e)
        if status and B.in_battle(t):
            body = B.status_text(t)
            head = keep or text
            if head and "招式:" in body:
                # status_text 已经列了招式,提示里那行"🔵 … 招式:…"就多余了
                head = "\n".join(
                    ln for ln in head.split("\n") if not ln.startswith("🔵 ")
                ).strip()
            if head and head not in body:
                body = f"{head}\n\n{body}"
        else:
            body = text or keep
        if body:
            yield event.plain_result(body)

# ── 模块级小工具 ──────────────────────────────────────────────────
# 对战中其他行动的一律锁定文案。写成**模块级常量**而不是类属性:
# 类上非 callable 的属性在测试宿主对象 `_Cmd` 上不会被拷贝(它只拷 callable),
# 一旦引用 self.XXX 测试里就会 AttributeError。
_BATTLE_LOCKED_MSG = (
    "⚔️ 你正在对战中,其他行动已锁定!\n"
    "· `/对战 <招式序号>` 出招(序号见提示)\n"
    "· `/捕捉 <精灵球>` 投球收服\n"
    "· `/对战 switch <队伍序号>` 换人、`/对战 item <道具>` 用药\n"
    "· `/对战 run` 逃跑\n"
    "打完(击败/被打败/逃跑/收服)或 `/对战 forfeit` 认输后才能继续探索。\n"
    "(期间仍可查看:`/状态`、`/队伍`、`/宝可梦 <序号>`、`/背包`)"
)


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


def _start_err(t) -> str:
    """开战前的通用检查:全队倒下时 B.start 会抛 BattleError,旧实现让它直接
    冒出去,玩家点什么都没回复。写成模块级函数避免测试宿主对象重绑 staticmethod。
    """
    if t.all_fainted():
        return "❌ 你的宝可梦全都失去战斗能力了,先去 `/治疗` 吧。"
    return ""


def _battle_weather(state, region: str) -> str:
    """开战天气:世界事件里"天气异常"指定的天气优先于地区常规天气。

    之前 battle_weather 只被写进 state.modifiers、全仓库没有任何读取点,
    于是"天气异常"事件纯属装饰(界面文案说下雨,打起来还是晴天)。
    写成模块级函数而不是 @staticmethod:测试宿主对象会把 staticmethod 拷成
    普通函数、多绑一个 self,导致调用签名错位。
    """
    return str(state.modifiers.get("battle_weather") or "") or state.weather_for(region)


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


# 玩家间交换报价的有效期(秒)与单次可持有的报价数上限
TRADE_TTL = 300
TRADE_MAX_OFFERS = 6


def _at_users(event) -> list[tuple[str, str]]:
    """从消息里取出被 @ 的玩家 `(uid, 昵称)`。

    AstrBot 的 At 组件在不同适配器上形态不一(属性 `.qq` / `.data["qq"]`),
    部分适配器还会把 CQ 码留在 message_str 里 —— 三种都兜住,
    并且忽略 @全体成员(qq=all/0)。
    """
    out: list[tuple[str, str]] = []
    msg = getattr(getattr(event, "message_obj", None), "message", None) or []
    if isinstance(msg, (list, tuple)):
        for comp in msg:
            data = getattr(comp, "data", None)
            is_at = str(getattr(comp, "type", "") or "").lower() == "at" or \
                "At" in type(comp).__name__
            if not is_at:
                continue
            uid = ""
            name = ""
            if isinstance(data, dict):
                uid = str(data.get("qq") or data.get("user_id") or data.get("target") or "")
                name = str(data.get("name") or data.get("nickname") or "")
            uid = uid or str(getattr(comp, "qq", "") or "")
            name = name or str(getattr(comp, "name", "") or "")
            if uid and uid not in ("all", "0"):
                out.append((uid, name))
    raw = str(getattr(event, "message_str", "") or "")
    for m in re.finditer(r"\[CQ:at,qq=(\d+)(?:,name=([^\]]+))?\]", raw):
        uid, name = m.group(1), m.group(2) or ""
        if (uid, name) not in out:
            out.append((uid, name))
    return out


def _trade_key(frm: str, to: str) -> str:
    """交换报价的键。

    写成模块级函数而不是 `@staticmethod`:测试宿主是把方法直接 setattr 到对象上的,
    `@staticmethod` 会被重绑成实例方法,调用时凭空多一个 self。
    """
    return f"{frm}->{to}"


def _find_mon_slot(trainer, mon_id: str) -> tuple[str, int]:
    """按 id 在队伍/电脑里定位宝可梦,返回 ("party"|"box", 下标) 或 ("", -1)。"""
    for where, rows in (("party", trainer.party), ("box", trainer.box)):
        for i, p in enumerate(rows):
            if str(p.get("id")) == str(mon_id):
                return where, i
    return "", -1


def _parse_page_args(arg: str, *, numeric_is_page: bool = False) -> tuple[str, int, int]:
    """解析"[口袋] [序号] [页 N]"(背包)与"[序号] [页 N]"(商店)。

    返回 `(剩余的第一个非数字词, 序号, 页码)` —— 序号与页码都是 1 起,0 表示没给。
    支持三种写法:`<序号>`、`页 <N>`/`第 <N> 页`/`page <N>`。
    单独一个数字默认当**序号**(更常用:想知道第 12 件是什么);
    `numeric_is_page=True` 时才当页码。
    """
    toks = [x for x in str(arg or "").replace("、", " ").split() if x]
    page = 0
    rest: list[str] = []
    i = 0
    while i < len(toks):
        a = toks[i].lower()
        if a in ("页", "第", "page", "p") and i + 1 < len(toks):
            page = coerce_int(toks[i + 1], 0)
            i += 2
            if i < len(toks) and toks[i].lower() in ("页", "page"):
                i += 1      # "第 3 页":尾巴那个"页"要吃掉
            continue
        if a.endswith("页") and a[:-1].isdigit():     # 第2页 写成 "2页"
            page = coerce_int(a[:-1], 0)
            i += 1
            continue
        rest.append(toks[i])
        i += 1
    index = 0
    word = ""
    for a in rest:
        if a.isdigit():
            if numeric_is_page and not page:
                page = coerce_int(a, 0)
            elif not index:
                index = coerce_int(a, 0)
        elif not word:
            word = a
    return word, index, page


_LEARN_METHOD_ZH = {
    "M": "招式机",
    "T": "教招",
    "E": "蛋招",
    "S": "特殊",
    "R": "特殊",
    "V": "VC/旧世代",
    "L0": "进化时",
}


def _accuracy_zh(acc) -> str:
    """命中率显示。数据里 `accuracy: True` 表示**必中**(不是 1%!)。"""
    if acc is True or acc is None:
        return "必中"
    if isinstance(acc, (int, float)):
        return f"{int(acc)}%"
    return "—"


def _learn_zh(methods) -> str:
    """把学习途径代码列表翻成中文(如 ["L12","M"] → "等级 12 / 招式机")。"""
    out: list[str] = []
    for c in methods or []:
        c = str(c)
        if c.startswith("L"):
            lv = int(c[1:] or 0)
            out.append("进化时" if lv == 0 else f"等级 {lv}")
        else:
            out.append(_LEARN_METHOD_ZH.get(c, c))
    return " / ".join(out)


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
