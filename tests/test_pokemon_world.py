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
from pw.world import (  # noqa: E402
    CORE_METHODS,
    REGION_ORDER,
    WorldMap,
    is_wild_method,
    item_price,
)
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


# ══════════════════════════════════════════════════════════════════
# 野外池清洗(上游数据带伪条目 / 异常等级 / 串区中文名)
# ══════════════════════════════════════════════════════════════════
LEGENDARIES = {
    "articuno", "zapdos", "moltres", "mewtwo", "mew", "raikou", "entei", "suicune",
    "lugia", "ho-oh", "celebi", "regirock", "regice", "registeel", "latias", "latios",
    "kyogre", "groudon", "rayquaza", "jirachi", "deoxys", "uxie", "mesprit", "azelf",
    "dialga", "palkia", "heatran", "giratina", "cresselia", "reshiram", "zekrom",
    "kyurem", "xerneas", "yveltal", "zygarde", "solgaleo", "lunala", "necrozma",
    "zacian", "zamazenta", "eternatus",
}


def test_wild_pools_exclude_non_wild_methods_and_legendaries():
    """普通野池里不能出现 礼物/定点/团本/游走/`-special` 以及传说宝可梦。"""
    world = WorldMap()
    for key in world._index:
        for p in world.wild_pools(key):
            assert is_wild_method(p["method"]), (key, p)
            assert not p["method"].endswith("-special"), (key, p)
            assert p["species"] not in LEGENDARIES, f"野池混入传说:{key} {p}"
            assert 1 <= p["min"] <= p["max"] <= 100, (key, p)


def test_side_pools_clamped_to_core_level_band():
    """空中/垂钓等侧池不得脱离该地点核心等级区间。

    PokeAPI 的 Let's Go 空中遭遇是 min=3/max=56,会把 Lv56 大比鸟塞进 1 号道路。
    """
    world = WorldMap()
    checked = 0
    for key in world._index:
        pools = world.wild_pools(key)
        core = [p for p in pools if p["method"] in CORE_METHODS]
        if not core:
            continue
        lo = min(p["min"] for p in core)
        hi = max(p["max"] for p in core)
        for p in pools:
            if p["method"] in CORE_METHODS:
                continue
            # 代码保证:min 不低于核心下限;max 不高于核心上限(若侧池本身
            # 下限就高于核心上限,则收敛为单点 min==max,不再向上扩张)
            assert p["min"] >= lo, (key, p, lo, hi)
            assert p["max"] <= max(hi, p["min"]), (key, p, lo, hi)
            checked += 1
    assert checked > 50, f"侧池样本太少:{checked}"


def test_wild_roll_levels_match_location_band():
    """实际遭遇等级必须落在该地点真实区间(1 号道路不能出 Lv50 大比鸟)。"""
    world = WorldMap()
    t = new_trainer("u1", "g1", "小智", starter="皮卡丘", day=1)
    for key in ("kanto-route-1", "digletts-cave", "kanto-route-17"):
        pools = world.wild_pools(key)
        lo = min(p["min"] for p in pools)
        hi = max(p["max"] for p in pools)
        t.data["location"] = key
        for i in range(60):
            t.data["steps"] = i * 11
            hit = B.roll_wild(t)
            assert hit, key
            assert lo <= hit["level"] <= hi, (key, hit, lo, hi)


def test_pseudo_locations_removed_from_map():
    """roaming-* / unknown-* 不是地点,不该出现在可前往列表里。"""
    world = WorldMap()
    bad = [k for k in world._index if k.startswith(("roaming-", "unknown-"))]
    assert not bad, bad
    for region in world.regions_with_data():
        for key in world.nodes(region):
            assert not key.startswith(("roaming-", "unknown-"))
            for nxt in world.neighbors(key):
                assert not nxt.startswith(("roaming-", "unknown-"))


def test_node_names_cleaned_and_routes_localized():
    """节点名:道路按标识生成,去掉串区后缀,不留繁体。"""
    world = WorldMap()
    traditional = "號島碼頭園羅藍灣爾奧樂歐納樹馬礦關圓環離點緣衆會國學車東門長陽雲電龍劍銀鋼鐵紅綠黃陸橋廳場隊華萬縣鎮區鄉燈爐館營徑嶺淵溝灘澗廣廢"
    for key in world._index:
        zh = world.node_zh(key)
        assert zh, key
        assert not any(c in traditional for c in zh), (key, zh)
        assert "（" not in zh and "(" not in zh, (key, zh)
        m = __import__("re").search(r"(?:^|-)(sea-)?route-(\d+)$", key)
        if m:
            want = f"{int(m.group(2))}号{'水路' if m.group(1) else '道路'}"
            assert zh == want, (key, zh, want)


def test_legendary_event_uses_curated_pool():
    from pw import npc

    world = WorldMap()
    t = new_trainer("u1", "g1", "小智", starter="皮卡丘", day=1)
    t.data["location"] = "kanto-route-1"
    ev = {"kind": "rare", "id": "e1", "location": "kanto-route-1", "region": "kanto", "created_day": 3}
    hit = npc.legendary_at(t, ev)
    assert hit and hit["species"] in npc.LEGENDARY_POOLS["kanto"], hit
    assert hit["level"] >= 40 and hit["_rare"] is True
    # 事件指定了合法物种则用它
    hit2 = npc.legendary_at(t, {**ev, "species": "snorlax"})
    assert hit2["species"] == "snorlax"
    # 指定的非法物种 → 回退到地区池
    hit3 = npc.legendary_at(t, {**ev, "species": "not-a-pokemon"})
    assert hit3["species"] in npc.LEGENDARY_POOLS["kanto"]
    _ = world


# ══════════════════════════════════════════════════════════════════
# 主线剧情 / 神兽 / 世界大赛
# ══════════════════════════════════════════════════════════════════
def test_story_stages_are_valid():
    from pw import story

    world = WorldMap()
    for region in world.regions_with_data():
        info = story.STORY.get(region)
        assert info, f"{region} 没有主线"
        assert info.get("org") and info.get("leader"), region
        stages = story.region_stages(region)
        assert len(stages) >= 5, (region, len(stages))
        assert [s["kind"] for s in stages].count("boss") >= 2, region
        for st in stages:
            for field in ("key", "kind", "title", "desc"):
                assert st.get(field), (region, st)
            loc = st.get("location")
            if st["kind"] in ("boss", "epilogue"):
                assert loc, f"{region}/{st['key']} 缺少地点"
            if loc:
                assert loc in world.nodes(region), (region, st["key"], loc)
            for sp, lv in st.get("team") or []:
                assert DEX.resolve_species(sp), (region, st["key"], sp)
                assert 1 <= int(lv) <= 100


def test_story_progress_follows_badges():
    from pw import story

    t = new_trainer("u1", "g1", "小智", starter="皮卡丘", day=1)
    newly = story.progress(t)
    assert any(s["key"] == "出发" for s in newly)
    assert story.current_stage(t)["key"] == "首枚徽章"
    t.add_badge("kanto", 1)
    newly = story.progress(t)
    assert any(s["key"] == "首枚徽章" for s in newly)
    # 徽章足够后当前章节应是第一个 boss
    t.add_badge("kanto", 2)
    story.progress(t)
    cur = story.current_stage(t)
    assert cur and cur["kind"] == "boss", cur
    meta = story.boss_meta(t, cur)
    assert meta["team"] and meta["kind"] == "rocket"
    assert meta["location"] in WorldMap().nodes("kanto")
    assert story.mark_stage(t, "kanto", cur["key"]) is True
    assert story.mark_stage(t, "kanto", cur["key"]) is False


def test_story_league_and_epilogue_after_champion():
    from pw import story

    t = new_trainer("u1", "g1", "小智", starter="皮卡丘", day=1)
    for i in range(1, 9):
        t.add_badge("kanto", i)
    t.set_flag("champion:kanto", True)
    story.progress(t)
    done = set(t.flag("story:kanto", []) or [])
    assert {"出发", "首枚徽章", "关都联盟"} <= done, done
    # 冠军后当前章节应为收尾(神兽)
    remaining = [s for s in story.region_stages("kanto") if s["key"] not in done]
    assert remaining and remaining[0]["kind"] in ("boss", "epilogue")


def test_legendary_sites_are_valid():
    from pw import legendary

    world = WorldMap()
    total = 0
    for region in world.regions_with_data():
        sites = legendary.sites_for(region)
        assert len(sites) >= 3, f"{region} 神兽太少:{len(sites)}"
        for s in sites:
            assert s["species"] in DEX.species
            assert s["location"] in world.nodes(region)
            assert 1 <= int(s["level"]) <= 100
            assert s["zh"]
            total += 1
    assert total >= 35, total


def test_legendary_requires_location_badges_and_champion():
    from pw import legendary

    world = WorldMap()
    t = new_trainer("u1", "g1", "小智", starter="皮卡丘", day=1)
    sites = legendary.sites_for("kanto")
    mewtwo = next(s for s in sites if s["species"] == "mewtwo")
    # 不在地点 → 不 ready
    assert not legendary.ready(t, day=1)
    t.data["location"] = mewtwo["location"]
    # 未成为冠军 → 不 ready
    assert not legendary.ready(t, day=1)
    t.set_flag("champion:kanto", True)
    # 冠军 + 地点正确 → ready,但仍需未捕获
    assert any(s["species"] == "mewtwo" for s in legendary.ready(t, day=1))
    legendary.mark_caught(t, "mewtwo")
    assert not any(s["species"] == "mewtwo" for s in legendary.ready(t, day=1))
    # 徽章门槛
    articuno = next(s for s in sites if s["species"] == "articuno")
    t.data["location"] = articuno["location"]
    assert not any(s["species"] == "articuno" for s in legendary.ready(t, day=1))
    for i in range(1, int(articuno["need"]) + 1):
        t.add_badge("kanto", i)
    assert any(s["species"] == "articuno" for s in legendary.ready(t, day=1))
    # 逃走后当天不能再战
    legendary.mark_fled(t, "articuno", 5)
    assert not any(s["species"] == "articuno" for s in legendary.ready(t, day=5))
    assert any(s["species"] == "articuno" for s in legendary.ready(t, day=6))
    _ = world


def test_legendary_team_and_panel():
    from pw import legendary

    t = new_trainer("u1", "g1", "小智", starter="皮卡丘", day=1)
    site = next(s for s in legendary.sites_for("kanto") if s["species"] == "articuno")
    t.data["location"] = site["location"]
    meta = legendary.legendary_meta(t, site)
    assert meta["kind"] == "legend" and meta["wild"] is True
    assert meta["team"] == [{"species": "articuno", "level": site["level"]}]
    assert "传说" in legendary.panel_text(t, day=1)
    # 打一场:确认能正常开战且捕获率极低(不会秒抓)
    B.start(t, meta["team"], kind="legend", wild=True, meta=meta, day=1)
    res = B.take_turn(t, "catch poke-ball", day=1, daytime="day")
    assert not res.error
    assert not (res.finished and res.outcome == "caught")


def test_tournament_requires_champion_and_builds_team():
    from pw import story

    world = WorldMap()
    t = new_trainer("u1", "g1", "小智", starter="皮卡丘", day=1)
    assert story.tournament_unlocked(t, world=world) is False
    t.set_flag("champion:kanto", True)
    assert story.tournament_unlocked(t, world=world) is True
    meta = story.tournament_meta(t, 0, world=world, rng=__import__("random").Random(1))
    assert meta["kind"] == "tournament"
    assert 1 <= len(meta["team"]) <= 6
    for m in meta["team"]:
        assert m["species"] in DEX.species
        assert 1 <= m["level"] <= 100
    # 三轮都会给出合法队伍
    for rnd in range(3):
        m = story.tournament_meta(t, rnd, world=world, rng=__import__("random").Random(rnd))
        assert m["team"]


def test_no_arbitrage_between_buy_and_sell():
    """卖价绝不能高于买价 —— 否则"买了立刻卖"就是无限刷钱。

    历史 bug:买价会吃徽章折扣 + 世界事件折扣并四舍五入到 10 的整数倍,
    而卖价用的是不带折扣的价格 // 2。3 徽章 + 事件打五折时精灵球买价 90₽、
    卖价 95₽,白赚 5₽。
    """
    from pw.items import BAG_ITEMS
    from pw.world import item_price

    worst = None
    for key in BAG_ITEMS:
        for badges in range(17):
            for disc in (1.0, 0.95, 0.9, 0.8, 0.7, 0.65, 0.6, 0.55, 0.5):
                buy = item_price(key, badge_count=badges, discount=disc)
                sell = max(1, item_price(key, badge_count=badges, discount=disc) // 2)
                assert sell <= buy, f"{key} 徽章{badges} 折扣{disc}:买 {buy} 卖 {sell} 可套利"
                if worst is None or sell - buy > worst[0]:
                    worst = (sell - buy, key)
    assert worst[0] <= 0


def test_world_modifiers_are_clamped_after_stacking():
    """多场事件叠加后的总量必须仍在声明区间内(否则 money_mult 能到 19683 倍)。"""
    from pw.events import EFFECT_RANGES
    from pw.worldstate import WorldState

    st = WorldState({"day": 100, "events": []}, "g1")
    for i in range(9):
        st.add_event({
            "id": f"e{i}", "kind": "festival", "title": "测试", "until_day": 105,
            "effects": {"money_mult": 3.0, "encounter_mult": 3.0, "rare_mult": 5.0,
                        "shop_discount": 0.5},
        })
    m = st.modifiers
    for key in ("money_mult", "encounter_mult", "rare_mult", "shop_discount"):
        lo, hi = EFFECT_RANGES[key]
        assert lo <= m[key] <= hi, f"{key}={m[key]} 超出声明区间 {EFFECT_RANGES[key]}"

    # 单场事件仍然按原值生效(钳制不能把正常效果压平)
    st2 = WorldState({"day": 100, "events": []}, "g1")
    st2.add_event({"id": "a", "title": "庆典", "until_day": 105,
                   "effects": {"money_mult": 1.3}})
    assert abs(st2.modifiers["money_mult"] - 1.3) < 1e-9

    # 折扣取 min(叠加只会更便宜,不会互相相乘)
    st3 = WorldState({"day": 100, "events": []}, "g1")
    st3.add_event({"id": "a", "title": "清仓", "until_day": 105,
                   "effects": {"shop_discount": 0.8}})
    st3.add_event({"id": "b", "title": "大甩卖", "until_day": 105,
                   "effects": {"shop_discount": 0.6}})
    assert abs(st3.modifiers["shop_discount"] - 0.6) < 1e-9
