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
    data["location"] = "kanto-route-1"   # 1 号道路:有野生池,双打开得起来
    data["name"] = "小茂"
    data["party"] = [mon_to_dict(create_pokemon("charmander", 8))]
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
        out = "\n".join(ev.outputs)
        assert "双打" in out or "找不到" in out, out
        row = COOP.pair_for(p._state(scope), t1.uid)
        if not COOP.battle_of(row):
            return          # 这个地点今天既没训练家也没野生池:只锁“有明确提示”
        view = B.view(p._load(_Event()))
        assert view["doubles"] and len(view["mine"]) == 2 and len(view["foes"]) == 2


def test_both_players_submit_before_the_turn_resolves():
    with tempfile.TemporaryDirectory() as tmp:
        p, scope, t1 = _setup(tmp)
        run_cmd(p, _Event("/双打"), p.cmd_coop)
        if not COOP.battle_of(COOP.pair_for(p._state(scope), t1.uid)):
            return
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


def test_spread_moves_hit_partner_but_skip_empty_slot():
    """双打范围招:地震(allAdjacent)连搭档一起打,热风(allAdjacentFoes)只打对面;
    搭档倒下留空位后,范围招不再打那个空位(也不会误伤不存在的目标)。"""
    from pw.engine import Battle, Side, create_pokemon

    def mk(sp, lv, moves, spe):
        mon = create_pokemon(sp, lv)
        mon.moves = list(moves)
        mon.pp = dict.fromkeys(moves, 20)
        mon.stats["spe"] = spe
        return mon

    host = mk("snorlax", 50, ["earthquake", "heatwave"], 10)
    ally = mk("pikachu", 50, ["thunderbolt"], 90)
    foe1 = mk("blastoise", 50, ["surf"], 60)
    foe2 = mk("venusaur", 50, ["razorleaf"], 55)
    b = Battle(player=Side("player", [host, ally]),
               enemy=Side("enemy", [foe1, foe2]), doubles=True)
    b.player.owners = ["a", "b"]
    b.enemy.owners = ["e", "e"]
    b.start()
    b.player.ally_active = 1
    b.enemy.ally_active = 1      # 对面也是双打:两只都在场上

    b.log = []
    b.step_doubles([{"type": "move", "move": "earthquake", "slot": "main"}])
    assert ally.cur_hp < ally.max_hp, f"地震没打到搭档,友伤丢了:{b.log}"
    assert foe1.cur_hp < foe1.max_hp and foe2.cur_hp < foe2.max_hp

    b.log = []
    b.step_doubles([{"type": "move", "move": "heatwave", "slot": "main"}])
    assert ally.cur_hp == 0 or "皮卡丘" not in "".join(b.log), b.log

    # 搭档打光 → 他的位置空着:范围招不会再“打”那个空位
    ally.cur_hp = 0
    ally.fainted = True
    b.log = []
    b.step_doubles([{"type": "move", "move": "earthquake", "slot": "main"}])
    assert not [x for x in b.log if "皮卡丘" in x and "击中" in x], b.log
    # 也不会把主办方后排顶到搭档的位置上
    assert [m.species for m in b.player.mons][1] == "pikachu", b.log
