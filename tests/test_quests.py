"""支线任务系统测试。

重点验证"强规则":任务必须靠真实行为推进、奖励只发一次、LLM 输出会被裁剪、
以及 LLM 不可用时能本地兜底。
"""

from __future__ import annotations

import asyncio
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
        # 把"今天已经滚动过"打上,避免世界事件(如"捡到钱包"送钱)混进来 ——
        # 事件送钱是正常玩法,但会让这条断言变成随机失败
        from pw.util import game_day

        st = p._state("g10086")
        st.data["day"] = game_day()
        st.data["last_roll_day"] = game_day()
        st.data["player_events"] = {}
        p._save_state(st)
        t = p._load(ev)
        money0 = t.money
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
        res = TurnResult(outcome="win", growth=["新叶喵: +14 EXP → Lv6"], levels_gained=1)
        lines = p._quests_after_battle(
            t, {"kind": "wild", "species": "caterpie", "types": ["Bug"], "method": "walk"}, res,
        )
        assert lines

        # ③ 不使用道具取胜:用过道具则不算。注意 used_item 记在 meta 上 ——
        # 战斗结束时 data["battle"] 已被清空,记在会话里读不到(真实 bug)
        _seed("no_item_win")
        assert p._quests_after_battle(
            t,
            {"kind": "wild", "species": "caterpie", "types": ["Bug"], "used_item": True},
            TurnResult(outcome="win"),
        ) == []
        assert p._quests_after_battle(
            t,
            {"kind": "wild", "species": "caterpie", "types": ["Bug"], "used_item": False},
            TurnResult(outcome="win"),
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


def test_sanitize_survives_json_dirty_values():
    """LLM 返回的脏数据(Infinity / 非 dict)必须能降级,不能让整批委托丢失。

    json.loads 默认接受 Infinity,int(float("inf")) 抛 OverflowError;
    非 dict 的 items 会 AttributeError —— 两种都会让当天 0 委托且不再回退。
    """
    from pw import events as EV

    with tempfile.TemporaryDirectory() as tmp:
        t = _trainer(tmp)
        inf = float("inf")
        cases = [
            {"objective": {"kind": "catch", "count": inf}},
            {"objective": {"kind": "catch", "count": 1}, "reward": {"money": inf}},
            {"objective": {"kind": "catch", "count": 1}, "reward": {"items": "abc"}},
            {"objective": {"kind": "catch", "count": 1}, "reward": {"items": ["a"]}},
            {"objective": {"kind": "catch", "count": 1}, "reward": 5},
        ]
        for raw in cases:
            q = Q.sanitize_quest(raw, t, 1)      # 不能抛异常
            assert q is None or isinstance(q, dict)
            assert q is None or q["objective"]["count"] >= 1

        from pw.worldstate import WorldState

        state = WorldState({"day": 1, "events": []}, "g1")
        ev = EV.sanitize_world_event({"kind": "rocket", "team": [{"level": inf}]}, state, 1)
        assert ev is None or isinstance(ev, dict)


def test_used_item_blocks_no_item_quest_end_to_end():
    """真实指令流:用过道具后再取胜,「不使用道具取胜」委托不应完成。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "quest_enable": False, "battle_image": False}
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        t = p._load(ev)
        t.data["location"] = "kanto-route-1"
        t.data["quests"] = {
            "active": [{
                "id": "ni1", "day": 1, "expire_day": 10 ** 9, "giver": "退休训练家",
                "title": "硬实力的证明", "desc": "不用道具赢 1 场",
                "objective": {"kind": "no_item_win", "count": 1},
                "progress": 0, "reward": {"money": 500, "items": {}},
                "region": "kanto", "state": "active",
            }],
            "done": [], "counter": 1, "day": 1,
        }
        t.add_item("potion", 3)
        p._save(t)
        from pw import battle as B
        from pw.engine import create_pokemon

        strong = create_pokemon("charizard", 80).to_dict()
        strong["id"] = "m1"
        t = p._load(ev)
        t.data["party"] = [strong]
        p._save(t)
        B.start(t, [{"species": "rattata", "level": 3}], kind="wild", wild=True,
                meta={"kind": "wild", "species": "rattata", "types": ["Normal"],
                      "method": "walk", "title": "野生的 小拉达"}, day=1)
        p._save(t)

        used = False
        for _ in range(12):
            t = p._load(ev)
            if t.data.get("battle") is None:
                break
            if not used:
                ev2 = _Event("/对战 item 伤药")
                run_cmd(p, ev2, p.cmd_battle)
                used = True
            else:
                ev2 = _Event(f"/对战 move {t.party[0]['moves'][0]}")
                run_cmd(p, ev2, p.cmd_battle)
        t2 = p._load(ev2)
        actives = [q["id"] for q in Q.active(t2)]
        assert "ni1" in actives, "用过道具取胜不该完成该委托"


def test_battle_exposes_structured_levels_and_evolutions():
    """TurnResult 必须带结构化的升级/进化信息 —— 任务系统不该解析展示文案。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "quest_enable": False, "battle_image": False}
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        t = p._load(ev)
        t.data["location"] = "kanto-route-1"
        from pw import battle as B
        from pw.dex import get_dex
        from pw.engine import create_pokemon

        weak = create_pokemon("caterpie", 20).to_dict()
        # 把经验压到刚好差一点点升级 → 赢下这一场必定升级
        dex = get_dex()
        rate = dex.growth_of("caterpie")
        weak["exp"] = max(0, dex.exp_for_level(rate, 21) - 3)
        weak["id"] = "m1"
        t.data["party"] = [weak]
        p._save(t)
        B.start(t, [{"species": "magikarp", "level": 2}], kind="wild", wild=True,
                meta={"kind": "wild", "species": "magikarp", "types": ["Water"],
                      "title": "野生的 鲤鱼王"}, day=1)
        p._save(t)
        res = None
        for _ in range(10):
            t = p._load(ev)
            if t.data.get("battle") is None:
                break
            res = B.take_turn(t, "move tackle", day=1)
            if res.finished:
                break
        assert res is not None and res.finished
        assert isinstance(res.levels_gained, int) and res.levels_gained >= 1, \
            "打赢弱对手应当升级,且 levels_gained 要有值"
        assert isinstance(res.evolved, list)


def test_fallback_quest_pools_and_completion_guards():
    """兜底生成器:有水域/商店时能刷出钓鱼/消费委托;走遍全图后不再刷旅行委托。"""
    from pw.world import WorldMap

    with tempfile.TemporaryDirectory() as tmp:
        t = _trainer(tmp)
        world = WorldMap()
        water = next(
            (loc for loc in world.nodes("kanto")
             if any("rod" in str(p.get("method") or "").lower()
                    for p in (world.wild_pools(loc) or []))),
            "",
        )
        assert water, "关都应当有可钓鱼的地点"
        t.data["location"] = water
        kinds = {Q.fallback_quest(t, 5, idx=i)["objective"]["kind"] for i in range(200)}
        assert "catch_fish" in kinds, f"有水域时应当能刷出钓鱼委托,实际 {kinds}"

        shop = next((loc for loc in world.nodes("kanto")
                     if "mart" in (world.services(loc) or [])), "")
        if shop:
            t.data["location"] = shop
            kinds = {Q.fallback_quest(t, 6, idx=i)["objective"]["kind"] for i in range(200)}
            assert "shop_spend" in kinds, f"有商店时应当能刷出消费委托,实际 {kinds}"

        # 走遍全图 + 见满图鉴后,不应再生成 travel/dex 委托
        from pw.dex import get_dex
        from pw.world import REGION_ORDER

        t.data["visited"] = [loc for r in REGION_ORDER for loc in world.nodes(r)]
        t.data["dex_seen"] = list(get_dex().species.keys())
        kinds = {Q.fallback_quest(t, 7, idx=i)["objective"]["kind"] for i in range(200)}
        assert "travel" not in kinds and "dex" not in kinds, f"实际 {kinds}"


def test_daily_quests_never_duplicate():
    """同一天刷出的委托不许重复 —— 标题重复或目标完全相同都算重复。

    实测 LLM 会返回两条标题一模一样的委托(「捕虫少年阿明「帮忙补充图鉴」、
    迷你裙少女「帮忙补充图鉴」」),玩家看到就像同一个任务刷了两遍。
    """
    from pw import quests as Q
    from pw.player import new_trainer

    def dump(t):
        return [
            (str(q.get("title")), Q._obj_key(q)) for q in Q.active(t)
        ]

    # ① LLM 故意返回两条完全一样的委托
    class _DupNarrator:
        def available(self):
            return True

        async def json(self, *a, **kw):
            q = {
                "giver": "捕虫少年阿明",
                "title": "帮忙补充图鉴",
                "desc": "帮我抓 2 只。",
                "objective": {"kind": "catch", "count": 2},
                "reward": {"money": 200, "items": {"poke-ball": 1}},
            }
            return {"quests": [dict(q), dict(q, giver="迷你裙少女")]}

    t = new_trainer("u1", "g1", "小智", starter="新叶喵", day=1)
    added = asyncio.run(Q.roll_daily_async(t, 1, _DupNarrator()))
    titles = [q["title"] for q in added]
    keys = [Q._obj_key(q) for q in added]
    assert len(titles) == len(set(titles)), f"标题重复:{titles}"
    assert len(keys) == len(set(keys)), f"目标重复:{keys}"
    assert len(dump(t)) == len(set(dump(t)))

    # ② 本地生成器:同一天多次刷新/多天也不能撞(含与**已有**委托比较)
    t2 = new_trainer("u2", "g1", "小智", starter="新叶喵", day=1)
    for day in range(1, 8):
        Q.roll_daily(t2, day)
        rows = dump(t2)
        assert len(rows) == len(set(rows)), f"第 {day} 天出现重复委托:{rows}"
        assert len(Q.active(t2)) <= Q.MAX_ACTIVE
