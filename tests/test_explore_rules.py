"""探索捡道具的"每节点每月上限" + 非野生对战禁止逃跑。

两条规则都是玩家提的:
1. "探索捡道具需要限制单个节点一个月内不能超过多少多少个,超过后一直提示没东西";
2. "除了野生以外战斗都禁止 run"。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tests"))

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402


def _plugin(tmp, *, cap: int = 3):
    p = _Cmd(tmp)
    p.config = {"ui_image": False, "battle_image": False, "quest_enable": False,
                "explore_item_cap": cap}
    run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
    t = p._load(_Event(""))
    t.data["region"] = "kanto"
    t.data["location"] = "kanto-route-1"
    p._save(t)
    return p


def _explore(p, cmd="/探索 道具"):
    ev = _Event(cmd)
    run_cmd(p, ev, p.cmd_explore)
    return "".join(str(x) for x in ev.outputs)


def _n_items(t) -> int:
    return sum(int(v or 0) for v in (t.data.get("bag") or {}).values())


# ── ① 捡道具上限 ────────────────────────────────────────────────
def test_item_find_cap_is_exact_and_then_reports_nothing():
    """捡满上限后不再给东西,而且**不会超过**上限。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp, cap=3)
        for _ in range(6):
            _explore(p)
        t = p._load(_Event(""))
        used = (t.data.get("item_finds") or {}).get("kanto-route-1") or {}
        assert int(used.get("n") or 0) == 3, t.data.get("item_finds")
        # 后几次必须都是"捡完了"的提示
        out = _explore(p)
        assert "捡完" in out and "3/3" in out, out
        assert _n_items(p._load(_Event(""))) == _n_items(t), "超限不该再给道具"


def test_item_cap_is_per_node():
    """上限按**节点**分别计算,换地方还能捡。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp, cap=2)
        for _ in range(3):
            _explore(p)
        assert "捡完" in _explore(p)
        t = p._load(_Event(""))
        t.data["location"] = "kanto-route-2"
        p._save(t)
        out = _explore(p)
        assert "发现" in out and "1/2" in out, out


def test_item_cap_resets_next_month():
    """跨月重置(把记录改成上个月即可验证)。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp, cap=2)
        for _ in range(3):
            _explore(p)
        assert "捡完" in _explore(p)
        t = p._load(_Event(""))
        t.data["item_finds"]["kanto-route-1"] = {"month": "2020-01", "n": 99}
        p._save(t)
        out = _explore(p)
        assert "发现" in out, out
        box = (p._load(_Event("")).data.get("item_finds") or {}).get("kanto-route-1") or {}
        assert int(box.get("n") or 0) <= 2, box


def test_generic_explore_also_respects_the_cap():
    """默认 `/探索` 的"捡到东西"分支也要计数 —— 否则绕开上限。"""
    from pw.util import game_day

    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp, cap=1)
        t = p._load(_Event(""))
        # 冻结当天,免得"个人事件送道具"干扰"背包总量不变"的断言
        st = p._state("g10086")
        st.data["day"] = game_day()
        st.data["last_roll_day"] = game_day()
        st.data["player_events"] = {}
        p._save_state(st)
        import pw_plugin.main as PM

        t.data["item_finds"] = {"kanto-route-1": {"month": PM._month_key(), "n": 1}}
        t.data["steps"] = 777
        p._save(t)
        before = _n_items(t)
        # 默认探索是掷骰决定的(野生/训练家/道具),多跑几轮才能命中"捡东西"分支
        seen = ""
        from pw import battle as B

        for _ in range(60):
            # 探索可能开战 → 先清掉战斗,否则后续探索会被"先打完这场"挡住
            cur = p._load(_Event(""))
            if B.in_battle(cur):
                cur.data["battle"] = None
                p._save(cur)
            out = _explore(p, "/探索")
            if "捡完" in out:
                seen = out
                break
        assert seen, "默认探索的捡东西分支没有计入上限"
        assert _n_items(p._load(_Event(""))) == before, "超限后默认探索也不该给道具"


def test_item_cap_zero_means_unlimited():
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp, cap=0)
        for _ in range(4):
            out = _explore(p)
            assert "捡完" not in out, out


# ── ② 非野生对战禁止逃跑 ─────────────────────────────────────────
def test_run_is_banned_outside_wild_and_costs_no_turn():
    """训练家/道馆/大赛:拒绝 runaway,且**不消耗回合**(敌人不能白打一下)。"""
    from pw import battle as B
    from pw.util import game_day

    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        for kind in ("trainer", "gym", "tournament", "rocket", "league"):
            t = p._load(_Event(""))
            t.data["battle"] = None          # 上一场要清掉才能开下一场
            B.start(t, [{"species": "pidgey", "level": 5}], kind=kind, wild=False,
                    meta={"kind": kind, "title": kind}, day=game_day())
            p._save(t)
            turn0 = B.session(t)["battle"]["turn"]
            ev = _Event("/对战 run")
            run_cmd(p, ev, p.cmd_battle)
            out = "".join(str(x) for x in ev.outputs)
            assert "不能逃跑" in out, f"{kind}: {out[:120]}"
            t2 = p._load(ev)
            assert B.in_battle(t2), f"{kind} 不该结束战斗"
            turn1 = B.session(t2)["battle"]["turn"]
            assert turn1 == turn0, f"{kind} 逃跑失败却消耗了回合:{turn0}→{turn1}"


def test_run_works_in_wild_battles():
    from pw import battle as B
    from pw.util import game_day

    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        t = p._load(_Event(""))
        B.start(t, [{"species": "caterpie", "level": 2}], kind="wild", wild=True,
                meta={"kind": "wild", "title": "野生"}, day=game_day())
        p._save(t)
        # 速度压制下多试几次必定逃掉
        for _ in range(6):
            if not B.in_battle(p._load(_Event(""))):
                break
            ev = _Event("/对战 run")
            run_cmd(p, ev, p.cmd_battle)
        assert not B.in_battle(p._load(_Event(""))), "野生对战应该能逃掉"


def test_battle_hint_hides_run_for_trainer_battles():
    """提示行不能在训练家战里推荐 `/对战 run`(玩家会白试一次)。"""
    from pw import battle as B
    from pw.util import game_day

    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        t = p._load(_Event(""))
        B.start(t, [{"species": "pidgey", "level": 5}], kind="gym", wild=False,
                meta={"kind": "gym", "title": "道馆战"}, day=game_day())
        p._save(t)
        hint = p._battle_hint(p._load(_Event("")))
        assert "/对战 run" not in hint, hint
        assert "forfeit" in hint, hint
        # 野生反过来
        t = p._load(_Event(""))
        t.data["battle"] = None
        B.start(t, [{"species": "pidgey", "level": 5}], kind="wild", wild=True,
                meta={"kind": "wild", "title": "野生"}, day=game_day())
        p._save(t)
        hint2 = p._battle_hint(p._load(_Event("")))
        assert "/对战 run" in hint2 and "/捕捉" in hint2, hint2


def test_type_targeting_is_gone():
    """按属性定点探索已移除 —— 别让这个入口悄悄回来。

    玩家提的要求:"把 /探索 属性 这个命令删除,不要让玩家能指定属性"。
    能指定属性会让"真实野外分布"变成点菜单。
    """
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        for cmd in ("/探索 属性 水", "/探索 水", "/探索 火", "/探索 属性 皮卡丘"):
            out = _explore(p, cmd)
            assert "用法" in out, f"{cmd} 不该被接受:{out[:80]}"
        # 正当目标仍然可用
        out = _explore(p, "/探索 道具")
        assert "发现" in out or "捡完" in out, out
