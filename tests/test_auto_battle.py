"""自动战斗:一直用同一招,直到打完 / PP 耗尽 / 对方换人(/自动战斗)。"""

from __future__ import annotations

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402

from pw import battle as B  # noqa: E402
from pw.engine import create_pokemon  # noqa: E402
from pw.player import mon_to_dict  # noqa: E402


def _battle(mon="charizard", level=60, enemy=("magikarp", 3)):
    tmp = tempfile.TemporaryDirectory()
    p = _Cmd(tmp.name)
    p.config = {"ui_image": False}
    run_cmd(p, _Event("/开始 小智 小火龙"), p.cmd_start)
    t = p._load(_Event())
    t.data["party"][0] = mon_to_dict(create_pokemon(mon, level))
    B.start(t, [{"species": enemy[0], "level": enemy[1], "shiny": False}],
            kind="wild", wild=True, meta={"title": "野生的鲤鱼王"}, day=1)
    p._save(t)
    return tmp, p


def test_auto_battle_finishes_a_wild_fight():
    """野生战:一直打到对面倒下(对战结束),只发一次画面 + 一条总结。"""
    tmp, p = _battle()
    with tmp:
        ev = _Event("/自动战斗 1")
        run_cmd(p, ev, p.cmd_auto_battle)
        out = "\n".join(ev.outputs)
        assert "自动战斗" in out and "回合" in out, out[:300]
        assert not B.in_battle(p._load(_Event())), "自动战斗应该把野生战打完了"


def test_auto_battle_stops_when_pp_runs_out():
    """PP 用尽就该停下来,而不是继续空挥。"""
    tmp, p = _battle(enemy=("blissey", 80))            # 对面要够肉,才用得上第二回合
    with tmp:
        t = p._load(_Event())
        snap = B.session(t)
        party = snap["battle"]["player"]["party"]
        act = int(snap["battle"]["player"]["active"] or 0)
        key = party[act]["moves"][0]
        party[act]["pp"][key] = 1                      # 只留 1 点 PP
        p._save(t)
        ev = _Event("/自动战斗 1")
        run_cmd(p, ev, p.cmd_auto_battle)
        out = "\n".join(ev.outputs)
        assert "PP" in out, out[:300]
        assert B.in_battle(p._load(_Event())), "PP 用完时对战应该还开着"


def test_auto_battle_in_doubles_points_to_the_doubles_command():
    """合作双打不能用自动战斗(要等搭档一起出招)。"""
    from test_coop import _setup

    with tempfile.TemporaryDirectory() as tmp:
        p, scope, _t1 = _setup(tmp)
        for who in ("u1", "u2"):
            d = p.trainers.load(scope, who)
            d["location"] = "kanto-route-1"
            p.trainers.save(scope, who, d)
        run_cmd(p, _Event("/双打"), p.cmd_coop)
        ev = _Event("/自动战斗 1")
        run_cmd(p, ev, p.cmd_auto_battle)
        out = "\n".join(ev.outputs)
        assert "/双打" in out, out[:300]
