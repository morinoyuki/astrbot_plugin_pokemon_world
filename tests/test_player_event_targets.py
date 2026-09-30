"""个人事件只随机挑 2~4 位玩家(用户要求),且同一天固定。"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import tempfile  # noqa: E402

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402


def _plugin():
    tmp = tempfile.TemporaryDirectory()
    p = _Cmd(tmp.name)
    p.config = {"ui_image": False}
    run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
    return tmp, p


def _players(n: int) -> list:
    return [{"uid": f"u{i}", "name": f"玩家{i}", "level": 5} for i in range(n)]


def test_picks_two_to_four_players_randomly_and_deterministically():
    tmp, p = _plugin()
    with tmp:
        many = _players(9)
        picked = p._pick_player_event_targets(many, "test:GroupMessage:g1", 42)
        assert 2 <= len(picked) <= 4, f"应该挑 2~4 位:{len(picked)}"
        assert all(x in many for x in picked)
        # 同一天(同群)结果必须一致 —— 刷新/重跑不能换人
        again = p._pick_player_event_targets(many, "test:GroupMessage:g1", 42)
        assert [x["uid"] for x in picked] == [x["uid"] for x in again]
        # 换一天通常会换一批(不强制不同,但至少要能变)
        other = p._pick_player_event_targets(many, "test:GroupMessage:g1", 43)
        assert len(other) >= 2
        # 每个群各自决定
        group2 = p._pick_player_event_targets(many, "test:GroupMessage:g2", 42)
        assert 2 <= len(group2) <= 4


def test_small_group_is_untouched():
    tmp, p = _plugin()
    with tmp:
        for players in ([], [{"uid": "u1"}], [{"uid": "u1"}, {"uid": "u2"}]):
            got = p._pick_player_event_targets(players, "test:GroupMessage:g1", 7)
            assert got == players, "不足 2 人时应原样返回"
