"""待补发推送:QQ 官方不能主动发消息 → 攒到玩家下次交互时补发。

覆盖:
1. `pw/push.py` 的入队/取走/去重/上限/TTL/落盘往返;
2. `_notify`(每日世界事件)与 `_announce`(超时自动出招/超时结算通知)在
   qq_official 会话里不主动发、改为入队;其它平台行为不变;
3. 下一条玩家交互(指令 / 按钮点击)由 `on_pending_push` 补发并清空;
4. `_PendingPushFilter` 的放行条件(有待补发 + 是真交互)。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 注意:main.py 是以包 `pw_plugin` 载入的,`from .pw import push` 拿到的是
# `pw_plugin.pw.push`;必须用插件同一个实例,否则测试改的 PENDING 和插件
# 看到的不是同一个 dict。
import pw_plugin.main as M  # noqa: E402
from pw_plugin.main import (  # noqa: E402
    _PendingPushFilter,
    _proactive_blocked,
    _scope_of,
)
from test_commands import _Cmd, _Event, run_cmd  # noqa: E402

PUSH = M.PUSH
SCOPE = "g10086"


@pytest.fixture(autouse=True)
def _clean_pending():
    PUSH.reset()
    yield
    PUSH.reset()


class _FakeCtx:
    def __init__(self):
        self.sent: list[tuple[str, object]] = []

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain))


def _world_event(day: int) -> dict:
    return {
        "id": "e1", "kind": "world", "region": "kanto",
        "title": "野生宝可梦骚动", "desc": "常磐森林里出现了异常脚印。",
        "day": day, "created_day": day, "until_day": day, "location": "",
    }


# ── pw/push.py 本体 ──────────────────────────────────────────────

def test_queue_take_and_dedupe():
    PUSH.queue("g1", "第一条")
    PUSH.queue("g1", "第二条")
    PUSH.queue("g1", "第二条")            # 连续重复不叠
    assert PUSH.has("g1")
    assert PUSH.take("g1") == ["第一条", "第二条"]
    assert not PUSH.has("g1")
    assert PUSH.take("g1") == []


def test_queue_limits_keep_newest():
    for i in range(PUSH.MAX_PER_SCOPE + 3):
        PUSH.queue("g1", f"第{i}条")
    texts = PUSH.take("g1")
    assert len(texts) == PUSH.MAX_PER_SCOPE
    assert texts[-1] == f"第{PUSH.MAX_PER_SCOPE + 2}条", "超上限应丢最旧的"
    assert all(len(t) <= PUSH.MAX_TEXT + 1 for t in texts)


def test_take_drops_expired():
    PUSH.queue("g1", "很旧的一条", ts=time.time() - PUSH.TTL - 10)
    PUSH.queue("g1", "很新的一条")
    assert PUSH.take("g1") == ["很新的一条"]


def test_drop_by_kind():
    PUSH.queue("g1", "每日", kind=PUSH.KIND_DAILY)
    PUSH.queue("g1", "战报", kind=PUSH.KIND_PVP)
    assert PUSH.drop("g1", PUSH.KIND_DAILY) == 1
    assert PUSH.take("g1") == ["战报"]


def test_save_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "pokemon_world", "pending_push.json")
        PUSH.queue("g1", "甲")
        PUSH.queue("u9", "乙", kind=PUSH.KIND_PVP)
        PUSH.save(path)
        PUSH.reset()
        assert PUSH.load(path) == 2
        assert PUSH.take("g1") == ["甲"]
        assert PUSH.has("u9", kind=PUSH.KIND_PVP)
        # 坏文件不能把插件搞崩
        with open(path, "w", encoding="utf-8") as f:
            f.write("{不是 json")
        assert PUSH.load(path) == 0
        assert PUSH.PENDING == {}


# ── 平台判定 / 过滤器 ────────────────────────────────────────────

def test_proactive_blocked_only_qq_official():
    assert _proactive_blocked("qq_official:GroupMessage:x")
    assert _proactive_blocked("qq_official_webhook:FriendMessage:x")
    assert not _proactive_blocked("test:GroupMessage:g1")
    assert not _proactive_blocked("aiocqhttp:GroupMessage:123")
    assert not _proactive_blocked("")


def test_scope_of_matches_plugin():
    assert _scope_of(_Event()) == SCOPE


def test_proactive_push_config_modes():
    old = M._PUSH_MODE["mode"]
    try:
        M._PUSH_MODE["mode"] = "never"
        assert _proactive_blocked("test:GroupMessage:g1"), "never:所有平台都等交互"
        M._PUSH_MODE["mode"] = "always"
        assert not _proactive_blocked("qq_official:GroupMessage:x"), "always:强制先主动推"
    finally:
        M._PUSH_MODE["mode"] = old


def test_pending_filter_requires_pending_and_interaction():
    flt = _PendingPushFilter()
    ev = _Event("/状态")
    ev.is_at_or_wake_command = False
    assert flt.filter(ev, None) is False, "没有待补发时不能命中"

    PUSH.queue(SCOPE, "世界事件")
    assert flt.filter(ev, None) is False, "有待补发,但群里闲聊不算交互"
    ev.is_at_or_wake_command = True
    assert flt.filter(ev, None) is True

    # 按钮点击(INTERACTION_CREATE)同样算一次交互
    class _ClickEvent(_Event):
        def get_interaction_button_data(self):
            return "/对战 1"

    click = _ClickEvent("")
    click.is_at_or_wake_command = False
    assert flt.filter(click, None) is True

    assert flt.filter(_Event("/状态"), None) is False, "别的群不受影响"


# ── _notify / _announce(PvP 超时通知):QQ 官方改为入队 ──────────

def test_notify_queues_instead_of_sending_on_qq_official():
    from pw.util import game_day

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"quest_enable": False, "narrate_enable": False}
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        run_cmd(p, _Event("/状态"), p.cmd_status)     # 世界落盘并带上 umo
        ctx = _FakeCtx()
        p.context = ctx
        p._push_file = os.path.join(tmp, "pokemon_world", "pending_push.json")
        st = p._state(SCOPE)
        st.data["umo"] = "qq_official:GroupMessage:abc"   # 切到不能主动推的平台
        st.data["last_roll_day"] = int(game_day()) - 1   # 世界停在昨天
        p._save_state(st)

        asyncio.run(p._roll_all(int(game_day())))
        assert not ctx.sent, "QQ 官方不能主动发消息,不该调 send_message"
        assert PUSH.has(SCOPE, kind=PUSH.KIND_DAILY), "每日事件必须攒着"
        assert os.path.exists(p._push_file), "待补发必须落盘,重启才能补"

        # 模拟重启:清内存 → 从文件读回 → 下一条玩家交互补发并清空
        PUSH.reset()
        assert PUSH.load(p._push_file) >= 1
        ev = _Event("/状态")
        run_cmd(p, ev, p.on_pending_push)
        assert any("世界第" in x for x in ev.outputs), ev.outputs
        assert not PUSH.has(SCOPE)


def test_notify_still_sends_on_other_platforms():
    from pw.util import game_day

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"quest_enable": False, "narrate_enable": False}
        ctx = _FakeCtx()
        p.context = ctx
        st = p._state(SCOPE)
        st.data["umo"] = "test:GroupMessage:g1"
        st.data["day"] = int(game_day())
        p._save_state(st)

        asyncio.run(p._notify(SCOPE, st, {"world_events": [_world_event(int(game_day()))]}))
        assert ctx.sent, "能主动推的平台行为不变"
        assert not PUSH.has(SCOPE)


def test_announce_queues_on_qq_official():
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        ctx = _FakeCtx()
        p.context = ctx
        p._push_file = os.path.join(tmp, "pokemon_world", "pending_push.json")
        st = p._state(SCOPE)
        st.data["umo"] = "qq_official_webhook:GroupMessage:abc"
        p._save_state(st)

        asyncio.run(p._announce(SCOPE, "⚔️ 超时自动出招:玩家对战继续"))
        assert not ctx.sent
        assert PUSH.has(SCOPE, kind=PUSH.KIND_PVP)
        assert PUSH.take(SCOPE) == ["⚔️ 超时自动出招:玩家对战继续"]


def test_pending_push_delivers_multiple_then_empty():
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p._push_file = os.path.join(tmp, "pokemon_world", "pending_push.json")
        p._state(SCOPE).data["umo"] = "qq_official:GroupMessage:abc"
        PUSH.queue(SCOPE, "第一条")
        PUSH.queue(SCOPE, "第二条")
        ev = _Event("/状态")
        run_cmd(p, ev, p.on_pending_push)
        assert [x for x in ev.outputs if "第" in str(x)] == ["第一条", "第二条"]

        ev2 = _Event("/状态")
        run_cmd(p, ev2, p.on_pending_push)
        assert not ev2.outputs, "补发完就该空了,不能重复发"


def test_today_drops_queued_daily():
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"quest_enable": False, "narrate_enable": False}
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        run_cmd(p, _Event("/状态"), p.cmd_status)
        p._push_file = os.path.join(tmp, "pokemon_world", "pending_push.json")
        PUSH.queue(SCOPE, "昨天的世界事件", kind=PUSH.KIND_DAILY)
        PUSH.queue(SCOPE, "战报", kind=PUSH.KIND_PVP)
        run_cmd(p, _Event("/今日"), p.cmd_today)
        assert not PUSH.has(SCOPE, kind=PUSH.KIND_DAILY), "自己看了 /今日 就不补世界事件"
        assert PUSH.has(SCOPE, kind=PUSH.KIND_PVP), "战报和 /今日 无关,继续留着"
