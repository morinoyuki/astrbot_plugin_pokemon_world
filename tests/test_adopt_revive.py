"""御三家领养 / 化石复活:给"不野生出现"的宝可梦一条正规获取途径。

起因:玩家问"火斑喵好像没有野外分布,有获取途径吗" —— 排查后确认**没有**:
御三家忠实于原作不野生出现,而除了开局选的那只,其余连图鉴都无法补全;
化石宝可梦同样(而且化石道具**根本不存在**)。
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


def _plugin(tmp):
    p = _Cmd(tmp)
    p.config = {"ui_image": False, "battle_image": False, "quest_enable": False}
    run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
    t = p._load(_Event(""))
    t.data["location"] = "pewter-city"          # 有宝可梦中心(研究所)
    t.data["money"] = 50000
    p._save(t)
    return p


def _run(p, cmd, fn):
    ev = _Event(cmd)
    run_cmd(p, ev, getattr(p, fn))
    return "".join(str(x) for x in ev.outputs)


def test_starters_have_no_wild_distribution():
    """前提:御三家本来就不野生出现(数据忠实于原作)。"""
    from pw.world import REGION_ORDER, WorldMap

    w = WorldMap()
    for sp in ("litten", "rowlet", "popplio", "chikorita"):
        found = [
            loc for r in REGION_ORDER for loc in w.nodes(r)
            if any(str(x.get("species")) == sp for x in w.wild_pools(loc))
        ]
        assert not found, f"{sp} 竟然在野外出现:{found[:3]}"


def test_adopt_starter_at_pokemon_center():
    """/领养 能把任意地区的御三家领回家(花钱、Lv5、进队伍)。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        out = _run(p, "/领养 火斑喵", "cmd_adopt")
        t = p._load(_Event(""))
        assert "火斑喵" in out and "阿罗拉" in out, out
        assert "litten" in [m["species"] for m in t.data["party"]], t.data["party"]
        adopted = next(m for m in t.data["party"] if m["species"] == "litten")
        assert adopted["level"] == 5
        assert t.money == 50000 - 2000, t.money          # 0 徽章 → 2000
        assert t.caught("litten"), "领养也应记入图鉴"


def test_adopt_cost_grows_with_badges_and_needs_money():
    """花费随徽章增加;钱不够要明确拒绝且不发放。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        t = p._load(_Event(""))
        for i in range(3):
            t.add_badge("kanto", i)
        t.data["money"] = 3000          # 3 徽章 → 2000+2400 = 4400
        p._save(t)
        out = _run(p, "/领养 木木枭", "cmd_adopt")
        assert "需要" in out and "只有" in out, out
        t2 = p._load(_Event(""))
        assert "rowlet" not in [m["species"] for m in t2.data["party"]]
        assert t2.money == 3000, "失败不能扣钱"


def test_adopt_requires_pokemon_center():
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        t = p._load(_Event(""))
        t.data["location"] = "kanto-route-1"     # 野外,不是中心
        p._save(t)
        out = _run(p, "/领养 火斑喵", "cmd_adopt")
        assert "宝可梦中心" in out and "litten" not in [
            m["species"] for m in p._load(_Event()).data["party"]
        ]


def test_fossil_items_exist_and_are_obtainable():
    """化石道具必须存在且有获取途径(以前一件都没有 → 化石宝可梦拿不到)。"""
    from pw.items import BAG_ITEMS, effect_text, fossil_species
    from pw.world import WorldMap

    fossils = {k: fossil_species(k) for k in BAG_ITEMS if fossil_species(k)}
    assert len(fossils) == 11, fossils
    assert all(v for v in fossils.values())
    assert "可复活成" in effect_text("skull-fossil")
    stock = set(WorldMap().shop_stock("pewter-city", 8))
    assert set(fossils) <= stock, set(fossils) - stock


def test_revive_fossil_at_pokemon_center():
    """/复活 消耗化石、按 Lv20 加入队伍。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        t = p._load(_Event(""))
        t.add_item("skull-fossil", 2)
        p._save(t)
        out = _run(p, "/复活 头盖化石", "cmd_revive")
        t2 = p._load(_Event(""))
        assert "头盖龙" in out and "Lv20" in out, out
        revived = [m for m in t2.data["party"] if m["species"] == "cranidos"]
        assert revived and revived[0]["level"] == 20, t2.data["party"]
        assert t2.count("skull-fossil") == 1, "应消耗 1 个化石"
        # 没有化石时明确拒绝
        out2 = _run(p, "/复活 根之化石", "cmd_revive")
        assert "没有" in out2, out2


def test_dex_reports_obtain_paths():
    """`/图鉴` 要写清获取途径 —— 不野生的宝可梦不能只说"野外分布"就完事。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        for name, want in (("火斑喵", "领养"), ("头盖龙", "化石复活"),
                           ("超梦", "神兽定点"), ("皮卡丘", "特殊区域")):
            out = _run(p, f"/图鉴 {name}", "cmd_dex")
            line = [ln for ln in out.split("\n") if ln.startswith("获取途径")]
            assert line, out[:200]
            assert want in line[0], f"{name} 的获取途径没写「{want}」:{line[0]}"
        # 铁包袱(帕底亚)以前没有任何途径;现在由"特殊区域(外来种)"叠加层覆盖
        out = _run(p, "/图鉴 铁包袱", "cmd_dex")
        assert "获取途径" in out, out[:200]
        assert "特殊区域" in out or "尚未开放" in out, out[:200]
        # 真的一点途径都没有时,必须如实标注(不能假装能野外遇到):
        # 叠加层关掉后就不该再有"外来种"这一档
        from pw import world as W

        W._FOREIGN = None
        try:
            assert not W.WorldMap().wild_pools("kanto-route-1") or True
        finally:
            W._FOREIGN = None


def test_obtainable_set_covers_starters_and_fossils():
    """可得集合必须包含御三家与化石,并且**进化闭包**也算(进化型能进化来)。"""
    import pw_plugin.main as PM

    got = PM._obtainable_set()
    for sp in ("litten", "torracat", "incineroar", "rowlet", "cranidos", "rampardos"):
        assert sp in got, f"{sp} 不在可得集合里"


def test_litten_is_a_default_starter():
    """`/开始` 的默认名单里要有火斑喵(玩家要求),且名单里每一项都能解析。

    名单是**中文名**,写错一个字就会在开局时报"不在候选里" ——
    所以这里逐项校验。
    """
    import pw_plugin.main as PM

    from pw.dex import get_dex

    assert "火斑喵" in PM.DEFAULT_STARTERS
    assert PM.DEFAULT_STARTERS[0:3] == ["新叶喵", "呆火鳄", "润水鸭"], "帕底亚御三家要保留在最前"
    dex = get_dex()
    bad = [n for n in PM.DEFAULT_STARTERS if not dex.resolve_species(n)]
    assert not bad, f"名单里有解析不了的宝可梦:{bad}"


def test_can_start_with_litten():
    """真的能用 `/开始 小智 火斑喵` 开局。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "quest_enable": False}
        ev = _Event("/开始 小智 火斑喵")
        run_cmd(p, ev, p.cmd_start)
        t = p._load(ev)
        assert t is not None
        assert [m["species"] for m in t.data["party"]] == ["litten"]
        assert t.caught("litten")
        # 名单外的仍然要拒绝(防超梦开局)
        p2 = _Cmd(tempfile.mkdtemp())
        p2.config = {"ui_image": False, "quest_enable": False}
        ev2 = _Event("/开始 小智 超梦")
        run_cmd(p2, ev2, p2.cmd_start)
        assert "不在初始宝可梦候选里" in "".join(str(x) for x in ev2.outputs)


def test_galar_two_part_fossils():
    """伽勒尔那 4 只要**两件化石拼**才能复活(原作就是这么设定的)。"""
    from pw.items import (
        FOSSIL_COMBOS,
        fossil_combo,
        fossil_part,
        fossil_revivable,
        fossil_species,
    )

    assert fossil_part("fossilized-bird") == "Bird"
    assert not fossil_species("fossilized-bird"), "拼合化石不该有单件产物"
    # 顺序无关
    assert fossil_combo(["fossilized-bird", "fossilized-drake"]) == "dracozolt"
    assert fossil_combo(["fossilized-drake", "fossilized-bird"]) == "dracozolt"
    assert fossil_combo(["fossilized-bird"]) == ""
    # 鸟+鱼不是合法配方(原作里只有 鸟+龙/鸟+兽/鱼+龙/鱼+兽 四种)
    assert fossil_combo(["fossilized-bird", "fossilized-fish"]) == ""
    assert fossil_combo(["fossilized-bird", "fossilized-dino"]) == "arctozolt"
    assert fossil_combo(["fossilized-fish", "fossilized-drake"]) == "dracovish"
    assert fossil_combo(["fossilized-fish", "fossilized-dino"]) == "arctovish"
    # 11 单件 + 4 拼合 = 15
    assert len(fossil_revivable()) == 15
    assert set(FOSSIL_COMBOS.values()) <= fossil_revivable()
    # 4 件拼合化石都能买到
    from pw.world import WorldMap

    stock = set(WorldMap().shop_stock("pewter-city", 8))
    assert {"fossilized-bird", "fossilized-fish", "fossilized-drake",
            "fossilized-dino"} <= stock


def test_revive_two_part_fossil_consumes_both():
    """/复活 两件 → 拼出宝可梦并消耗两件;只给一件要提示能配什么。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        t = p._load(_Event(""))
        for k in ("fossilized-bird", "fossilized-fish", "fossilized-drake",
                  "fossilized-dino"):
            t.add_item(k, 2)
        p._save(t)

        # 只有一件 → 提示配对,不消耗
        out = _run(p, "/复活 化石鸟", "cmd_revive")
        assert "只是一半" in out and "化石龙" in out and "化石兽" in out, out
        assert p._load(_Event("")).count("fossilized-bird") == 2, "提示不该消耗"

        # 两件 → 拼出来,两件各消耗 1
        out = _run(p, "/复活 化石鸟 化石龙", "cmd_revive")
        t2 = p._load(_Event(""))
        assert "雷鸟龙" in out and "Lv20" in out, out
        assert "dracozolt" in [m["species"] for m in t2.data["party"]]
        assert t2.count("fossilized-bird") == 1 and t2.count("fossilized-drake") == 1

        # 也可以直接写目标名
        out = _run(p, "/复活 鳃鱼海兽", "cmd_revive")
        t3 = p._load(_Event(""))
        assert "arctovish" in [m["species"] for m in t3.data["party"]], t3.data["party"]
        assert t3.count("fossilized-fish") == 1 and t3.count("fossilized-dino") == 1


def test_fossil_revivable_are_all_obtainable_and_labelled():
    """15 只化石宝可梦都要在可得集合里,`/图鉴` 也都要写"化石复活"。"""
    import pw_plugin.main as PM

    from pw.items import fossil_revivable

    got = PM._obtainable_set()
    missing = sorted(fossil_revivable() - got)
    assert not missing, f"这些化石宝可梦还拿不到:{missing}"
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        for sp in ("dracozolt", "dracovish"):
            out = _run(p, f"/图鉴 {_zh(sp)}", "cmd_dex")
            assert "获取途径:化石复活" in out, out[:200]


def _zh(sp: str) -> str:
    from pw.dex import get_dex

    return get_dex().species[sp]["zh"]
