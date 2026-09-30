"""qqofficial 读不到 @ —— @人 的玩法要能用角色名替代。"""

from __future__ import annotations

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402

from pw import coop as COOP  # noqa: E402
from pw.engine import create_pokemon  # noqa: E402
from pw.player import mon_to_dict  # noqa: E402


def _team_handler(p):
    """按命令注册找到 /组队 的处理函数(方法名可能与 /队伍 的撞车)。"""
    for attr in ("cmd_coop_team", "cmd_team_up", "cmd_group", "cmd_team"):
        fn = getattr(p, attr, None)
        if fn is not None and "组队" in (getattr(fn, "__doc__", "") or ""):
            return fn
    raise AssertionError("找不到 /组队 的处理函数")


def _host_with(name: str, uid: str, tmp: str):
    p = _Cmd(tmp)
    p.config = {"ui_image": False}
    ev = _Event("/开始 小智 杰尼龟")
    ev.sender_id = "u1"
    run_cmd(p, ev, p.cmd_start)
    t = p._load(ev)
    p.trainers.save(t.scope, uid, {
        "uid": uid, "name": name, "region": t.region, "location": t.location,
        "party": [mon_to_dict(create_pokemon("charmander", 9))],
        "box": [], "bag": {}, "money": 3000, "badges": [], "dex_seen": [],
        "dex_caught": [], "flags": {}, "unlocked_regions": [t.region],
    })
    return p, t


def test_team_offer_by_character_name_without_at():
    """没有 @ 组件时,`/组队 小茂` 也能向那位玩家发起邀请。"""
    with tempfile.TemporaryDirectory() as tmp:
        p, t = _host_with("小茂", "u2", tmp)
        ev = _Event("/组队 小茂")
        run_cmd(p, ev, _team_handler(p))
        state = p._state(t.scope)
        incoming = COOP.incoming(state, "u2")
        assert incoming, f"按名字没能找到对象:{ev.outputs}"
        row = incoming[0]
        assert COOP.partner_of(row, "u2") == "u1"
        assert (row.get("names") or {}).get("u2") == "小茂"


def test_name_match_prefers_exact_and_ignores_unknown():
    """精确匹配优先;名字不存在时不乱指人。"""
    with tempfile.TemporaryDirectory() as tmp:
        p, t = _host_with("小茂", "u2", tmp)
        p.trainers.save(t.scope, "u3", {
            "uid": "u3", "name": "小茂茂", "region": t.region, "location": t.location,
            "party": [mon_to_dict(create_pokemon("bulbasaur", 9))], "box": [], "bag": {},
            "money": 100, "badges": [], "dex_seen": [], "dex_caught": [], "flags": {},
            "unlocked_regions": [t.region],
        })
        got = p._name_targets(_Event("/组队 小茂"), t, "小茂")
        assert [u for u, _n in got] == ["u2"], got          # 精确命中,不选“小茂茂”
        assert p._name_targets(_Event("/组队 不存在"), t, "不存在") == []
        assert p._name_targets(_Event("/组队 小茂茂"), t, "小茂茂")[0][0] == "u3"
