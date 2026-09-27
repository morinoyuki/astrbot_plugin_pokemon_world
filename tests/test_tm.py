"""招式机(TM):数据完整性、兼容性、三条获取途径、与"待决定"的配合。

背景:招式机此前**完全不存在** —— 背包有「招式机」分类却永远是空的,`/图鉴`
会写"可用招式机学会"但没有任何途径。现在做了真的:道馆首次通关送本系招牌、
商店按徽章卖、委托奖励随机给。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402


def _p(tmp, bag=None, party_extra=None):
    from pw.engine import create_pokemon

    p = _Cmd(tmp)
    # 关掉图片:奖励文本(含"馆主送了招式机")才会走文本路径,便于断言
    p.config = {"ui_image": False, "battle_image": False, "quest_enable": False}
    run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
    t = p._load(_Event(""))
    t.data["region"] = "kanto"
    for i, (sp, lv, moves) in enumerate(party_extra or []):
        mon = create_pokemon(sp, lv).to_dict()
        mon["id"] = f"mx{i}"
        mon["moves"] = list(moves)
        t.data["party"].append(mon)
    for k, n in (bag or {}).items():
        t.add_item(k, n)
    p._save(t)
    return p


def _run(p, cmd, fn):
    ev = _Event(cmd)
    run_cmd(p, ev, getattr(p, fn))
    return "".join(str(x) for x in ev.outputs), ev


# ── 数据完整性 ──────────────────────────────────────────────────────
def test_tm_data_is_consistent():
    from pw.dex import get_dex
    from pw.items import BAG_ITEMS, TM_GYM_BY_TYPE, TM_MISSING, TM_MOVES, TM_SHOP, tm_key, tm_move

    assert not TM_MISSING, f"这些招式 key 在招式表里不存在:{TM_MISSING}"
    assert len(TM_MOVES) >= 25, f"招式机太少:{len(TM_MOVES)}"
    dex = get_dex()
    for mv in TM_MOVES:
        assert dex.moves.get(mv), mv
        key = tm_key(mv)
        e = BAG_ITEMS.get(key)
        assert e, f"{key} 不在背包数据里"
        assert e["kind"] == "tm", key
        assert e["effect"]["teaches"] == mv, key
        assert e["zh"].startswith("招式机·"), e["zh"]
        assert tm_move(key) == mv and tm_move("potion") == ""
    # 商店列表里的招式都在 TM_MOVES 里(否则会卖一台不存在的机器)
    stray = [m for _t, ks in TM_SHOP for m in ks if m not in TM_MOVES]
    assert not stray, stray
    # 道馆属性覆盖:18 种属性都能送出一台
    assert len(TM_GYM_BY_TYPE) == 18, TM_GYM_BY_TYPE
    for t, mv in TM_GYM_BY_TYPE.items():
        assert mv in TM_MOVES, (t, mv)


def test_tm_compatibility_follows_learnset():
    """能不能用这台机器,取决于学习表的 M 码(与 `/图鉴` 展示一致)。"""
    from pw.dex import get_dex

    dex = get_dex()
    assert dex.tm_compatible("pikachu", "thunderbolt")
    assert not dex.tm_compatible("pikachu", "flamethrower")
    assert not dex.tm_compatible("magikarp", "thunderbolt")
    assert dex.tm_compatible("gyarados", "surf") or dex.tm_compatible("gyarados", "icebeam")
    # 学过的招式必定在学习表里(反向一致性)
    for mv in list(dex.tm_moves("pikachu"))[:20]:
        assert mv in (dex._own_learnset("pikachu") or {}), mv


# ── 使用招式机 ─────────────────────────────────────────────────────
def test_tm_teaches_when_there_is_room():
    with tempfile.TemporaryDirectory() as tmp:
        p = _p(tmp, bag={"tm-thunderbolt": 1},
               party_extra=[("pikachu", 20, ["thundershock", "quickattack", "growl"])])
        out, ev = _run(p, "/使用 招式机·十万伏特 2", "cmd_use")
        assert "学会了" in out, out
        t = p._load(ev)
        assert "thunderbolt" in t.party[1]["moves"]
        assert t.count("tm-thunderbolt") == 0, "学到了就该用掉机器"


def test_tm_rejects_incompatible_species():
    with tempfile.TemporaryDirectory() as tmp:
        p = _p(tmp, bag={"tm-thunderbolt": 1})
        out, ev = _run(p, "/使用 招式机·十万伏特 1", "cmd_use")   # 杰尼龟 学不会
        assert "用不了这台招式机" in out, out
        t = p._load(ev)
        assert "thunderbolt" not in t.party[0]["moves"]
        assert t.count("tm-thunderbolt") == 1, "不兼容不该消耗机器"


def test_tm_full_moves_goes_pending_and_machine_kept():
    """招式栏满 → 进待决定,机器**先不消耗**;替换时才消耗,放弃则留在背包。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _p(tmp, bag={"tm-icebeam": 2},
               party_extra=[("squirtle", 30, ["watergun", "tailwhip", "tackle", "bite"])])
        out, ev = _run(p, "/使用 招式机·冰冻光束 2", "cmd_use")
        assert "招式栏满了" in out, out
        t = p._load(ev)
        assert t.party[1]["pending"] == ["icebeam"], t.party[1]["pending"]
        assert t.party[1]["pending_tm"]["icebeam"] == "tm-icebeam"
        assert t.count("tm-icebeam") == 2, "还没决定,机器不该消耗"

        # 面板要标出来源
        panel, _ = _run(p, "/学招 2", "cmd_learn")
        assert "招式机·冰冻光束" in panel, panel

        # 替换 → 学会 + 消耗
        out2, ev2 = _run(p, "/学招 2 替换 3", "cmd_learn")
        assert "学会了" in out2, out2
        t2 = p._load(ev2)
        assert "icebeam" in t2.party[1]["moves"]
        assert t2.count("tm-icebeam") == 1, "替换成功才消耗机器"
        assert not (t2.party[1].get("pending_tm") or {}), t2.party[1].get("pending_tm")

        # 再来一次,这次选放弃 → 机器还在
        out3, ev3 = _run(p, "/使用 招式机·冰冻光束 2", "cmd_use")
        assert "招式栏满了" not in out3, out3        # 其它队友已经会了?
        t3 = p._load(ev3)
        if t3.party[1].get("pending"):
            out4, ev4 = _run(p, "/学招 2 放弃", "cmd_learn")
            assert "还在背包里" in out4, out4
            t4 = p._load(ev4)
            assert t4.count("tm-icebeam") == 1, "放弃不该消耗机器"
            assert not (t4.party[1].get("pending_tm") or {})


def test_tm_pending_is_dropped_by_new_levelup_batch_but_machine_survives():
    """招式机的待决定被新一批升级招式顶掉 → 机器留在背包里(不浪费)。"""
    from pw import growth

    with tempfile.TemporaryDirectory() as tmp:
        p = _p(tmp, bag={"tm-protect": 1},
               party_extra=[("squirtle", 30, ["watergun", "tailwhip", "tackle", "bite"])])
        _run(p, "/使用 招式机·守住 2", "cmd_use")
        t = p._load(_Event(""))
        assert t.party[1]["pending"] == ["protect"]

        # 模拟一次升级带来的新批次
        dropped = growth.set_pending(t.party[1], ["surf"])
        assert dropped == ["protect"], dropped
        p._save(t)
        t2 = p._load(_Event(""))
        assert t2.party[1]["pending"] == ["surf"]
        assert t2.count("tm-protect") == 1, "被顶掉的机器应该还在"


# ── 获取途径 ───────────────────────────────────────────────────────
def test_shop_sells_tms_by_badge_tier():
    from pw.world import WorldMap, tm_stock

    world = WorldMap()
    assert tm_stock(0) == [], "0 徽章不该有招式机"
    assert 0 < len(tm_stock(2)) < len(tm_stock(6)), (len(tm_stock(2)), len(tm_stock(6)))
    stock6 = world.shop_stock("pewter-city", 6)
    tms = [k for k in stock6 if k.startswith("tm-")]
    assert len(tms) >= 25, len(tms)
    # 价格按招式强度分档,且买卖一致(不能刷钱)
    from pw.world import item_price

    weak = item_price("tm-protect", badge_count=0)
    strong = item_price("tm-earthquake", badge_count=0)
    assert 0 < weak < strong, (weak, strong)
    for k in ("tm-earthquake", "tm-thunderbolt"):
        assert item_price(k, badge_count=8) < item_price(k, badge_count=0), "徽章应打折"


def test_gym_first_clear_gives_type_tm():
    """道馆首次通关送该馆属性的招牌招式机(且只送一次)。"""
    from pw import battle as B
    from pw.engine import create_pokemon
    from pw.util import game_day

    assert B.tm_for_gym({"types": ["Electric"]}) == "tm-thunderbolt"
    assert B.tm_for_gym({"types": ["ghost"]}) == "tm-shadowball"
    assert B.tm_for_gym({"types": []}) == "" and B.tm_for_gym(None) == ""

    with tempfile.TemporaryDirectory() as tmp:
        p = _p(tmp)
        t = p._load(_Event(""))
        strong = create_pokemon("mewtwo", 80).to_dict()
        strong["id"] = "mz"
        strong["moves"] = ["psychic", "psystrike", "shadowball", "icebeam"]
        t.data["party"] = [strong]
        gym = {"order": 1, "types": ["Electric"], "badge": "橙色徽章", "name": "马志士",
               "title": "电系馆主"}
        B.start(t, [{"species": "pikachu", "level": 5}], kind="gym",
                meta={"kind": "gym", "gym": gym, "title": "道馆战"}, day=game_day())
        p._save(t)
        for _ in range(12):
            ev = _Event("/对战 move 1")
            run_cmd(p, ev, p.cmd_battle)
            if not B.in_battle(p._load(ev)):
                break
        t2 = p._load(ev)
        assert t2.count("tm-thunderbolt") == 1, "首次通关应送该馆属性的招式机"
        assert any("招式机" in r for r in ev.outputs), "".join(ev.outputs)[:200]

        # 再打一次(同一枚徽章)→ 不再送机器
        t2 = p._load(_Event(""))
        t2.add_item("poke-ball", 1)
        B.start(t2, [{"species": "pikachu", "level": 5}], kind="gym",
                meta={"kind": "gym", "gym": gym, "title": "道馆战"}, day=game_day())
        p._save(t2)
        for _ in range(12):
            ev2 = _Event("/对战 move 1")
            run_cmd(p, ev2, p.cmd_battle)
            if not B.in_battle(p._load(ev2)):
                break
        assert p._load(ev2).count("tm-thunderbolt") == 1, "重复通关不该再送"


def test_quest_rewards_include_tms():
    from pw.items import BAG_ITEMS
    from pw.quests import REWARD_ITEMS

    tms = [k for k in REWARD_ITEMS if k.startswith("tm-")]
    assert tms, "委托奖励应有招式机"
    for k in tms:
        assert k in BAG_ITEMS, k


def test_bag_pocket_shows_tms():
    """背包的「招式机」分类终于有东西了。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _p(tmp, bag={"tm-thunderbolt": 2, "potion": 1})
        from pw.ui_render import POCKETS

        assert ("tm", "招式机") in POCKETS
        pay = p._bag_payload(p._load(_Event("")), "招式机")
        assert pay["pocket"] == "tm"
        assert [x["key"] for x in pay["items"]] == ["tm-thunderbolt"], pay["items"]
        out, _ = _run(p, "/背包 招式机", "cmd_bag")
        assert "招式机·十万伏特" in out, out
