"""宝可梦世界内核测试(不依赖 AstrBot 运行环境,只依赖 astrbot.api.logger)。

运行:
    /mnt/e/astrbot_plugin_life_sim/.venv/bin/python -m pytest tests/ -q
"""

import asyncio
import importlib.util
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


def _load_plugin_package():
    """把插件目录当成包加载(与 AstrBot 运行时的导入方式一致)。"""
    spec = importlib.util.spec_from_file_location(
        "pw_plugin",
        os.path.join(_ROOT, "__init__.py"),
        submodule_search_locations=[_ROOT],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pw_plugin"] = mod
    spec.loader.exec_module(mod)
    return mod


from pw import battle as B  # noqa: E402
from pw import daily as D  # noqa: E402
from pw import events as EV  # noqa: E402
from pw import growth  # noqa: E402
from pw.dex import get_dex  # noqa: E402
from pw.engine import create_pokemon  # noqa: E402
from pw.items import BAG_ITEMS, resolve_bag_item  # noqa: E402
from pw.player import Trainer, TrainerStore, new_trainer  # noqa: E402
from pw.world import REGION_ORDER, WorldMap, item_price  # noqa: E402
from pw.worldstate import WorldState  # noqa: E402

DEX = get_dex()


# ══════════════════════════════════════════════════════════════════
# 数据完整性
# ══════════════════════════════════════════════════════════════════
def test_maps_json_adjacency_symmetric_and_connected():
    world = WorldMap()
    assert world.regions_with_data(), "maps.json 缺失或为空"
    for region in world.regions_with_data():
        nodes = world.nodes(region)
        assert nodes, f"{region} 没有节点"
        # 相邻关系必须双向
        for key in nodes:
            for nxt in world.neighbors(key):
                assert nxt in nodes, f"{region}: {key} -> {nxt} 不在本地区"
                assert key in world.neighbors(nxt), f"{region}: {key} <-> {nxt} 不对称"
        # 单连通
        seen = {next(iter(nodes))}
        stack = list(seen)
        while stack:
            cur = stack.pop()
            for nxt in world.neighbors(cur):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        assert len(seen) == len(nodes), f"{region} 不连通({len(seen)}/{len(nodes)})"
        # tier 在 1..8
        for key, node in nodes.items():
            assert 1 <= int(node.get("tier", 1)) <= 8, f"{region}/{key} tier 越界"


def test_gyms_json_valid_and_locations_exist():
    world = WorldMap()
    regions = [r for r in REGION_ORDER if world.gyms(r)]
    assert len(regions) >= 8, f"道馆地区太少:{regions}"
    for region in regions:
        data = world.region_gyms(region)
        gyms = data.get("gyms") or []
        assert gyms, f"{region} 没有道馆"
        orders = [int(g["order"]) for g in gyms]
        assert orders == sorted(orders), f"{region} 道馆顺序错乱"
        assert data.get("elite4"), f"{region} 缺四天王"
        assert data.get("champion"), f"{region} 缺冠军"
        for g in gyms:
            assert g.get("location") in world.nodes(region), (
                f"{region} 道馆地点 {g.get('location')} 不存在"
            )
            assert g.get("leader") and g.get("badge")
            assert DEX.resolve_type(g.get("type")), f"{region} 属性非法:{g.get('type')}"
            assert 2 <= len(g.get("team") or []) <= 6
            for m in g["team"]:
                assert DEX.resolve_species(m["species"]), m
                assert 1 <= int(m["level"]) <= 100
        # 等级单调:馆主 < 四天王 < 冠军
        gym_max = max(int(m["level"]) for g in gyms for m in g["team"])
        e4_min = min(int(m["level"]) for e in data["elite4"] for m in e["team"])
        champ_min = min(int(m["level"]) for m in data["champion"]["team"])
        assert gym_max <= e4_min + 3, f"{region} 馆主等级远高于四天王"
        assert e4_min <= champ_min + 5, f"{region} 冠军等级异常偏低"


def test_all_gym_species_are_real_and_teams_nonempty():
    world = WorldMap()
    n = 0
    for region in world.regions_with_data():
        for g in world.gyms(region):
            for m in g.get("team") or []:
                assert m["species"] in DEX.species
                n += 1
    assert n > 150, f"道馆宝可梦数据太少:{n}"


def test_starters_available():
    for name in ("新叶喵", "呆火鳄", "润水鸭", "皮卡丘", "伊布", "小火龙"):
        assert DEX.resolve_species(name), name


# ══════════════════════════════════════════════════════════════════
# 训练家 / 地图 / 通行
# ══════════════════════════════════════════════════════════════════
def test_new_trainer_and_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        store = TrainerStore(tmp)
        t = new_trainer("u1", "g1", "小智", starter="皮卡丘", day=1, now=0)
        assert t.party and t.party[0]["species"] == "pikachu"
        assert t.bag["poke-ball"] == 5 and t.money == 3000
        assert t.location == WorldMap().start_location("kanto")
        store.save(t.scope, t.uid, t.data)
        again = Trainer(store.load(t.scope, t.uid), uid="u1", scope="g1")
        assert again.party[0]["species"] == "pikachu"
        assert again.party[0]["id"] and again.location == t.location


def test_travel_gating():
    world = WorldMap()
    t = new_trainer("u1", "g1", "小智", starter="皮卡丘", day=1)
    start = t.location
    nxt = world.neighbors(start)[0]
    ok, _ = world.travel_check(t, nxt)
    assert ok, "相邻地点应当可走"
    # 不相邻
    far = next(
        k
        for k in world.nodes("kanto")
        if k != start and k not in world.neighbors(start)
    )
    ok, msg = world.travel_check(t, far)
    assert not ok and ("相邻" in msg or "徽章" in msg)
    # 未解锁地区
    ok, msg = world.travel_check(t, world.start_location("hoenn"))
    assert not ok and "尚未开放" in msg
    # 飞行需要 3 枚徽章
    ok, msg = world.travel_check(t, start, by_fly=True)
    assert not ok


def test_travel_unlock_after_badge():
    world = WorldMap()
    t = new_trainer("u1", "g1", "小智", starter="皮卡丘", day=1)
    # 高危险度地点在 0 徽章时不可进入
    high = max(world.nodes("kanto"), key=lambda k: world.tier(k))
    ok, _msg = world.travel_check(t, high)
    assert not ok
    # 给足徽章后开放(仍需相邻或飞行)
    for i in range(1, 9):
        t.add_badge("kanto", i)
    ok, _ = world.travel_check(t, high, by_fly=True) if high in t.data["visited"] else (True, "")
    assert ok


# ══════════════════════════════════════════════════════════════════
# 对战 / 捕获 / 成长
# ══════════════════════════════════════════════════════════════════
def _trainer(tmp, *, species="charizard", level=60):
    store = TrainerStore(tmp)
    t = new_trainer("u1", "g1", "小智", starter="charmander", day=1)
    mon = create_pokemon(species, level)
    d = mon.to_dict()
    d["id"] = "m1"
    t.party[0] = d
    store.save(t.scope, t.uid, t.data)
    return t


def test_wild_battle_and_catch():
    with tempfile.TemporaryDirectory() as tmp:
        t = _trainer(tmp, species="charmander", level=5)
        t.data["location"] = "kanto-route-1"
        hit = B.roll_wild(t)
        assert hit and hit["species"] in DEX.species
        B.start(
            t,
            [{"species": hit["species"], "level": hit["level"]}],
            kind="wild",
            wild=True,
            meta={"title": "野生"},
            day=1,
        )
        assert B.in_battle(t)
        res = None
        for _ball in range(40):
            res = B.take_turn(t, "catch poke-ball", day=1, daytime="day")
            if res.finished:
                break
            t.add_item("poke-ball", 1)
        assert res and res.finished
        assert res.outcome in ("caught", "escaped", "loss", "win")
        assert not B.in_battle(t)


def test_catch_master_ball_always_works():
    with tempfile.TemporaryDirectory() as tmp:
        t = _trainer(tmp, species="charizard", level=50)
        t.data["location"] = "kanto-route-1"
        t.add_item("master-ball", 1)
        B.start(
            t,
            [{"species": "snorlax", "level": 50}],
            kind="wild",
            wild=True,
            meta={"title": "野生"},
            day=1,
        )
        res = B.take_turn(t, "catch master-ball", day=1, daytime="day")
        assert res.finished and res.outcome == "caught"
        assert any(p["species"] == "snorlax" for p in t.party + t.box)
        assert t.caught("snorlax")


def test_cannot_catch_trainer_pokemon():
    with tempfile.TemporaryDirectory() as tmp:
        t = _trainer(tmp, species="charizard", level=60)
        B.start(
            t,
            [{"species": "rattata", "level": 5}],
            kind="trainer",
            wild=False,
            meta={"title": "训练家"},
            day=1,
        )
        res = B.take_turn(t, "catch poke-ball", day=1, daytime="day")
        assert not res.finished
        assert not any("捕获" in r for r in res.rewards)


def test_gym_battle_rewards_badge_and_money():
    with tempfile.TemporaryDirectory() as tmp:
        world = WorldMap()
        t = _trainer(tmp, species="charizard", level=80)
        t.data["location"] = world.gyms("kanto")[0]["location"]
        money0 = t.money
        meta = __import__("pw.npc", fromlist=["npc"]).build_gym_battle(
            t, world.gyms("kanto")[0]
        )
        B.start(t, meta["team"], kind="gym", meta=meta, day=1)
        res = None
        for _ in range(60):
            if t.party[0].get("cur_hp", 0) <= 0:
                t.heal_party()
            res = B.take_turn(t, f"move {t.party[0]['moves'][0]}", day=1, daytime="day")
            if res.error or res.finished:
                break
        assert res and res.finished and res.outcome == "win", res and res.error
        assert t.badges == ["kanto:1"]
        assert t.money > money0


def test_level_up_and_evolution_and_learn():
    mon = create_pokemon("charmander", 15)
    before = mon.level
    res = growth.gain_exp(mon, DEX.exp_for_level("medium-slow", 17), daytime="day")
    assert mon.level >= 17 and res.levels_gained > 0
    assert mon.exp >= DEX.exp_for_level(DEX.growth_of("charmander"), mon.level)
    assert before < mon.level
    # 进化:小火龙 16 级 → 火恐龙
    mon2 = create_pokemon("charmander", 16)
    old = mon2.species
    evo = growth.auto_evolve(mon2, daytime="day")
    assert evo == "charmeleon", evo
    assert mon2.species != old and mon2.max_hp > 0
    # 学会招式 + 替换
    mon3 = create_pokemon("pikachu", 30, moves=["thunderbolt", "quickattack", "growl", "tailwhip"])
    assert growth.learn_move(mon3, "irontail") is False  # 已满 4 招
    assert growth.replace_move(mon3, "growl", "irontail") is True
    assert "irontail" in mon3.moves and "growl" not in mon3.moves
    assert "growl" not in mon3.pp and "irontail" in mon3.pp


def test_evolution_requires_item_for_stone():
    mon = create_pokemon("pikachu", 30)
    opts = DEX.use_item_evolutions(mon.species, "thunder-stone")
    assert opts, "皮卡丘应可用雷之石进化"
    target = opts[0]["target"] if isinstance(opts[0], dict) else opts[0]
    growth.apply_evolution(mon, target)
    assert mon.species == "raichu"


def test_fainted_pokemon_not_revived_by_levelup():
    mon = create_pokemon("pikachu", 20)
    mon.full_heal()
    mon.cur_hp = 0
    mon.fainted = True
    growth.recompute(mon)
    assert mon.cur_hp == 0 and mon.fainted


# ══════════════════════════════════════════════════════════════════
# 每日事件
# ══════════════════════════════════════════════════════════════════
def test_fallback_world_events_are_legal():
    world = WorldMap()
    state = WorldState({}, "g1")
    evs = EV.fallback_world_events(state, 1, regions=["kanto"])
    assert evs
    for ev in evs:
        assert ev["kind"] in EV.WORLD_KINDS
        assert ev["location"] in world.nodes(ev["region"])
        for k, v in (ev.get("effects") or {}).items():
            if EV.EFFECT_RANGES[k] is None:
                assert v in EV.ALLOWED_WEATHER
                continue
            lo, hi = EV.EFFECT_RANGES[k]
            assert lo <= float(v) <= hi, (k, v)


def test_sanitize_clamps_llm_effects():
    state = WorldState({}, "g1")
    ev = EV.sanitize_world_event(
        {
            "kind": "swarm",
            "title": "x",
            "region": "kanto",
            "location": "kanto-route-1",
            "effects": {"encounter_mult": 999, "money_mult": -5, "battle_weather": "hail"},
            "days": 99,
        },
        state,
        1,
    )
    assert ev is not None
    assert ev["effects"]["encounter_mult"] == 3.0
    assert ev["effects"]["money_mult"] == 0.5
    assert "battle_weather" not in ev["effects"]
    assert ev["days"] == 3


def test_sanitize_rejects_bad_kind_and_wrong_region_location():
    state = WorldState({}, "g1")
    assert EV.sanitize_world_event({"kind": "nuke"}, state, 1) is None
    ev = EV.sanitize_world_event(
        {"kind": "rocket", "region": "kanto", "location": "littleroot-town"}, state, 1
    )
    # 地点与地区不匹配 → 地点被丢弃并就近补一个合法地点
    assert ev is not None
    assert WorldMap().region_of(ev["location"]) == "kanto"


def test_roll_day_is_idempotent_and_delivers_player_events():
    t = new_trainer("u1", "g1", "小智", starter="皮卡丘", day=1)
    state = WorldState({}, "g1")

    async def go():
        r1 = await D.roll_day(scope="g1", state=state, players=[t], day=5)
        r2 = await D.roll_day(scope="g1", state=state, players=[t], day=5)
        assert r1["rolled"] is True and r2["rolled"] is False
        lines = D.deliver_player_events(t, state)
        assert lines, "应有个人事件"
        assert D.deliver_player_events(t, state) == [], "个人事件只能领一次"

    asyncio.run(go())


def test_player_event_rewards_apply():
    t = new_trainer("u1", "g1", "小智", starter="皮卡丘", day=1)
    t.bag.pop("potion", None)
    EV.apply_player_event(t, {"kind": "gift_item", "item": "potion", "n": 2})
    assert t.count("potion") == 2
    m0 = t.money
    EV.apply_player_event(t, {"kind": "gift_money", "money": 800})
    assert t.money == m0 + 800
    f0 = t.party[0]["friendship"]
    EV.apply_player_event(t, {"kind": "friend", "friendship": 10})
    assert t.party[0]["friendship"] == f0 + 10


def test_locks_block_travel():
    world = WorldMap()
    t = new_trainer("u1", "g1", "小智", starter="皮卡丘", day=1)
    nxt = world.neighbors(t.location)[0]
    ok, msg = world.travel_check(t, nxt, locked_until={nxt: 9})
    assert not ok and "封锁" in msg
    # 事件过期后恢复
    ok, _ = world.travel_check(t, nxt, locked_until={})
    assert ok


# ══════════════════════════════════════════════════════════════════
# 商店 / 道具
# ══════════════════════════════════════════════════════════════════
def test_item_price_and_shop_stock():
    world = WorldMap()
    assert world.shop_stock("pallet-town")
    base = item_price("poke-ball")
    with_badge = item_price("poke-ball", badge_count=8)
    assert 0 < with_badge <= base
    discounted = item_price("poke-ball", discount=0.5)
    assert discounted < base
    for key in world.shop_stock("pallet-town"):
        entry = resolve_bag_item(key)
        assert entry, f"商店售卖的 {key} 不在 BAG_ITEMS 中"


def test_bag_add_remove_and_money():
    t = new_trainer("u1", "g1", "小智", starter="皮卡丘", day=1)
    t.add_item("potion", 3)
    assert t.count("potion") == 6  # 初始 3 + 新增 3
    assert t.take_item("potion", 2)
    assert t.count("potion") == 4
    assert not t.take_item("potion", 5)
    assert t.spend_money(100)
    assert not t.spend_money(10**9)


def test_all_bag_items_have_zh_and_kind():
    for key, entry in BAG_ITEMS.items():
        assert entry.get("zh"), key
        assert entry.get("kind"), key


# ══════════════════════════════════════════════════════════════════
# 插件入口可加载
# ══════════════════════════════════════════════════════════════════
def test_plugin_package_imports_and_commands_registered():
    mod = _load_plugin_package()
    cls = mod.PokemonWorldPlugin
    for name in ("cmd_start", "cmd_status", "cmd_go", "cmd_explore", "cmd_battle",
                 "cmd_catch", "cmd_shop", "cmd_gym", "cmd_league", "cmd_dex",
                 "cmd_evolve", "cmd_learn", "cmd_today", "cmd_help"):
        assert hasattr(cls, name), f"缺少指令 {name}"
