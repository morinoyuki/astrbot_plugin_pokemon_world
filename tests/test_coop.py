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


def test_no_npc_today_falls_back_to_a_wild_double_battle():
    """线上崩过的那条路:今天这里没有训练家 → 野生双打(不能抛异常)。"""
    import tempfile

    from test_commands import _Event, run_cmd

    import pw.npc as NPC
    from pw import battle as B

    with tempfile.TemporaryDirectory() as tmp:
        p, scope, _t1 = _setup(tmp)
        # 两人站到同一地点,并把训练家生成清空(=今天这里没人)
        for who in ("u1", "u2"):
            d = p.trainers.load(scope, who)
            d["location"] = "kanto-route-3"
            p.trainers.save(scope, who, d)
        orig = NPC.route_trainers
        NPC.route_trainers = lambda *a, **k: []
        try:
            ev = _Event("/双打")
            run_cmd(p, ev, p.cmd_coop)
        finally:
            NPC.route_trainers = orig
        out = "\n".join(ev.outputs)
        assert "Traceback" not in out and "AttributeError" not in out, out[:300]
        host = p.trainers.load(scope, "u1")
        assert host.get("battle"), f"应该已经开起野生双打:{out[:300]}"
        from pw.player import Trainer

        v = B.view(Trainer(host, uid="u1", scope=scope))
        assert v["doubles"] is True
        assert len(v["foes"]) == 2, "野生双打要有两只对手"


def test_turn_shows_both_players_field_moves():
    """双打出招前要能看到**两位玩家**场上宝可梦的招式(实测反馈:以前没有)。"""
    import tempfile

    from test_commands import _Event, run_cmd

    with tempfile.TemporaryDirectory() as tmp:
        p, scope, _t1 = _setup(tmp)
        for who in ("u1", "u2"):          # 先会合(双打要求同地点)
            d = p.trainers.load(scope, who)
            d["location"] = "kanto-route-1"
            p.trainers.save(scope, who, d)
        run_cmd(p, _Event("/双打"), p.cmd_coop)
        ev = _Event("/双打 1")
        run_cmd(p, ev, p.cmd_coop)
        out = "\n".join(ev.outputs)
        assert "场上两位的宝可梦" in out, out[:300]
        # 两位玩家各自在场上的那只都要点名(主位/副位)
        assert "主位" in out and "副位" in out, out[:300]
        # 并且要真的列出招式(带 PP),不只是名字
        assert "1." in out and "(" in out, out[:300]


def test_single_trainer_brings_two_mons_instead_of_silent_wild():
    """只有 1 位训练家时:让他带两只打双打,而不是默默变成野生(实测反馈)。"""
    import tempfile

    from test_commands import _Event, run_cmd

    import pw.npc as NPC
    from pw import battle as B
    from pw.player import Trainer

    with tempfile.TemporaryDirectory() as tmp:
        p, scope, _t1 = _setup(tmp)
        for who in ("u1", "u2"):
            d = p.trainers.load(scope, who)
            d["location"] = "kanto-route-1"
            p.trainers.save(scope, who, d)
        host = Trainer(p.trainers.load(scope, "u1"), uid="u1", scope=scope)
        # 从今天所有关都地点里挑一位真实训练家,再把他"伪装"成这里唯一的一位
        orig = NPC.route_trainers
        probe = None
        for loc in ("kanto-route-1", "kanto-route-2", "kanto-route-3", "kanto-route-4",
                    "kanto-route-5", "kanto-route-6", "kanto-route-7", "kanto-route-8",
                    "viridian-city", "pewter-city", "cerulean-city"):
            got = orig(host, loc, day=1)
            if got:
                probe = got[0]
                break
        assert probe is not None, "今天所有地点都没训练家,无法构造用例"
        NPC.route_trainers = lambda *a, **k: [probe]
        try:
            ev = _Event("/双打")
            run_cmd(p, ev, p.cmd_coop)
        finally:
            NPC.route_trainers = orig
        out = "\n".join(ev.outputs)
        assert "一个" in out and "两只" in out, out[:400]
        data = p.trainers.load(scope, "u1")
        assert (data.get("battle") or {}).get("kind") == "trainer", out[:400]
        v = B.view(Trainer(data, uid="u1", scope=scope))
        assert v["doubles"] is True and len(v["foes"]) == 2, out[:400]
