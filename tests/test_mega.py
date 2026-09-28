"""Mega 进化回归:形态/石头数据、战斗内变身、写回还原、商店与指令。

覆盖用户反馈的入口:「赫拉克罗斯只能 Mega 进化」—— 项目里必须真的能变。
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
from pw import mega as M  # noqa: E402
from pw.engine import battle_from_dict, create_pokemon, start_battle  # noqa: E402
from pw.items import BAG_ITEMS, ITEMS, MEGA_STONE_CONFLICTS  # noqa: E402
from pw.player import Trainer, mon_to_dict  # noqa: E402
from pw.world import WorldMap  # noqa: E402


def _mon(species="heracross", item="heracronite", level=30,
         moves=("megahorn", "tackle")):
    return create_pokemon(species, level, moves=list(moves), item=item)


def _trainer(*, bag=None, item="heracronite", species="heracross", moves=None, level=30):
    kw = {"moves": moves} if moves is not None else {}
    mon = _mon(species, item, level=level, moves=kw.get("moves", ("megahorn", "tackle")))
    data = {
        "party": [mon_to_dict(mon)],
        "bag": dict(bag or {}),
        "uid": "u1",
        "scope": "s1",
    }
    return Trainer(data, uid="u1", scope="s1")


# ══════════════════════════════════════════════════════════════════
# 数据:每只 Mega 都真的能变
# ══════════════════════════════════════════════════════════════════
def test_every_mega_form_is_reachable_by_its_stone():
    """全部 Mega 形态都要有对应石头;每块石头都能把对应原种变成它。"""
    from pw.dex import get_dex

    dex = get_dex()
    stones = M.all_stones()
    assert len(stones) == 92, f"Mega 石数量不对:{len(stones)}"
    assert M.mega_report() == [], M.mega_report()
    for stone in stones:
        row = M.stone_entry(stone)
        for mk in row["forms"]:
            assert mk in dex.species, mk
            # 石头必须真的能触发对应形态(共用石按原形态挑,名字不同的分支除外)
            base = dex.species[mk].get("baseSpecies")
            got = M.target_for(base, stone)
            assert got, f"{base} 拿着 {stone} 变不了身"
    # 赫拉克罗斯:只能 Mega 进化,绝对不能有普通进化
    assert dex.evolution_options("heracross") == []
    assert M.target_for("heracross", "heracronite") == "heracrossmega"


def test_mega_stones_are_items_and_key_stone_is_not_held():
    assert MEGA_STONE_CONFLICTS == []
    assert BAG_ITEMS["heracronite"]["zh"] == "赫拉克罗斯进化石"
    assert BAG_ITEMS["heracronite"]["kind"] == "mega"
    assert BAG_ITEMS["heracronite"]["effect"]["mega"] == ["heracrossmega"]
    assert BAG_ITEMS["charizardite-x"]["zh"] == "喷火龙进化石X"
    assert "heracronite" in ITEMS, "Mega 石必须能 /持有"
    # 钥石是重要物品:能买到、进背包,但不能给宝可梦携带
    assert BAG_ITEMS["key-stone"]["kind"] == "key"
    assert "key-stone" not in ITEMS


def test_target_for_x_y_and_shared_stones():
    assert M.target_for("charizard", "charizardite-x") == "charizardmegax"
    assert M.target_for("charizard", "charizardite-y") == "charizardmegay"
    assert M.target_for("mewtwo", "mewtwonite-x") == "mewtwomegax"
    # 共享石:同一块石头按原形态挑正确分支
    assert M.target_for("tatsugiri", "tatsugirinite") == "tatsugiricurlymega"
    assert M.target_for("tatsugiridroopy", "tatsugirinite") == "tatsugiridroopymega"
    assert M.target_for("tatsugiristretchy", "tatsugirinite") == "tatsugiristretchymega"
    assert M.target_for("meowstic", "meowsticite") == "meowsticmmega"
    assert M.target_for("meowsticf", "meowsticite") == "meowsticfmega"
    assert M.target_for("magearna", "magearnite") == "magearnamega"
    assert M.target_for("magearnaoriginal", "magearnite") == "magearnaoriginalmega"
    # 超级烈空坐:不需要石头,会画龙点睛即可
    assert M.target_for("rayquaza", "", ["dragonascent"]) == "rayquazamega"
    assert M.target_for("rayquaza", "", ["tackle"]) == ""
    # 石头与宝可梦不匹配 → 变不了
    assert M.target_for("heracross", "pinsirite") == ""


# ══════════════════════════════════════════════════════════════════
# 引擎:变身 / 数值 / 还原 / 序列化
# ══════════════════════════════════════════════════════════════════
def test_mega_transform_changes_stats_types_ability_and_reverts():
    mon = _mon("heracross", "heracronite", level=50)
    base_atk = mon.stats["atk"]
    assert mon.ability != "skill-link"
    assert mon.mega_evolve_to("heracrossmega")
    assert mon.species == "heracrossmega"
    assert mon.mega_from == "heracross"
    assert mon.ability == "skill-link"
    assert mon.stats["atk"] > base_atk
    assert mon.display.startswith("赫拉克罗斯")
    # 写回存档:还原成原种(战斗外不该是 Mega)
    stored = mon.to_storage_dict()
    assert stored["species"] == "heracross"
    assert stored["mega_from"] == ""
    assert stored["stats"]["atk"] == base_atk
    # 还原后还能再变(下一场)
    mon.revert_mega()
    assert mon.species == "heracross" and mon.mega_from == ""
    assert mon.stats["atk"] == base_atk


def test_mega_hp_scales_and_survives_serialization():
    mon = _mon("heracross", "heracronite", level=50)
    mon.cur_hp = mon.max_hp // 2
    battle = start_battle([mon], [_mon("snorlax", "", level=50, moves=["tackle"])],
                          bag={"key-stone": 1})
    battle.start()
    frac = mon.cur_hp / mon.max_hp
    assert battle.mega_evolve(battle.player)
    assert abs(mon.cur_hp / mon.max_hp - frac) < 0.05
    again = battle_from_dict(battle.to_dict())
    assert again.player.mon.species == "heracrossmega"
    assert again.player.mon.mega_from == "heracross"
    assert again.player.mega_used is True


def test_step_applies_mega_before_move_and_only_once_per_side():
    a = _mon("heracross", "heracronite", level=50)
    b = _mon("snorlax", "", level=50, moves=["tackle"])
    battle = start_battle([a], [b], bag={"key-stone": 1})
    battle.start()
    lines = battle.step({"type": "move", "move": "megahorn", "mega": True})
    assert any("Mega 进化" in line for line in lines)
    assert a.species == "heracrossmega"
    assert battle.player.mega_used
    # 第二次不再变身(每侧每场一次)
    assert not battle.mega_evolve(battle.player)


def test_ai_enemy_mega_evolves_when_holding_stone():
    a = _mon("snorlax", "", level=50, moves=["tackle"])
    b = _mon("gyarados", "gyaradosite", level=50, moves=["waterfall", "tackle"])
    battle = start_battle([a], [b], bag={})
    battle.start()
    lines = battle.step({"type": "move", "move": "tackle"})
    assert any("Mega 进化" in line for line in lines)
    assert b.species == "gyaradosmega"


# ══════════════════════════════════════════════════════════════════
# 行动解析 / 回合入口
# ══════════════════════════════════════════════════════════════════
def test_parse_action_mega_variants_and_errors():
    t = _trainer(bag={"key-stone": 1})
    B.start(t, [{"species": "snorlax", "level": 30}])
    battle = B.battle_from_dict(B.session(t)["battle"])
    act = B.parse_action("mega 1", t, battle)
    assert act == {"type": "move", "move": "megahorn", "tera": False, "mega": True}
    assert B.parse_action("mega", t, battle) == {"type": "mega"}
    assert B.parse_action("超级进化 1", t, battle)["mega"] is True
    assert B.parse_action("mega进化 1", t, battle)["mega"] is True
    # megahorn(超级角击)不能被当成 mega 前缀
    assert B.parse_action("megahorn", t, battle)["mega"] is False
    # 「mega punch」这种招式名:整句能解析成招式时按招式处理
    t2 = _trainer(bag={"key-stone": 1}, moves=("megapunch", "tackle"))
    B.start(t2, [{"species": "snorlax", "level": 30}])
    b2 = B.battle_from_dict(B.session(t2)["battle"])
    plain = B.parse_action("mega punch", t2, b2)
    assert plain["mega"] is False and plain["move"] == "megapunch"
    combo = B.parse_action("mega megapunch", t2, b2)
    assert combo["mega"] is True and combo["move"] == "megapunch"
    # 没有钥石 / 石头不匹配 / 已经 Mega 过
    no_key = _trainer(bag={})
    B.start(no_key, [{"species": "snorlax", "level": 30}])
    nb = B.battle_from_dict(B.session(no_key)["battle"])
    try:
        B.parse_action("mega 1", no_key, nb)
        raise AssertionError("没有钥石应当报错")
    except B.BattleError as e:
        assert "钥石" in str(e)
    wrong = _trainer(bag={"key-stone": 1}, item="pinsirite")
    B.start(wrong, [{"species": "snorlax", "level": 30}])
    wb = B.battle_from_dict(B.session(wrong)["battle"])
    try:
        B.parse_action("mega 1", wrong, wb)
        raise AssertionError("石头不匹配应当报错")
    except B.BattleError as e:
        assert "凯罗斯进化石" in str(e)


def test_take_turn_standalone_mega_does_not_consume_turn():
    t = _trainer(bag={"key-stone": 1})
    B.start(t, [{"species": "snorlax", "level": 30}])
    res = B.take_turn(t, "mega")
    assert not res.error and not res.finished
    assert any("Mega 进化" in line for line in res.lines)
    sess = B.session(t)["battle"]
    assert sess["turn"] == 0, "Mega 进化不该消耗回合"
    assert sess["player"]["party"][0]["species"] == "heracrossmega"
    # 存档里已经还原(战斗结束/重启都不会把 Mega 形态存下来)
    stored = B.dict_to_mon(t.party[0])
    assert stored.species == "heracross"
    assert "heracrossmega" in t.data["dex_seen"]
    # 之后照常出招
    res2 = B.take_turn(t, "1")
    assert not res2.error


def test_take_turn_combo_mega_move():
    t = _trainer(bag={"key-stone": 1})
    B.start(t, [{"species": "snorlax", "level": 30}])
    res = B.take_turn(t, "mega 1")
    assert not res.error and not res.finished
    assert any("Mega 进化" in line for line in res.lines)
    assert any("赫拉克罗斯" in line for line in res.lines)
    assert B.session(t)["battle"]["turn"] == 1


# ══════════════════════════════════════════════════════════════════
# PvP:双方都能 Mega(独立变身不占行动 / 组合出招)
# ══════════════════════════════════════════════════════════════════
def test_pvp_both_sides_can_mega_in_one_turn():
    a = _mon("heracross", "heracronite", level=50)
    b = _mon("pinsir", "pinsirite", level=50, moves=["xscissor", "tackle"])
    battle = start_battle([a], [b], bag={}, wild=False)
    battle.start()
    lines = battle.step(
        {"type": "move", "move": "tackle", "mega": True},
        {"type": "move", "move": "tackle", "mega": True},
    )
    assert a.species == "heracrossmega"
    assert b.species == "pinsirmega"
    assert sum(1 for line in lines if "Mega 进化" in line) == 2


def test_pvp_parse_for_validates_each_side():
    from pw import pvp

    a = _mon("heracross", "heracronite", level=50)
    b = _mon("pinsir", "pinsirite", level=50, moves=["xscissor", "tackle"])
    row = {
        "stage": "battle",
        "from": "u1", "from_name": "甲",
        "to": "u2", "to_name": "乙",
    }
    battle = start_battle([a], [b], bag={}, wild=False)
    battle.start()
    row["battle"] = battle.to_dict()
    t1 = _trainer(bag={"key-stone": 1})
    t2 = Trainer({"party": [mon_to_dict(_mon("pinsir", "pinsirite", level=50,
                                             moves=["xscissor", "tackle"]))],
                  "bag": {"key-stone": 1}, "uid": "u2", "scope": "s1"},
                 uid="u2", scope="s1")
    assert pvp.parse_for(row, "u1", t1, "mega 1")["mega"] is True
    assert pvp.parse_for(row, "u2", t2, "mega 1")["mega"] is True
    assert pvp.parse_for(row, "u1", t1, "mega")["type"] == "mega"


# ══════════════════════════════════════════════════════════════════
# 道馆主配 Mega / 首胜掉落
# ══════════════════════════════════════════════════════════════════
def test_gym_leader_aces_carry_mega_stones_only_after_lv30():
    """馆长招牌 Lv30+ 才配 Mega 石(太早的馆主玩家还没钥石,不能被打爆)。"""
    import json
    import pathlib

    from pw import npc
    from pw.world import WorldMap

    data = json.loads(
        pathlib.Path(_ROOT, "pw/static/gyms.json").read_text(encoding="utf-8")
    )
    with_mega = {
        (region, gm["leader"]): gm["team"][-1].get("item")
        for region, info in data["regions"].items()
        for gm in info.get("gyms") or []
        if (gm["team"][-1] or {}).get("item")
    }
    assert with_mega[("sinnoh", "阿李")] == "lucarionite"
    assert with_mega[("kalos", "可尔妮")] == "hawluchanite"
    assert with_mega[("kanto", "娜姿")] == "alakazite"
    for region, early in (("kanto", "小霞"), ("kanto", "马志士"),
                          ("johto", "松叶"), ("hoenn", "铁旋")):
        assert (region, early) not in with_mega, f"{early} 太早,不该配 Mega"
    assert all(M.stone_entry(item) for item in with_mega.values())
    # 馆主配的石头必须真的传进战斗(meta team 带 item)
    gym = next(g for g in WorldMap().gyms("sinnoh") if g["leader"] == "阿李")
    meta = npc.build_gym_battle(_trainer(), gym)
    assert meta["team"][-1]["item"] == "lucarionite"


def test_first_gym_win_drops_leader_mega_stone():
    """首次打赢带 Mega 石的馆主,那块石头掉进背包;梅开二度不再掉。"""
    from pw import npc
    from pw.world import WorldMap

    t = _trainer(bag={"key-stone": 1}, item="", level=80, moves=("tackle",))
    t.data["region"] = "sinnoh"
    gym = next(g for g in WorldMap().gyms("sinnoh") if g["leader"] == "阿李")
    meta = npc.build_gym_battle(t, gym)
    B.start(t, meta["team"], kind="gym", meta=meta, wild=False)
    res = B.take_turn(t, "1")
    for _ in range(30):
        if res.finished:
            break
        res = B.take_turn(t, "1")
    assert res.finished and res.outcome == "win", (res.outcome, res.lines)
    assert any("路卡利欧进化石" in line for line in res.rewards), res.rewards
    assert t.count("lucarionite") == 1
    assert "sinnoh:3" in t.data["badges"]


def test_first_elite_win_drops_mega_stone():
    from pw import npc
    from pw.world import WorldMap

    members = WorldMap().elite4("sinnoh")
    member = next(m for m in members if m["team"][-1].get("item"))
    # 坦一点、技能 PP 够多:Lv100 卡比兽硬抗四天王整队
    t = _trainer(bag={"key-stone": 1}, item="", species="snorlax", level=100,
                 moves=("bodyslam", "tackle"))
    t.data["region"] = "sinnoh"
    meta = npc.build_elite4_battle(t, member)
    stone = meta["team"][-1]["item"]
    assert M.stone_entry(stone)
    B.start(t, meta["team"], kind="elite", meta=meta, wild=False)
    res = B.take_turn(t, "1")
    for _ in range(40):
        if res.finished:
            break
        res = B.take_turn(t, "1")
    assert res.finished and res.outcome == "win", (res.outcome, res.lines)
    assert t.count(stone) == 1
    zh = M.stone_zh(stone)
    assert any(zh in line for line in res.rewards), res.rewards


# ══════════════════════════════════════════════════════════════════
# 商店 / 指令
# ══════════════════════════════════════════════════════════════════
def test_shop_key_stone_always_and_mega_stones_after_key_and_catch():
    world = WorldMap()
    stock8 = set(world.shop_stock("pewter-city", 8))
    assert "key-stone" in stock8
    assert "heracronite" in stock8                 # 无 trainer = 全量(数据自检用)

    # 没钥石:一块都不展示
    t = _trainer(bag={}, species="snorlax", item="")
    assert "heracronite" not in world.shop_stock("pewter-city", 8, trainer=t)
    # 有钥石但没这只:也不展示
    t.add_item("key-stone", 1)
    assert "heracronite" not in world.shop_stock("pewter-city", 8, trainer=t)
    t.data["dex_caught"] = ["heracross"]
    stock = world.shop_stock("pewter-city", 8, trainer=t)
    assert "heracronite" in stock and "pinsirite" not in stock
    # 队伍里持有(即便图鉴没记)也算拥有
    own = _trainer(bag={"key-stone": 1})
    assert "heracronite" in world.shop_stock("pewter-city", 8, trainer=own)


def test_explore_mega_find_useful_and_no_duplicates():
    """探索稀有拾得:只掉已捕获且还没拥有的 Mega 石,拿到后不再重复掉。"""
    import random

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "quest_enable": False}
        run_cmd(p, _Event("/开始 小智 新叶喵"), p.cmd_start)
        t = p._load(_Event())
        t.data["dex_caught"] = ["heracross"]
        got = [
            msg
            for msg in (p._maybe_mega_find(t, random.Random(seed)) for seed in range(500))
            if msg
        ]
        assert got, "500 次都没触发 Mega 石拾得"
        assert "赫拉克罗斯进化石" in got[0]
        assert "钥石" in got[0]              # 还没钥石时会提示
        assert t.count("heracronite") == 1
        again = [
            msg
            for msg in (p._maybe_mega_find(t, random.Random(seed)) for seed in range(500))
            if msg and "赫拉克罗斯进化石" in msg
        ]
        assert not again, "已经拥有的石头不该重复掉"


def test_mega_command_and_use_hint():
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "quest_enable": False}
        run_cmd(p, _Event("/开始 小智 新叶喵"), p.cmd_start)
        t = p._load(_Event())
        t.party[0] = mon_to_dict(_mon("heracross", "heracronite"))
        t.add_item("key-stone", 1)
        p._save(t)

        # /使用 Mega 石 → 引导到 /持有 + /mega,而不是当成消耗品
        t = p._load(_Event())
        t.add_item("heracronite", 1)
        p._save(t)
        ev = _Event("/使用 赫拉克罗斯进化石")
        run_cmd(p, ev, p.cmd_use)
        assert "携带道具" in "".join(ev.outputs)

        # /mega:对战中变身、不消耗回合
        B.start(t, [{"species": "snorlax", "level": 30}])
        p._save(t)
        ev = _Event("/mega")
        run_cmd(p, ev, p.cmd_mega)
        text = "".join(ev.outputs)
        assert "Mega 进化" in text, text

        # /商店 能买到(有钥石 + 捕获过 + 徽章够了)
        t = p._load(_Event())
        t.data.setdefault("dex_caught", []).append("heracross")
        t.data["badges"] = ["kanto:1", "kanto:2", "kanto:3"]
        p._save(t)
        assert "赫拉克罗斯进化石" in p._shop_text(t, 1.0)
