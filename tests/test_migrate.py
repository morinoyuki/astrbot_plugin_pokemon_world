"""平台切换(qqofficial):群 ID / 玩家 ID 迁移。"""

from __future__ import annotations

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402

from pw.engine import create_pokemon  # noqa: E402
from pw.player import mon_to_dict  # noqa: E402


def _host(cfg=None):
    tmp = tempfile.TemporaryDirectory()
    p = _Cmd(tmp.name)
    p.config = cfg or {"ui_image": False}
    return tmp, p


def _start(p, name="小智"):
    run_cmd(p, _Event(f"/开始 {name} 杰尼龟"), p.cmd_start)


def test_store_rename_scope_moves_players_and_world():
    """整群迁移:玩家存档 + 世界状态一起搬,源群清空。"""
    tmp, p = _host()
    with tmp:
        _start(p)
        t = p._load(_Event())
        state = p._state(t.scope)
        state.data["weather"] = "rain"
        p.worlds.save(state.scope, state.data)
        moved, msg = p.trainers.rename_scope(t.scope, "new-scope")
        assert (moved, msg) == (1, ""), (moved, msg)
        assert p.worlds.rename(t.scope, "new-scope") is True      # 世界状态一起搬
        assert p.trainers.list_players(t.scope) == []
        assert p.trainers.list_players("new-scope") == [t.uid]
        assert p.trainers.load("new-scope", t.uid)["party"]
        assert (p.worlds.load("new-scope") or {}).get("weather") == "rain"
        assert not p.worlds.load(t.scope)


def test_store_rename_refuses_to_overwrite():
    """目标群/玩家已有存档时必须拒绝(除非 force)—— 绝不静默覆盖。"""
    tmp, p = _host()
    with tmp:
        _start(p)
        t = p._load(_Event())
        p.trainers.save("new-scope", "u9", {"party": [mon_to_dict(create_pokemon("pikachu", 5))]})
        moved, msg = p.trainers.rename_scope(t.scope, "new-scope")
        assert moved == 0 and "已有存档" in msg, (moved, msg)
        assert p.trainers.load("new-scope", "u9")["party"][0]["species"] == "pikachu"
        assert p.trainers.list_players(t.scope) == [t.uid]      # 源群没被动
        moved2, msg2 = p.trainers.rename_scope(t.scope, "new-scope", force=True)
        assert moved2 == 1 and msg2 == ""
        # 玩家 ID 迁移同理
        n, m = p.trainers.rename_player("new-scope", t.uid, "u9")
        assert n == 0 and "已有存档" in m, (n, m)
        n2, m2 = p.trainers.rename_player("new-scope", t.uid, "u99")
        assert (n2, m2) == (1, "")
        assert p.trainers.load("new-scope", "u99")["party"]


def test_admin_command_gating_and_listing():
    """命令:非管理员拒绝;配了 admin_uids 才能迁移;列表能看到群与玩家。"""
    tmp, p = _host({"ui_image": False, "admin_uids": ""})
    with tmp:
        _start(p)
        p._load(_Event())
        ev = _Event("/迁移存档 列表")
        run_cmd(p, ev, p.cmd_migrate)
        out = "\n".join(ev.outputs)
        assert "只有管理员" in out, out              # 未配管理员 → 默认拒绝

    tmp2, p2 = _host({"ui_image": False, "admin_uids": "u1"})
    with tmp2:
        _start(p2)
        t2 = p2._load(_Event())
        ev2 = _Event("/迁移存档 列表")
        run_cmd(p2, ev2, p2.cmd_migrate)
        out2 = "\n".join(ev2.outputs)
        assert "共 1 个群有存档" in out2 and t2.uid in out2, out2

        ev3 = _Event("/迁移存档 预览 群 old-g new-g")
        run_cmd(p2, ev3, p2.cmd_migrate)
        assert "预览模式" in "\n".join(ev3.outputs)
        assert p2.trainers.list_players("old-g") == []        # 预览不落盘

        ev4 = _Event(f"/迁移存档 群 {t2.scope} qqofficial-group")
        run_cmd(p2, ev4, p2.cmd_migrate)
        out4 = "\n".join(ev4.outputs)
        assert "已迁移 1 位玩家的存档" in out4, out4
        assert p2.trainers.list_players("qqofficial-group") == [t2.uid]
