"""`/回忆` —— 宝可梦中心的「招式教学狂」:心之鳞片换回忘掉的招式。

忠实于正作的回忆规则:
  · 等级招可以回忆到**当前等级及以前**学会的(更高等级的招不行);
  · 蛋招式(出生时就会的)也可以回忆;
  · 招式机 / 教学 / 活动招式不在其列(招式机在本插件里是道具经济);
  · 仅进化前能学的招不算(第九世代起不再支持);
  · 当前世代已无法使用的非标准招式不能教(教了也不能用)。
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

CENTER = "pewter-city"          # 有宝可梦中心(研究所)


def _plugin(tmp):
    p = _Cmd(tmp)
    p.config = {"ui_image": False, "battle_image": False, "quest_enable": False}
    run_cmd(p, _Event("/开始 小智 新叶喵"), p.cmd_start)
    t = p._load(_Event(""))
    t.data["location"] = CENTER
    p._save(t)
    return p


def _t(p):
    return p._load(_Event(""))


def _run(p, cmd, fn="cmd_recall"):
    ev = _Event(cmd)
    run_cmd(p, ev, getattr(p, fn))
    return "".join(str(x) for x in ev.outputs)


class _Rng:
    """只会返回固定随机数的假 rng(`random()` 的返回值可指定)。"""

    def __init__(self, value=0.0):
        self.value = value

    def random(self):
        return self.value


# ── 数据层:回忆列表符合正作规则 ──────────────────────────────

def test_relearn_list_is_level_and_egg_moves_only():
    from pw.dex import get_dex

    dex = get_dex()
    low = {c["move"] for c in dex.relearn_moves("charmander", 10)}
    high = {c["move"] for c in dex.relearn_moves("charmander", 100)}
    assert "ember" in low                      # 等级 4,当前等级以内
    assert "flamethrower" not in low            # 等级 24,当前等级学不到
    assert "flamethrower" in high
    assert "aircutter" in high                  # 蛋招式(出生时就会)
    assert "flameburst" not in high             # Gen9 已无法使用的非标准招式
    # 招式机 / 教学招式不能靠回忆拿
    row = dex._own_learnset("charmander")
    non_relearn = [
        m for m, c in row.items()
        if not any(x.startswith("L") or x == "E" for x in str(c).split(","))
    ]
    assert non_relearn, "数据前提:小火龙应该有只能靠 M/T/S 学的招"
    assert not (set(non_relearn) & high)
    # 蛋招式排在等级招之后
    levels = [c["level"] for c in dex.relearn_moves("charmander", 100)]
    assert levels == sorted(x for x in levels if x is not None) + [None] * levels.count(None)


def test_relearn_does_not_borrow_pre_evolution_moves():
    """仅进化前能学的招不能回忆(第九世代起:进化后只认自己的学习表)。"""
    from pw.dex import get_dex

    dex = get_dex()
    got = {c["move"] for c in dex.relearn_moves("floatzel", 100)}
    assert "tackle" not in got, "泳圈鼬的撞击不该出现在浮潜鼬的回忆列表里"
    assert "tackle" in {c["move"] for c in dex.relearn_moves("buizel", 100)}


def test_heart_scale_item_is_obtainable_and_documented():
    from pw.items import BAG_ITEMS, effect_text
    from pw.world import WorldMap

    entry = BAG_ITEMS.get("heart-scale")
    assert entry and entry["zh"] == "心之鳞片"
    assert "回忆" in effect_text("heart-scale")
    stock = WorldMap().shop_stock(CENTER, 8)
    assert "heart-scale" in stock, "心之鳞片必须有获取途径"
    assert any("心之鳞片" in (BAG_ITEMS[k].get("zh") or "") for k in stock)


def test_heart_scale_drops_at_water_biomes_more_often():
    from pw.items import scale_biome

    assert scale_biome("kanto-sea-route-21")
    assert not scale_biome("kanto-route-1")
    # 水边掉率(15%)远高于别处(3%):同一随机数下,水边会掉、内陆不会
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        t = _t(p)
        t.data["location"] = "kanto-sea-route-21"      # 海路(水边)
        assert "心之鳞片" in p._maybe_scale_find(t, _Rng(0.10))
        t.data["location"] = "kanto-route-1"           # 内陆道路
        assert p._maybe_scale_find(t, _Rng(0.10)) == ""
        assert t.count("heart-scale") == 1


# ── 指令层 ───────────────────────────────────────────────────

def test_recall_needs_pokemon_center():
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        t = _t(p)
        t.data["location"] = "kanto-route-1"
        t.add_item("heart-scale", 2)
        p._save(t)
        out = _run(p, "/回忆 1")
        assert "宝可梦中心" in out, out
        assert _t(p).count("heart-scale") == 2, "被拒绝时不能扣道具"


def test_recall_needs_heart_scale():
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        out = _run(p, "/回忆 1 1")
        assert "心之鳞片" in out and "没有" in out, out
        mon = _t(p).mon(0)
        assert "allyswitch" not in mon.moves, "没付报酬就不能学会"


def test_recall_learns_move_into_free_slot():
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        t = _t(p)
        t.add_item("heart-scale", 2)
        p._save(t)
        out = _run(p, "/回忆 1 1")
        assert "想起了" in out and "用掉 1 枚" in out, out
        t2 = _t(p)
        mon = t2.mon(0)
        assert "allyswitch" in mon.moves, mon.moves          # 列表第 1 个是蛋招式
        assert mon.pp.get("allyswitch"), "新招的 PP 要一起写上"
        assert t2.count("heart-scale") == 1


def test_recall_full_moveset_requires_replace():
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        t = _t(p)
        mon = t.mon(0)
        # 覆盖新叶喵 Lv5 已能学的全部等级招,列表第 1 个才是蛋招式「交换场地」
        mon.moves = ["scratch", "leafage", "tailwhip", "quickattack"]
        mon.pp = dict.fromkeys(mon.moves, 20)
        t.commit(0, mon)
        t.add_item("heart-scale", 1)
        p._save(t)
        # 招式栏满时不给替换目标 → 拒绝,不消耗
        out = _run(p, "/回忆 1 1")
        assert "招式栏满了" in out, out
        assert _t(p).count("heart-scale") == 1
        # 写明替换哪一招 → 换掉并消耗
        out = _run(p, "/回忆 1 1 替换 4")
        assert "忘掉了" in out and "想起了" in out, out
        t2 = _t(p)
        mon2 = t2.mon(0)
        assert mon2.moves[3] == "allyswitch", mon2.moves
        assert "quickattack" not in mon2.moves
        assert t2.count("heart-scale") == 0


def test_recall_rejects_known_and_non_relearnable_moves():
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        t = _t(p)
        t.add_item("heart-scale", 2)
        p._save(t)
        known = _t(p).mon(0).moves[0]
        out = _run(p, f"/回忆 1 {known}")
        assert "已经会" in out, out
        # 招式机招式(新叶喵学不了 / 不在回忆列表)不能靠心之鳞片拿
        out = _run(p, "/回忆 1 剑舞")
        assert "想不起" in out, out
        assert _t(p).count("heart-scale") == 2, "被拒绝时不能扣道具"
        # 面板本身要能列出可回忆的招式与现有招式
        out = _run(p, "/回忆 1")
        assert "能想起的招式" in out and "现有招式" in out, out


def test_recall_clears_same_pending_entry():
    """回忆到手的招式若正挂在"待决定"里,那条记录要顺手消掉。

    否则 `/学招` 会再要求决定一次 —— 那时招式已经会了,替换必然失败。
    """
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        t = _t(p)
        t.add_item("heart-scale", 1)
        t.add_item("tm-protect", 1)
        t.party[0]["pending"] = ["allyswitch"]
        t.party[0]["pending_tm"] = {"allyswitch": "tm-protect"}
        p._save(t)
        out = _run(p, "/回忆 1 1")
        assert "想起了" in out, out
        t2 = _t(p)
        assert "allyswitch" in t2.mon(0).moves
        assert not (t2.party[0].get("pending") or []), t2.party[0]
        assert not (t2.party[0].get("pending_tm") or {}), t2.party[0]
        assert t2.count("tm-protect") == 1, "回忆不该消耗背包里的招式机"


def test_use_heart_scale_points_to_recall():
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        t = _t(p)
        t.add_item("heart-scale", 1)
        p._save(t)
        out = _run(p, "/使用 心之鳞片", "cmd_use")
        assert "回忆" in out and "招式教学狂" in out, out
        assert _t(p).count("heart-scale") == 1, "提示用法不应消耗道具"


def test_recall_index_lists_party():
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        t = _t(p)
        t.add_item("heart-scale", 1)
        p._save(t)
        out = _run(p, "/回忆")
        assert "招式教学狂" in out and "可回忆" in out, out
        assert "/回忆 1" in out, out
