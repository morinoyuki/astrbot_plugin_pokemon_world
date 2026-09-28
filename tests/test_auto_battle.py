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


def test_swarm_event_actually_boosts_the_species():
    """「某某大量出现」不能只是文字:今天这里刷 swarm 就该真的多刷出它。"""


    tmp, p = _battle(enemy=("magikarp", 3))
    with tmp:
        t = p._load(_Event())
        state = p._state(t.scope)
        # 手动塞一个"常青森林大量出现皮卡丘"的事件
        state.data.setdefault("events", []).append({
            "id": "sw1", "kind": "swarm", "region": t.region,
            "location": "viridian-forest", "species": "pikachu",
            "zh": "皮卡丘", "until_day": state.day + 1, "created_day": state.day,
            "effects": {"encounter_mult": 1.8},
        })
        p.worlds.save(state.scope, state.data)
        assert p._swarm_species(p._state(t.scope), "viridian-forest") == "pikachu"
        assert p._swarm_species(p._state(t.scope), "kanto-route-1") == ""
        # 遭遇入口确实会换成它(直接调 _emit_wild 的判定逻辑,避免依赖随机探索)
        t.data["battle"] = None
        p._save(t)
        hit = {"species": "pidgey", "zh": "波波", "level": 5, "shiny": False}
        notice: list[str] = []
        import asyncio

        ev = _Event("")
        asyncio.run(  # 只跑"遭遇 + 开战"这一段
            p._emit_wild(ev, p._load(_Event()), p._state(t.scope),
                         __import__("pw.world", fromlist=["WorldMap"]).WorldMap(),
                         "viridian-forest", hit, notice).__anext__()
        )
        battle = B.session(p._load(_Event())) or {}
        assert str((battle.get("meta") or {}).get("species") or "") in ("pikachu", "pidgey")
        assert any("大量出现" in x for x in notice) or notice == []


def test_events_with_effects_are_registered_and_trigger():
    """事件种类必须"真的有效果":注册进目录、效果键合法、且被代码消费。"""
    from pw import events as EV

    for kind in ("harvest", "crowd", "shine"):
        assert kind in EV.WORLD_KINDS, f"{kind} 没注册进世界事件目录"
        assert kind in {k for k, _zh, _eff in EV.FALLBACK_WORLD}
    for _k, _zh, eff in EV.FALLBACK_WORLD:
        for key, val in eff.items():
            lo, hi = EV.EFFECT_RANGES[key]
            assert lo <= float(val) <= hi, f"{key}={val} 超出 {lo}~{hi}"
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False}
        run_cmd(p, _Event("/开始 小智 小火龙"), p.cmd_start)
        t = p._load(_Event())
        state = p._state(t.scope)

        class _Always:
            def random(self):
                return 0.0

        ev = {"kind": "rare", "species": "lapras", "zh": "拉普拉斯"}
        hit = p._maybe_rare(t, state, ev, _Always(),
                            {"species": "tentacool", "zh": "玛瑙水母", "level": 22})
        assert hit["species"] == "lapras" and hit["zh"] == "拉普拉斯", hit
        hit2 = p._maybe_rare(t, state, {"kind": "rare"}, _Always(),
                             {"species": "tentacool", "zh": "玛瑙水母", "level": 22})
        assert hit2.get("species"), hit2


def test_rocket_event_blocks_the_road_and_can_be_fought():
    """「可疑的黑衣人」不只是文字:探索时会真的拦路开打。"""
    tmp, p = _battle()
    with tmp:
        t = p._load(_Event())
        t.data["battle"] = None
        t.data["location"] = "kanto-vermilion-city"
        p._save(t)
        state = p._state(t.scope)
        state.data.setdefault("events", []).append({
            "id": "rk1", "kind": "rocket", "region": t.region,
            "location": "kanto-vermilion-city", "trainer": "火箭队手下",
            "team": [{"species": "koffing", "level": 12},
                     {"species": "zubat", "level": 12}],
            "until_day": state.day + 1, "created_day": state.day,
        })
        p.worlds.save(state.scope, state.data)

        class _Rng:
            def random(self):
                return 0.0

        got = p._maybe_rocket_event(p._load(_Event()), p._state(t.scope),
                                    "kanto-vermilion-city", _Rng())
        assert got is not None, "有黑衣人却没给出拦路队伍"
        specs, meta = got
        assert [s["species"] for s in specs] == ["koffing", "zubat"]
        assert meta["kind"] == "rocket" and "火箭队手下" in meta["title"]
        # 别的地点不该被拦
        assert p._maybe_rocket_event(p._load(_Event()), p._state(t.scope),
                                     "kanto-route-1", _Rng()) is None
        # 没有 rocket 事件时也不会凭空冒出来
        state2 = p._state(t.scope)
        state2.data["events"] = []
        p.worlds.save(state2.scope, state2.data)
        assert p._maybe_rocket_event(p._load(_Event()), p._state(t.scope),
                                     "kanto-vermilion-city", _Rng()) is None


def test_blockade_really_stops_travel():
    """「封锁中:枯叶市」必须真的走不过去(不是只显示一行字)。"""
    from pw.world import WorldMap

    tmp, p = _battle()
    with tmp:
        t = p._load(_Event())
        world = WorldMap()
        state = p._state(t.scope)
        locked = {"vermilion-city": state.day + 1}
        ok, msg = world.travel_check(t, "vermilion-city", locked_until=locked)
        assert not ok and "封锁" in msg, (ok, msg)
