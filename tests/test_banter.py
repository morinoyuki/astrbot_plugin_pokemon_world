"""角色台词(开战前/被打败)与火箭队三人组的登场。

台词是"内容型"功能,最容易在重构里被静默丢掉 —— 这里锁住几件关键的事:
每个战斗类型都要有话可说(除了野生),具体人物(馆主/三人组)要命中专属台词。
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pw import banter  # noqa: E402


def test_every_battle_kind_has_banter_except_wild():
    kinds = ["trainer", "gym", "trial", "elite", "champion", "rocket",
             "legend", "tournament"]
    for kind in kinds:
        assert banter.pre_battle(kind, {"name": "测试"}), f"{kind} 缺开战台词"
        assert banter.defeat(kind, {"name": "测试"}), f"{kind} 缺战败台词"
    assert banter.pre_battle("wild", {}) == ""
    assert banter.defeat("wild", {}) == ""


def test_gym_leaders_and_trio_get_their_own_lines():
    pre = banter.pre_battle("gym", {"gym": {"leader": "小刚"}})
    assert "小刚" in pre and "岩石" in pre, pre
    assert "小刚" in banter.defeat("gym", {"gym": {"leader": "小刚"}})
    trio = banter.pre_battle("rocket", {"trio": True})
    assert "诚心诚意" in trio and "喵" in trio, trio
    assert "好讨厌的感觉" in banter.defeat("rocket", {"trio": True})
    # 各地区反派各有各的台词
    for region in ("hoenn", "sinnoh", "galar"):
        assert "「" in banter.pre_battle("rocket", {"region": region})


def test_trio_event_is_deterministic_per_player_and_day():
    class T:
        uid = "u1"

    hits = [banter.trio_event(T(), day=d) for d in range(1, 60)]
    assert any(hits), "60 天里一次都没撞上三人组,概率太低"
    again = [banter.trio_event(T(), day=d) for d in range(1, 60)]
    assert [bool(x) for x in hits] == [bool(x) for x in again], "同一天结果必须一致"
    one = next(x for x in hits if x)
    assert one["kind"] == "rocket" and one["trio"] is True
    assert "喵" in one["pre"] and "好讨厌的感觉" in one["lose"]


# ── 三人组随机登场 + 长台词单独成条 ──────────────────────────────
def _plugins(tmp):
    import test_commands as TC
    sys.path.insert(0, TC.__file__.rsplit("/", 1)[0])
    cmd = TC._Cmd(tmp)
    return cmd, TC


def test_explore_trio_event_starts_a_rocket_battle():
    """/探索 撞见三人组:开一场火箭队战,开场白单独成条完整发出(不被战报滚掉)。"""
    import sys as _s
    import tempfile

    from test_commands import _Event, run_cmd

    with tempfile.TemporaryDirectory() as tmp:
        p, _TC = _plugins(tmp)
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        _ban = _s.modules["pw_plugin.pw.banter"]
        orig_ev, orig_team = _ban.trio_event, _ban.trio_team
        _ban.trio_event = lambda t, day: {"title": "火箭队三人组出现了!",
                                          "kind": "rocket", "trio": True}
        try:
            ev = _Event("/探索")
            run_cmd(p, ev, p.cmd_explore)
        finally:
            _ban.trio_event, _ban.trio_team = orig_ev, orig_team
        out = "\n".join(ev.outputs)
        t = p._load(_Event())
        from pw import battle as B
        assert B.in_battle(t), f"应该已经开战:{out[:200]}"
        assert str((t.data["battle"].get("meta") or {}).get("trio")) == "True" \
            or (t.data["battle"].get("meta") or {}).get("trio")
        assert "💬" in out, f"开场白应该单独成条:{out[:200]}"
        assert "既然你诚心诚意地发问了" in out, out[:400]
        # 同一天不再重复出现
        ev2 = _Event("/探索")
        run_cmd(p, ev2, p.cmd_explore)
        assert "火箭队三人组" not in "\n".join(ev2.outputs)


def test_long_banter_is_its_own_message():
    """超过两行的台词不进战报滚动区,由 _emit_battle 单独发一条完整的。"""
    import sys as _s
    import tempfile

    from test_commands import _Event, run_cmd

    with tempfile.TemporaryDirectory() as tmp:
        p, _TC = _plugins(tmp)
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        _ban = _s.modules["pw_plugin.pw.banter"]
        orig, orig_ev = _ban.pre_battle, _ban.trio_event
        _ban.pre_battle = lambda kind, meta, region="": "第一句名台词\n第二句名台词\n第三句名台词"
        # 用“必定触发的三人组”把流程钉在会调 _emit_battle 的分支上
        _ban.trio_event = lambda t, day: {"title": "火箭队三人组出现了!",
                                          "kind": "rocket", "trio": True}
        try:
            ev = _Event("/探索")
            run_cmd(p, ev, p.cmd_explore)
        finally:
            _ban.pre_battle, _ban.trio_event = orig, orig_ev
        out = "\n".join(ev.outputs)
        assert "💬" in out and "第三句名台词" in out, out[:300]
