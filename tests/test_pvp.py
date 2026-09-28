"""玩家对玩家(PvP)回合制对战。"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tests"))

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402


class At:
    def __init__(self, qq, name=""):
        self.type = "at"
        self.data = {"qq": qq, "name": name}


class P2(_Event):
    """第二个玩家:同一个群,换个 sender_id。"""

    def get_sender_id(self):
        return "u2"


def setup(tmp):
    """两个玩家各就位(同群,所以共用一个 scope)。"""
    p = _Cmd(tmp)
    p.config = {"ui_image": False, "battle_image": False, "quest_enable": False}
    run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
    run_cmd(p, P2("/开始 小霞 小火龙"), p.cmd_start)
    return p


def challenge(p, wager=0):
    ev = _Event(f"/对战 @小霞 赌注 {wager}" if wager else "/对战 @小霞")
    ev.message_obj = SimpleNamespace(message=[At("u2", "小霞")])
    run_cmd(p, ev, p.cmd_battle)
    return "".join(str(x) for x in ev.outputs)


def act(p, cmd, second=False):
    ev = P2(cmd) if second else _Event(cmd)
    run_cmd(p, ev, p.cmd_battle)
    return "".join(str(x) for x in ev.outputs)


def test_challenge_accept_and_markers():
    with tempfile.TemporaryDirectory() as tmp:
        p = setup(tmp)
        out = challenge(p, 200)
        assert "发起对战" in out and "200" in out, out
        from pw import pvp as PVP

        st = p._state("g10086")
        assert PVP.incoming(st, "u2"), "报价没进收件箱"
        assert not PVP.incoming(st, "u1"), "不该发给自己"
        acc = act(p, "/对战 接受", second=True)
        assert "玩家对战" in acc and "小智" in acc and "小霞" in acc, acc
        for uid in ("u1", "u2"):
            assert p.trainers.load("g10086", uid).get("pvp"), f"{uid} 没有 pvp 标记"


def test_self_and_rich_challenge_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        p = setup(tmp)
        ev = _Event("/对战 @小智")
        ev.message_obj = SimpleNamespace(message=[At("u1", "小智")])
        run_cmd(p, ev, p.cmd_battle)
        assert "不能和自己对战" in "".join(str(x) for x in ev.outputs)
        out = challenge(p, 999999)
        assert "赌注不能超过" in out, out


def _start(p, wager=0):
    challenge(p, wager)
    act(p, "/对战 接受", second=True)


def test_turns_need_both_sides():
    with tempfile.TemporaryDirectory() as tmp:
        p = setup(tmp)
        _start(p)
        first = act(p, "/对战 1")
        assert "等对方出招" in first, first
        second = act(p, "/对战 1", second=True)
        assert "第 2 回合" in second or "结束" in second, second
        assert "使用了" in second or "结束" in second, second


def test_other_commands_are_locked_during_pvp():
    with tempfile.TemporaryDirectory() as tmp:
        p = setup(tmp)
        _start(p)
        ev = _Event("/探索")
        run_cmd(p, ev, p.cmd_explore)
        out = "".join(str(x) for x in ev.outputs)
        assert "正在和别人对战" in out, out
        ev2 = _Event("/治疗")
        run_cmd(p, ev2, p.cmd_heal)
        assert "正在和别人对战" in "".join(str(x) for x in ev2.outputs)
        # 查看类不受影响
        ev3 = _Event("/状态")
        run_cmd(p, ev3, p.cmd_status)
        assert "正在和别人对战" not in "".join(str(x) for x in ev3.outputs)


def test_forfeit_and_wager_transfer():
    with tempfile.TemporaryDirectory() as tmp:
        p = setup(tmp)
        _start(p, wager=200)
        m1 = p.trainers.load("g10086", "u1")["money"]
        out = act(p, "/对战 弃权")
        assert "获胜" in out and "小霞 获胜" in out, out
        d1 = p.trainers.load("g10086", "u1")
        d2 = p.trainers.load("g10086", "u2")
        assert d1["money"] == m1 - 200, d1["money"]
        assert d2["money"] == 3000 + 200, d2["money"]
        assert not d1.get("pvp") and not d2.get("pvp"), "标记要清掉"


def test_timeout_auto_acts_and_sweeps():
    with tempfile.TemporaryDirectory() as tmp:
        p = setup(tmp)
        _start(p)
        from pw import pvp as PVP

        act(p, "/对战 1")            # 只有 u1 出了招
        import time

        st = p._state("g10086")
        row = next(iter((st.data.get("pvp") or {}).values()))
        # 把 u1 的出招时间抹掉(当作很久以前)→ u2 视为超时
        row["acted_at"] = dict.fromkeys(row["acted_at"], 0)
        assert PVP.timeout_side(row, now=time.time() + 999) == "u2"
        # 走真实的调度路径:先落到存档,再让 `_pvp_tick` 重读并推进
        p._save_state(st)
        asyncio.run(p._pvp_tick())
        st2 = p._state("g10086")
        row2 = next(iter((st2.data.get("pvp") or {}).values()))
        assert int(row2.get("turn") or 0) == 1, row2.get("turn")
        # 该回合的行动已清空(等下一回合双方重新出招)
        assert not (row2.get("actions") or {}), row2.get("actions")
        # 而且这一回合是**真的落盘**了(以前只在结束时保存,推进的回合会丢)
        assert any(x.get("turn") == 1 for x in (st2.data.get("pvp") or {}).values())


def test_pvp_gives_no_exp_or_quest_progress():
    """玩家对战不发经验/不算委托 —— 否则可以和朋友互相刷。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = setup(tmp)
        _start(p)
        exp0 = [m["exp"] for m in p.trainers.load("g10086", "u1")["party"]]
        for _ in range(60):
            a = act(p, "/对战 1")
            b = act(p, "/对战 1", second=True)
            if "结束" in a or "结束" in b:
                break
        exp1 = [m["exp"] for m in p.trainers.load("g10086", "u1")["party"]]
        assert exp0 == exp1, f"PvP 不该给经验:{exp0} → {exp1}"


def test_timeout_turn_is_announced_to_group():
    """超时自动出招/超时结束都必须播报到群里。

    否则玩家要等自己下一次操作才发现“对战早就结束了”—— 赌注转移也看不到。
    """
    import asyncio

    with tempfile.TemporaryDirectory() as tmp:
        p = setup(tmp)
        sent: list[str] = []

        async def fake_announce(scope, text):
            sent.append(text)

        p._announce = fake_announce
        _start(p, wager=300)
        act(p, "/对战 1")                     # 只有 u1 出招
        st = p._state("g10086")
        row = next(iter((st.data.get("pvp") or {}).values()))
        row["acted_at"] = dict.fromkeys(row["acted_at"], 0)   # 视为很久以前
        p._save_state(st)
        asyncio.run(p._pvp_tick())            # 调度器那一跳
        assert any("超时" in x for x in sent), f"超时推进没播报:{sent}"
        assert any("玩家对战" in x for x in sent), sent


