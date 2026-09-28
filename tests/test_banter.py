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
