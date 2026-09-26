"""支线任务系统测试。

重点验证"强规则":任务必须靠真实行为推进、奖励只发一次、LLM 输出会被裁剪、
以及 LLM 不可用时能本地兜底。
"""

from __future__ import annotations

import sys
import tempfile

import pytest

# test_commands 会把仓库根目录加进 sys.path 并加载插件包,这里复用它的宿主对象
from test_commands import _Cmd, _Event, run_cmd

from pw import quests as Q
from pw.player import new_trainer


def _trainer(tmp, uid="u1"):
    return new_trainer(uid, "g1", "小智", starter="新叶喵")


def test_daily_roll_is_idempotent_and_bounded(tmp="/tmp"):
    with tempfile.TemporaryDirectory() as tmp:
        t = _trainer(tmp)
        first = Q.roll_daily(t, 1)
        assert len(first) == Q.DAILY_COUNT
        # 同一天再调不重复生成
        assert Q.roll_daily(t, 1) == []
        # 上限 MAX_ACTIVE
        Q.roll_daily(t, 2)
        Q.roll_daily(t, 3)
        assert len(Q.active(t)) <= Q.MAX_ACTIVE
        # 每条任务都有目标与奖励,数量 ≥ 1(不能"什么都不用做")
        for q in Q.active(t):
            obj = q["objective"]
            assert int(obj["count"]) >= 1
            assert obj["kind"] in Q.OBJECTIVES
            assert (q["reward"]["money"] or q["reward"]["items"])


def test_progress_requires_real_action():
    with tempfile.TemporaryDirectory() as tmp:
        t = _trainer(tmp)
        # 手动塞一条"抓 2 只虫属性"的委托
        t.data["quests"] = {
            "active": [
                {
                    "id": "q1-1",
                    "day": 1,
                    "expire_day": 10 ** 9,
                    "giver": "捕虫少年阿明",
                    "title": "收集虫子",
                    "desc": "抓 2 只虫属性",
                    "objective": {"kind": "catch_type", "type": "Bug", "count": 2},
                    "progress": 0,
                    "reward": {"money": 500, "items": {"poke-ball": 2}},
                    "region": "kanto",
                    "state": "active",
                }
            ],
            "done": [],
            "counter": 1,
            "day": 1,
        }
        # 属性不匹配 → 不涨进度
        assert Q.note(t, "catch", species="pidgey", types=["Normal", "Flying"]) == []
        assert Q.active(t)[0]["progress"] == 0
        # 无关事件 → 不涨进度
        assert Q.note(t, "shop", amount=9999) == []
        assert Q.active(t)[0]["progress"] == 0
        # 匹配 → 涨 1
        assert Q.note(t, "catch", species="caterpie", types=["Bug"]) == []
        assert Q.active(t)[0]["progress"] == 1
        money0, ball0 = t.money, t.count("poke-ball")
        # 第二次达标 → 完成并发奖
        lines = Q.note(t, "catch", species="weedle", types=["Bug"])
        assert lines and "委托完成" in lines[0]
        assert t.money == money0 + 500
        assert t.count("poke-ball") == ball0 + 2
        assert Q.active(t) == []
        assert "q1-1" in Q.completed_ids(t)


def test_reward_paid_once():
    """奖励只发一次,重复推进不会重复发钱。"""
    CALLS = {
        "catch": ("catch", {"species": "x", "types": ["Normal"], "method": "walk"}),
        "defeat": ("win", {"is_trainer": False, "types": ["Normal"], "used_item": False}),
        "level_up": ("level_up", {"levels": 99}),
        "travel": ("travel", {"new": True}),
        "steps": ("steps", {"steps": 99999}),
        "dex": ("dex", {"species": "x"}),
        "shop_spend": ("shop", {"amount": 999999}),
        "evolve": ("evolve", {"species": "x"}),
        "catch_fish": ("catch", {"species": "x", "types": [], "method": "fishing"}),
        "no_item_win": ("win", {"is_trainer": True, "used_item": False}),
        "defeat_trainer": ("win", {"is_trainer": True, "used_item": False}),
        "gym": ("win", {"kind": "gym", "is_trainer": True, "used_item": False}),
        "catch_type": ("catch", {"species": "x", "types": ["Bug"], "method": ""}),
        "defeat_type": ("win", {"is_trainer": False, "types": ["Bug"], "used_item": False}),
    }
    with tempfile.TemporaryDirectory() as tmp:
        t = _trainer(tmp)
        Q.roll_daily(t, 1)
        q = Q.active(t)[0]
        # 只留这一条,避免另一条委托的奖励干扰断言
        t.data["quests"]["active"] = [q]
        kind = q["objective"]["kind"]
        assert kind in CALLS, f"本地生成器产出了未覆盖的目标类型 {kind}"
        event, kw = CALLS[kind]
        need = int(q["objective"]["count"])
        money0 = t.money
        for _ in range(need + 3):          # 多推几次,确认不会重复发奖
            Q.note(t, event, **kw)
        assert Q.active(t) == []
        assert t.money - money0 == int(q["reward"]["money"])
        Q.note(t, event, **kw)
        assert t.money - money0 == int(q["reward"]["money"])


def test_llm_output_is_sanitized():
    with tempfile.TemporaryDirectory() as tmp:
        t = _trainer(tmp)
        # 1) 非法目标类型 → 拒绝
        assert Q.sanitize_quest({"objective": {"kind": "hack", "count": 1}}, t, 1) is None
        # 2) 数量被裁剪到区间内
        q = Q.sanitize_quest(
            {
                "giver": "大木博士",
                "title": "抓虫",
                "objective": {"kind": "catch_type", "type": "Bug", "count": 999},
                "reward": {"money": 999999, "items": {"master-ball": 9, "poke-ball": 99}},
            },
            t, 1,
        )
        assert q is not None
        assert q["objective"]["count"] == Q.OBJECTIVES["catch_type"]["max"]
        assert q["reward"]["money"] == Q.MONEY_MAX
        # 大师球不在奖励池里、精灵球数量也被压到上限
        assert "master-ball" not in q["reward"]["items"]
        assert q["reward"]["items"]["poke-ball"] == Q.REWARD_ITEMS["poke-ball"]["max"]
        # 3) 属性名必须是真实属性
        assert Q.sanitize_quest(
            {"objective": {"kind": "catch_type", "type": "不存在的属性", "count": 1}}, t, 1
        ) is None
        # 4) 奖励为 0 时至少补一份保底金钱
        q2 = Q.sanitize_quest(
            {"objective": {"kind": "catch", "count": 1}, "reward": {"money": 0, "items": {}}},
            t, 1,
        )
        assert q2 and q2["reward"]["money"] >= Q.MONEY_MIN


def test_sanitize_rejects_unobtainable_species():
    with tempfile.TemporaryDirectory() as tmp:
        t = _trainer(tmp)
        # catch_species 不在 LLM 可选目录里 → 直接拒绝(避免生成做不完的委托)
        assert Q.sanitize_quest(
            {"objective": {"kind": "catch_species", "species": "mewtwo", "count": 1}}, t, 1
        ) is None
        # 但本地生成器可以产出 catch_species,且物种必须是本地池里的
        for i in range(40):
            q = Q.fallback_quest(t, 3, idx=i)
            obj = q["objective"]
            assert obj["kind"] in Q.OBJECTIVES
            if obj.get("species"):
                from pw.world import WorldMap

                assert WorldMap().locations_with_species(str(obj["species"]), limit=1)


@pytest.mark.asyncio
async def test_roll_daily_async_falls_back_without_llm():
    with tempfile.TemporaryDirectory() as tmp:
        t = _trainer(tmp)

        class _Dead:
            def available(self):
                return False

        added = await Q.roll_daily_async(t, 1, _Dead())
        assert len(added) == Q.DAILY_COUNT

        class _Boom:
            def available(self):
                return True

            async def json(self, *a, **kw):
                raise RuntimeError("boom")

        t2 = _trainer(tmp, uid="u2")
        added2 = await Q.roll_daily_async(t2, 1, _Boom())
        assert len(added2) == Q.DAILY_COUNT  # LLM 炸了也要有委托


def test_expire_and_abandon():
    with tempfile.TemporaryDirectory() as tmp:
        t = _trainer(tmp)
        Q.roll_daily(t, 1)
        first = Q.active(t)[0]["id"]
        # 3 天后过期
        Q.roll_daily(t, 1 + Q.EXPIRE_DAYS)
        assert first not in [q["id"] for q in Q.active(t)]
        # 放弃
        n = len(Q.active(t))
        assert n > 0
        q = Q.abandon(t, 1)
        assert q and len(Q.active(t)) == n - 1
        assert Q.abandon(t, 99) is None


def test_quest_command_renders_image_and_text():
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": True, "battle_image_scale": 2}
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        t = p._load(ev)
        Q.roll_daily(t, 1)
        p._save(t)
        ev2 = _Event("/任务")
        run_cmd(p, ev2, p.cmd_quest)
        joined = "".join(ev2.outputs)
        assert "<chain:" in joined
        assert "委托" in joined


def test_travel_and_shop_advance_quests():
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "quest_enable": False}
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        t = p._load(ev)
        t.data["quests"] = {
            "active": [
                {
                    "id": "t1", "day": 1, "expire_day": 10 ** 9, "giver": "旅行者",
                    "title": "旅行见闻", "desc": "去 1 个新地方",
                    "objective": {"kind": "travel", "count": 1},
                    "progress": 0, "reward": {"money": 300, "items": {}},
                    "region": "kanto", "state": "active",
                }
            ],
            "done": [], "counter": 1, "day": 1,
        }
        p._save(t)
        money0 = p._load(ev).money
        assert t.location == "pallet-town"
        ev2 = _Event("/前往 1号道路")   # 真新镇只与 1 号道路相邻
        run_cmd(p, ev2, p.cmd_go)
        t2 = p._load(ev2)
        assert Q.active(t2) == []          # 到达新地点 → 完成
        assert t2.money == money0 + 300


def test_fishing_objective_needs_a_rod_catch():
    """「钓鱼捕获」类委托必须靠钓鱼推进 —— 这曾经因为读错 encounter 的字段名而永远做不完。"""
    import pw_plugin  # noqa: F401  (确保插件包已加载)
    from test_commands import _Cmd

    main_mod = sys.modules["pw_plugin.main"]

    # 遭遇方式归一:rod/fish → fish,surf → surf
    assert main_mod._primary_method(["Old-Rod", "Good-Rod"]) == "fish"
    assert main_mod._primary_method(["Surf"]) == "surf"
    assert main_mod._primary_method(["Walk"]) == "walk"
    assert main_mod._primary_method(None) == ""

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "quest_enable": False}
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        t = p._load(ev)
        t.data["quests"] = {
            "active": [
                {
                    "id": "f1", "day": 1, "expire_day": 10 ** 9, "giver": "钓鱼老手",
                    "title": "钓鱼收获", "desc": "钓 1 只宝可梦上来",
                    "objective": {"kind": "catch_fish", "count": 1},
                    "progress": 0, "reward": {"money": 400, "items": {}},
                    "region": "kanto", "state": "active",
                }
            ],
            "done": [], "counter": 1, "day": 1,
        }
        p._save(t)
        money0 = p._load(ev).money
        # 陆地捕获不算
        Q.note(t, "catch", species="pidgey", types=["Normal"], method="walk")
        assert Q.active(t) and Q.active(t)[0]["progress"] == 0
        # 钓上来的才算
        lines = Q.note(t, "catch", species="magikarp", types=["Water"], method="fish")
        assert lines and Q.active(t) == []
        assert t.money == money0 + 400


def test_explore_metadata_carries_species_and_method():
    """探索开战时写进 meta 的物种/属性/方式必须齐全(任务系统靠它结算)。"""
    import pw_plugin  # noqa: F401

    from pw import battle as B

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "quest_enable": False, "battle_image": False}
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        t = p._load(ev)
        t.data["location"] = "kanto-route-1"
        p._save(t)
        # 反复探索直到进入野生对战
        for _ in range(30):
            ev2 = _Event("/探索")
            run_cmd(p, ev2, p.cmd_explore)
            sess = B.session(p._load(ev2))
            meta = dict((sess or {}).get("meta") or {})
            if meta.get("kind") == "wild":
                assert meta.get("species"), "野生战斗 meta 缺 species"
                assert meta.get("types"), "野生战斗 meta 缺 types"
                assert isinstance(meta.get("method"), str) and meta["method"]
                break
        else:
            pytest.skip("这次探索没抽到野生对战")


def test_battle_hook_advances_catch_and_win_quests():
    """战斗结算钩子:捕获/击败都要换算成任务进度(所有战斗类型共用这条路径)。"""
    import pw_plugin  # noqa: F401

    from pw.battle import TurnResult

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "quest_enable": False}
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        t = p._load(ev)

        def _seed(kind, **extra):
            t.data["quests"] = {
                "active": [
                    {
                        "id": "b1", "day": 1, "expire_day": 10 ** 9, "giver": "测试",
                        "title": "测试委托", "desc": "",
                        "objective": {"kind": kind, "count": 1, **extra},
                        "progress": 0, "reward": {"money": 0, "items": {}},
                        "region": "kanto", "state": "active",
                    }
                ],
                "done": [], "counter": 1, "day": 1,
            }

        # ① 捕获 → catch 类委托完成
        _seed("catch")
        res = TurnResult(outcome="caught")
        lines = p._quests_after_battle(
            t, {"kind": "wild", "species": "pidgey", "types": ["Normal", "Flying"],
                "method": "walk"}, res,
        )
        assert lines and "委托完成" in lines[0]

        # ② 以指定属性击败野生 → defeat_type 完成
        _seed("defeat_type", type="Bug")
        res = TurnResult(outcome="win", growth=["新叶喵 升到了 Lv6!"])
        lines = p._quests_after_battle(
            t, {"kind": "wild", "species": "caterpie", "types": ["Bug"], "method": "walk"}, res,
        )
        assert lines

        # ③ 不使用道具取胜:用过道具则不算
        _seed("no_item_win")
        t.data["battle"] = {"used_item": True}
        assert p._quests_after_battle(
            t, {"kind": "wild", "species": "caterpie", "types": ["Bug"]}, TurnResult(outcome="win")
        ) == []
        t.data["battle"] = {"used_item": False}
        assert p._quests_after_battle(
            t, {"kind": "wild", "species": "caterpie", "types": ["Bug"]}, TurnResult(outcome="win")
        )

        # ④ 训练家战:对手物种从队伍首发取
        _seed("defeat_trainer")
        lines = p._quests_after_battle(
            t, {"kind": "trainer", "team": [{"species": "geodude", "level": 12}]},
            TurnResult(outcome="win"),
        )
        assert lines


def test_command_decorators_are_not_misplaced():
    """AST 静态检查:每个函数最多挂一个 @filter.command。

    这个检查来自一次真实事故:新增指令时把 `@filter.command("任务")` 插到了
    `@filter.command("重置世界")` 与 `cmd_reset` 之间,结果 `/重置世界` 被悄悄
    重定向到了新函数、`cmd_reset` 反而没有任何指令绑定 —— 而按方法名直接调用的
    测试完全看不出来。
    """
    import ast
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "main.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        cmds = [
            d for d in node.decorator_list
            if isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "command"
        ]
        counts[node.name] = len(cmds)
        for d in cmds:
            if d.args and isinstance(d.args[0], ast.Constant):
                names[str(d.args[0].value)] = node.name
    bad = {k: v for k, v in counts.items() if v > 1}
    assert not bad, f"这些函数挂了多个 @filter.command:{bad}"
    # 关键指令必须绑在预期的方法上
    assert names.get("重置世界") == "cmd_reset"
    assert names.get("任务") == "cmd_quest"
    assert names.get("开始") == "cmd_start"
