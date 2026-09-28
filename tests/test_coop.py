"""合作双打:组队 → 开打 → 两人各交一次行动 → 结算/写回。

命令层流程(`/组队` → `/双打` → 各交一次)与引擎层(`step_doubles`)一起测。
"""

from __future__ import annotations

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402

from pw import battle as B  # noqa: E402
from pw import coop as COOP  # noqa: E402
from pw.engine import create_pokemon  # noqa: E402
from pw.player import mon_to_dict  # noqa: E402


def _poke(text: str, uid: str):
    """换一个发言的玩家(_Event 的 get_sender_id 是写死的,要一起换掉)。"""
    ev = _Event(text)
    ev.sender_id = uid
    ev.message_str = text
    ev.get_sender_id = lambda: uid
    return ev


def _setup(tmp):
    """两位玩家各一只,已组队(搭档存档直接写进店,避开第二条 /开始 的解析)。"""
    p = _Cmd(tmp)
    p.config = {"ui_image": False}
    run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
    t1 = p._load(_Event())
    scope = t1.scope
    data = dict(t1.data)
    data["name"] = "小茂"
    data["party"] = [mon_to_dict(create_pokemon("charmander", 8))]
    data["location"] = t1.location
    p.trainers.save(scope, "u2", data)
    state = p._state(scope)
    COOP.offer(state, t1.uid, "u2", "小智", "小茂")
    p._save_state(state)
    run_cmd(p, _poke("/组队 接受", "u2"), p.cmd_coop_team)
    assert COOP.pair_for(p._state(scope), t1.uid) is not None
    return p, scope, t1


def test_team_up_then_start_a_double_battle():
    with tempfile.TemporaryDirectory() as tmp:
        p, scope, t1 = _setup(tmp)
        ev = _Event("/双打")
        run_cmd(p, ev, p.cmd_coop)
        assert any("双打" in o for o in ev.outputs), ev.outputs
        row = COOP.pair_for(p._state(scope), t1.uid)
        assert COOP.battle_of(row), f"会话里没有登记双打:{ev.outputs}"
        view = B.view(p._load(_Event()))
        assert view["doubles"] and len(view["mine"]) == 2 and len(view["foes"]) == 2


def test_both_players_submit_before_the_turn_resolves():
    with tempfile.TemporaryDirectory() as tmp:
        p, scope, _t1 = _setup(tmp)
        run_cmd(p, _Event("/双打"), p.cmd_coop)
        first = _Event("/双打 1")
        run_cmd(p, first, p.cmd_coop)
        assert any("等" in o for o in first.outputs), first.outputs
        turn_before = int(B.view(p._load(_Event())).get("turn") or 0)
        run_cmd(p, _poke("/双打 1", "u2"), p.cmd_coop)
        assert int(B.view(p._load(_Event())).get("turn") or 0) == turn_before + 1
        assert (p.trainers.load(scope, "u2") or {}).get("party")


def test_doubles_leaves_singles_paths_alone():
    """道馆/联盟/大赛/主线全部走单打 —— 普通 NPC 对战不能变成双打。"""
    from pw import npc

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False}
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        t = p._load(_Event())
        gen = npc.build_route_battle(t, {"name": "测试", "id": "x"}, day=0)
        B.start(t, list(gen.get("team") or []), kind="trainer",
                meta=dict(gen.get("meta") or {}), day=0)
        assert not B.view(t)["doubles"], "普通对战不该变成双打"
        B.clear_finished(t)
