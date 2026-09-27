"""新手引导:5 步上手 + 随进度变化的"下一步该做什么"。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402


def _start(tmp):
    p = _Cmd(tmp)
    p.config = {"ui_image": False, "quest_enable": False}
    ev = _Event("/开始 小智 新叶喵")
    run_cmd(p, ev, p.cmd_start)
    return p, ev


def _next_step():
    return sys.modules["pw_plugin.main"]._next_step


def _txt(ev) -> str:
    """把事件输出拼成文本(chain_result 里存的是 (图片, 文本) 元组)。"""
    return "".join(str(x) for x in ev.outputs)


def test_tutorial_command_and_aliases():
    with tempfile.TemporaryDirectory() as tmp:
        p, _ev = _start(tmp)
        for cmd in ("/新手", "/引导", "/教程", "/tutorial"):
            _ev = _Event(cmd)
            run_cmd(p, _ev, p.cmd_tutorial)
            out = _txt(_ev)
            assert "5 步" in out, out
            assert "/开始" in out and "/探索" in out and "/对战" in out
            assert "/捕捉" in out and "/道馆" in out and "/联盟" in out
        # 没开始的新玩家也能看引导(不该被"先开始"拦住)
        p2 = _Cmd(tempfile.mkdtemp())
        p2.config = {"ui_image": False}
        _ev2 = _Event("/新手")
        run_cmd(p2, _ev2, p2.cmd_tutorial)
        out = _txt(_ev2)
        assert "新手引导" in out and "/开始" in out


def test_not_started_message_tells_you_how_to_start():
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False}
        _ev3 = _Event("/队伍")
        run_cmd(p, _ev3, p.cmd_team)
        out = _txt(_ev3)
        assert "/开始" in out, out
        assert "皮卡丘" in out, "要给出可以照抄的例子"
        assert "/新手" in out, "要指向引导"


def test_next_step_changes_with_progress():
    """引导要随进度变,而且**永远不能是空的**。"""
    from pw.world import WorldMap

    nxt = _next_step()
    world = WorldMap()
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        t = p._load(ev)
        assert "探索" in nxt(t), nxt(t)          # 刚创建 → 出门

        def apply(mut):
            tt = p._load(ev)
            mut(tt)
            p._save(tt)
            return p._load(ev)

        # 残血 → 先治
        t = apply(lambda x: x.data["party"][0].update({"cur_hp": 1}))
        assert "治疗" in nxt(t) or "伤药" in nxt(t), nxt(t)
        # 恢复 + 出过门(图鉴仍只有初始那只)→ 教怎么抓
        def recover(x):
            x.data["party"][0]["cur_hp"] = x.data["party"][0]["max_hp"]
            x.data["steps"] = 40
        t = apply(recover)
        assert "捕捉" in nxt(t), nxt(t)
        # 抓过第二只 → 站在下一个道馆城镇时应提示挑战道馆
        gym = world.next_gym(t.region, t.badges)

        def caught2(x):
            recover(x)
            x.mark_caught("pidgey")
            x.data["location"] = gym["location"]
        t = apply(caught2)
        assert "道馆 挑战" in nxt(t), nxt(t)
        # 8 徽章 + 站在联盟门口 → 打联盟
        def full(x):
            x.mark_caught("pidgey")
            for i in range(8):
                x.add_badge("kanto", i)
            x.data["location"] = world.gateway("kanto")
        t = apply(full)
        assert "联盟 挑战" in nxt(t), nxt(t)
        # 冠军 → 大赛
        t = apply(lambda x: x.set_flag("champion:kanto", True))
        assert "大赛" in nxt(t), nxt(t)


def test_status_hint_contains_next_step():
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        t = p._load(ev)
        hint = p._status_hint(t, [])
        assert "下一步" in hint, hint
        assert "/新手" in hint, "档案末尾要指出引导入口"
