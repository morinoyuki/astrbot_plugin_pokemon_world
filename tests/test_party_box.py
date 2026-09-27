"""单只宝可梦资料 / 队伍-仓库管理 / 对战锁定 这三块新功能的测试。"""

from __future__ import annotations

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402

from pw import battle as B  # noqa: E402
from pw.engine import create_pokemon  # noqa: E402
from pw.player import MAX_PARTY  # noqa: E402
from pw.util import game_day  # noqa: E402


def _start(tmp, **cfg):
    p = _Cmd(tmp)
    p.config = {"ui_image": False, "quest_enable": False, **cfg}
    ev = _Event("/开始 小智 新叶喵")
    run_cmd(p, ev, p.cmd_start)
    return p, ev


def _run(p, cmd, attr):
    ev = _Event(cmd)
    run_cmd(p, ev, getattr(p, attr))
    return "".join(ev.outputs)


def _add_box(p, ev, species, level, ident):
    t = p._load(ev)
    d = create_pokemon(species, level).to_dict()
    d["id"] = ident
    t.data["box"].append(d)
    p._save(t)


def _start_battle(p, ev, *, kind="gym", level=14, species="geodude"):
    t = p._load(ev)
    B.start(t, [{"species": species, "level": level}], kind=kind,
            meta={"kind": kind, "title": "测试战"}, day=game_day())
    p._save(t)
    return t


# ══════════════════════════════════════════════════════════════════
# /宝可梦 —— 单只资料
# ══════════════════════════════════════════════════════════════════
def test_mon_detail_shows_full_profile():
    with tempfile.TemporaryDirectory() as tmp:
        p, _ev = _start(tmp)
        out = _run(p, "/宝可梦", "cmd_mon")
        for token in ("新叶喵", "Lv5", "属性:草", "能力值", "攻击",
                      "特性:茂盛", "性格:", "亲密:", "招式", "PP"):
            assert token in out, f"资料页缺少 {token}:\n{out}"
        # 种族值要带上(括号内)
        assert "(61)" not in out or "(" in out
        assert "种族值" in out or "(40)" in out


def test_mon_detail_by_name_and_box_prefix():
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        _add_box(p, ev, "pidgey", 7, "bx1")
        by_name = _run(p, "/宝可梦 新叶喵", "cmd_mon")
        assert "新叶喵" in by_name
        # 名字也能匹配到电脑里的
        in_box = _run(p, "/宝可梦 电脑 1", "cmd_mon")
        assert "波波" in in_box and "Lv7" in in_box
        # 找不到 → 明确报错
        assert "❌" in _run(p, "/宝可梦 喷火龙", "cmd_mon")
        assert "❌" in _run(p, "/宝可梦 电脑 9", "cmd_mon")


def test_mon_detail_defaults_to_active_in_battle():
    """/宝可梦 不带参数时,对战中看当前出战的那只。"""
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        second = create_pokemon("pidgey", 9).to_dict()
        second["id"] = "m2"
        t = p._load(ev)
        t.data["party"].append(second)
        p._save(t)
        _start_battle(p, ev, level=20)
        _run(p, "/对战 switch 2", "cmd_battle")
        out = _run(p, "/宝可梦", "cmd_mon")
        assert "波波" in out, f"应看当前出战的那只:\n{out[:200]}"


def test_mon_detail_image_and_text_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        p, _ev = _start(tmp, ui_image=True, battle_image_scale=2)
        ev2 = _Event("/宝可梦")
        run_cmd(p, ev2, p.cmd_mon)
        assert ev2.outputs[0].startswith("<chain:"), "应输出资料图片"
        p.config = {"ui_image": False, "quest_enable": False}
        out = _run(p, "/宝可梦", "cmd_mon")
        assert "<chain:" not in out and "新叶喵" in out


# ══════════════════════════════════════════════════════════════════
# /电脑 + /队伍 管理
# ══════════════════════════════════════════════════════════════════
def test_box_listing():
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        assert "空" in _run(p, "/电脑", "cmd_box")
        _add_box(p, ev, "pidgey", 5, "bx1")
        _add_box(p, ev, "rattata", 8, "bx2")
        out = _run(p, "/电脑", "cmd_box")
        assert "波波" in out and "小拉达" in out and "2 只" in out
        # 管理入口(作为 /队伍 的子指令也能看)
        assert "波波" in _run(p, "/队伍 电脑", "cmd_team")


def test_deposit_withdraw_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        second = create_pokemon("pidgey", 6).to_dict()
        second["id"] = "m2"
        t = p._load(ev)
        t.data["party"].append(second)
        p._save(t)

        out = _run(p, "/队伍 存入 2", "cmd_team")
        assert "已存入电脑" in out
        t = p._load(ev)
        assert len(t.party) == 1 and len(t.box) == 1
        assert t.box[0]["species"] == "pidgey"

        out = _run(p, "/队伍 取出 1", "cmd_team")
        assert "加入了队伍" in out
        t = p._load(ev)
        assert len(t.party) == 2 and len(t.box) == 0
        assert t.party[-1]["species"] == "pidgey"


def test_cannot_deposit_last_pokemon_or_overflow_party():
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        assert "至少" in _run(p, "/队伍 存入 1", "cmd_team")
        assert p._load(ev).party, "不能把最后一只存进电脑"

        _add_box(p, ev, "pidgey", 5, "bx1")
        t = p._load(ev)          # 必须在 _add_box 之后再取,否则覆盖掉刚加的仓库条目
        for i in range(MAX_PARTY - 1):
            d = create_pokemon("rattata", 3).to_dict()
            d["id"] = f"f{i}"
            t.data["party"].append(d)
        p._save(t)
        assert len(p._load(ev).party) == MAX_PARTY
        out = _run(p, "/队伍 取出 1", "cmd_team")
        assert "满" in out, out
        assert len(p._load(ev).box) == 1, "取出失败时不能把仓库里的删掉"


def test_swap_party_order():
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        t = p._load(ev)
        for i, sp in enumerate(("pidgey", "rattata"), 1):
            d = create_pokemon(sp, 4).to_dict()
            d["id"] = f"s{i}"
            t.data["party"].append(d)
        p._save(t)
        assert "已交换" in _run(p, "/队伍 换位 1 3", "cmd_team")
        t = p._load(ev)
        assert t.party[0]["species"] == "rattata"
        assert t.party[2]["species"] == "sprigatito"
        # 越界/相同 → 拒绝
        assert "❌" in _run(p, "/队伍 换位 1 9", "cmd_team")
        assert "❌" in _run(p, "/队伍 换位 2 2", "cmd_team")


def test_release_requires_confirmation_and_keeps_last():
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        t = p._load(ev)
        d = create_pokemon("pidgey", 5).to_dict()
        d["id"] = "m2"
        t.data["party"].append(d)
        p._save(t)

        assert "确认" in _run(p, "/队伍 放生 2", "cmd_team")
        assert len(p._load(ev).party) == 2, "没确认就不该放生"

        assert "放生了" in _run(p, "/队伍 放生 2 确认", "cmd_team")
        assert len(p._load(ev).party) == 1

        # 最后一只不能放生
        assert "❌" in _run(p, "/队伍 放生 1 确认", "cmd_team")
        assert len(p._load(ev).party) == 1


def test_release_from_box():
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        _add_box(p, ev, "pidgey", 5, "bx1")
        assert "确认" in _run(p, "/电脑 放生 1", "cmd_box")
        assert len(p._load(ev).box) == 1
        assert "放生了" in _run(p, "/电脑 放生 1 确认", "cmd_box")
        assert p._load(ev).box == []
        assert "❌" in _run(p, "/电脑 放生 1 确认", "cmd_box")


# ══════════════════════════════════════════════════════════════════
# 对战锁定
# ══════════════════════════════════════════════════════════════════
LOCKED_CMDS = (
    ("/探索", "cmd_explore"),
    ("/前往 常青市", "cmd_go"),
    ("/治疗", "cmd_heal"),
    ("/使用 伤药 1", "cmd_use"),
    ("/主线 挑战", "cmd_story"),
    ("/神兽 挑战 急冻鸟", "cmd_legend"),
    ("/大赛 挑战", "cmd_tournament"),
    ("/学招 1 喷射火焰", "cmd_learn"),
    ("/进化 1", "cmd_evolve"),
    ("/交换 1", "cmd_trade"),
    ("/商店 买 伤药 1", "cmd_shop"),
    ("/道馆 挑战", "cmd_gym"),
    ("/联盟 挑战", "cmd_league"),
)

FREE_CMDS = (
    ("/状态", "cmd_status"),
    ("/队伍", "cmd_team"),
    ("/背包", "cmd_bag"),
    ("/宝可梦", "cmd_mon"),
    ("/电脑", "cmd_box"),
    ("/图鉴 新叶喵", "cmd_dex"),
    ("/地图", "cmd_map"),
    ("/今日", "cmd_today"),
    ("/任务", "cmd_quest"),
)


def test_battle_locks_out_other_actions():
    """对战中其他行动必须全部禁用(用户要求),查看类仍可用。"""
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        t = p._load(ev)
        t.data["location"] = "pewter-city"
        p._save(t)
        _start_battle(p, ev, level=30)

        for cmd, attr in LOCKED_CMDS:
            out = _run(p, cmd, attr)
            assert "锁定" in out or "对战中" in out, f"{cmd} 应当被锁:\n{out[:120]}"
            assert "对战" in out, f"{cmd} 的提示要告诉玩家怎么打:\n{out[:120]}"

        for cmd, attr in FREE_CMDS:
            out = _run(p, cmd, attr)
            assert "其他行动已锁定" not in out, f"{cmd} 不该被锁:\n{out[:120]}"

        # 队伍/仓库的管理操作也不行(否则能中途换满血队)
        for cmd, attr in (("/队伍 存入 1", "cmd_team"),
                          ("/队伍 取出 1", "cmd_team"),
                          ("/队伍 放生 1 确认", "cmd_team"),
                          ("/电脑 放生 1 确认", "cmd_box"),
                          ("/任务 放弃 1", "cmd_quest")):
            out = _run(p, cmd, attr)
            assert "对战中不能" in out or "锁定" in out, f"{cmd} 应当被拒:\n{out[:120]}"


def test_actions_unlock_after_battle_ends():
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        t = p._load(ev)
        t.data["location"] = "pewter-city"
        p._save(t)
        _start_battle(p, ev, level=30)
        assert "锁定" in _run(p, "/探索", "cmd_explore")

        # 认输 → 战斗结束 → 行动恢复
        _run(p, "/对战 forfeit", "cmd_battle")
        assert not B.in_battle(p._load(ev)), "认输后不该还在对战"
        # 用 /治疗 验证(它不会像 /探索 那样又掷出一次野生遭遇)
        out = _run(p, "/治疗", "cmd_heal")
        assert "锁定" not in out, out[:120]
        out = _run(p, "/探索", "cmd_explore")
        if not B.in_battle(p._load(ev)):
            assert "锁定" not in out, out[:120]


def test_status_reports_battle_state():
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        out = _run(p, "/状态", "cmd_status")
        assert "没有在战斗" in out, out[:200]

        _start_battle(p, ev, level=12, species="pidgey")
        out = _run(p, "/状态", "cmd_status")
        assert "正在对战中" in out, out[:200]
        assert "波波" in out, f"要显示对手是谁:\n{out[:200]}"
        assert "我方" in out, f"要显示我方出战:\n{out[:200]}"
        # 训练家卡末行也要能看出在对战
        assert "对战中" in p._card_battle_line(p._load(ev))


def test_player_can_always_forfeit_out_of_battle():
    """认输必须是玩家最后的逃生口 —— 包括"场上倒下、等着换人"的时候。

    否则:其他行动被对战锁定 + 换人指令打错/队伍里没有可换的 → 玩家出不去。
    """
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        t = p._load(ev)
        d = create_pokemon("pidgey", 40).to_dict()
        d["id"] = "m2"
        t.data["party"].append(d)
        p._save(t)
        _start_battle(p, ev, level=50)

        # 打到场上那只倒下(必须换人),然后直接认输
        for _ in range(12):
            t = p._load(_Event())
            if not t.data.get("battle"):
                break
            run_cmd(p, _Event("/对战 1"), p.cmd_battle)
            t = p._load(_Event())
            sess = (t.data.get("battle") or {}).get("battle") or {}
            if sess.get("awaiting_switch") or not t.data.get("battle"):
                break
        out = _run(p, "/对战 forfeit", "cmd_battle")
        assert not B.in_battle(p._load(_Event())), f"认输没能结束战斗:{out[:150]}"
        # 结束后其他行动恢复
        assert "锁定" not in _run(p, "/治疗", "cmd_heal")


# ══════════════════════════════════════════════════════════════════
# /招式 —— 招式说明与效果
# ══════════════════════════════════════════════════════════════════
def test_move_list_shows_effect_not_just_name():
    """列表不能只给名字 —— 每招都要有属性/分类/威力/PP + 一句效果。"""
    with tempfile.TemporaryDirectory() as tmp:
        p, _ev = _start(tmp)
        out = _run(p, "/招式", "cmd_move")
        assert "的招式" in out
        for token in ("[草/", "威力40", "PP ", "使对手", "防御 -1", "/招式 <序号>"):
            assert token in out, f"招式列表缺少 {token}:\n{out}"


def test_move_detail_has_fields_and_learn_state():
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        t = p._load(ev)
        t.data["party"][0]["moves"] = ["scratch", "leafage", "tailwhip", "growl"]
        t.data["party"][0]["pp"] = {"scratch": 35, "leafage": 40, "tailwhip": 30, "growl": 40}
        p._save(t)
        out = _run(p, "/招式 1", "cmd_move")
        for token in ("属性:", "分类:", "威力:", "命中:", "PP:", "优先度:",
                      "实际效果:", "已经学会"):
            assert token in out, f"详情缺少 {token}:\n{out}"

        # 队里没人会的招式 → 仍能查图鉴资料,但要说清楚
        out = _run(p, "/招式 十万伏特", "cmd_move")
        assert "十万伏特" in out and "没有学会这招" in out
        assert "麻痹" in out, out
        # 序号越界 / 名字不存在
        assert "❌" in _run(p, "/招式 9", "cmd_move")
        assert "❌" in _run(p, "/招式 不存在的招", "cmd_move")


def test_move_can_target_party_or_box_mon():
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        second = create_pokemon("charmander", 8).to_dict()
        second["id"] = "m2"
        t = p._load(ev)
        t.data["party"].append(second)
        p._save(t)
        _add_box(p, ev, "pidgey", 6, "bx1")

        out = _run(p, "/招式 2 1", "cmd_move")      # 第 2 只宝可梦的第 1 招
        assert "小火龙" in out, f"详情要写清是谁的招式:{out}"   # 标题带持有者
        assert "实际效果:" in out
        out = _run(p, "/招式 电脑 1", "cmd_move")
        assert "波波" in out and "电脑" in out
        assert "❌" in _run(p, "/招式 电脑 9", "cmd_move")
        assert "❌" in _run(p, "/招式 5 1", "cmd_move")


def test_move_lookup_during_battle_is_free():
    """对战中查招式必须放行,而且**不消耗回合**。"""
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        t = p._load(ev)
        t.data["region"] = "kanto"
        p._save(t)
        _start_battle(p, ev, level=12, species="pidgey")
        turn0 = ((p._load(ev).data.get("battle") or {}).get("battle") or {}).get("turn")

        out = _run(p, "/招式", "cmd_move")
        assert "其他行动已锁定" not in out, out
        assert "招式" in out
        out = _run(p, "/招式 2", "cmd_move")
        assert "实际效果:" in out, out
        turn1 = ((p._load(ev).data.get("battle") or {}).get("battle") or {}).get("turn")
        assert turn0 == turn1, "查招式不该推进回合"

        # 对战提示里要告诉玩家可以查招式
        hint = p._battle_hint(p._load(ev))
        assert "/招式" in hint, hint


def test_bai_zhong_moves_are_not_shown_as_1_percent():
    """数据里 accuracy=True 是"必中",不是 1%(Python 的 True 是 int)。"""
    from pw.dex import get_dex

    dex = get_dex()
    assert dex.moves["aerialace"].get("accuracy") is True     # 必中招式
    from test_commands import _MOD as _PKG

    acc_mod = sys.modules.get("pw_plugin.main", _PKG)
    out = acc_mod._accuracy_zh(True)
    assert out == "必中", out
    assert acc_mod._accuracy_zh(100) == "100%"
    assert acc_mod._accuracy_zh(None) == "必中"
    # 列表里不能出现"命中1%"
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        t = p._load(ev)
        t.data["party"][0]["moves"] = ["aerialace", "swift"]
        t.data["party"][0]["pp"] = {"aerialace": 20, "swift": 20}
        p._save(t)
        listed = _run(p, "/招式", "cmd_move")
        assert "命中1%" not in listed, listed


def test_move_effect_text_is_derived_from_structured_fields():
    """效果文本要来自**真正生效的字段**(而不是图鉴风味文字)。"""
    from pw.dex import get_dex

    dex = get_dex()
    cases = {
        "flamethrower": "灼伤",        # secondary.status
        "tailwhip": "防御 -1",         # boosts
        "doublekick": "连续攻击",       # multihit
        "gigadrain": "回复造成伤害",     # drain
        "bravebird": "反作用伤害",      # recoil
        "sunnyday": "大晴天",          # weather
        "reflect": "反射壁",           # sideCondition
        "quickattack": "优先度 +1",     # priority
        "selfdestruct": "自身倒下",      # selfdestruct
        "recover": "回复自身最大 HP",    # heal
        "protect": "自身",             # volatileStatus(给自己上的)
    }
    for key, want in cases.items():
        got = dex.move_effect_text(key)
        assert want in got, f"{key} 的效果文本缺少「{want}」:{got}"
    # 占位说明("无法使用这个招式")不能当效果显示
    assert "无法使用" not in dex.move_short_desc("barrage")
    assert "无法使用" not in dex.move_short_desc("flameburst")
    # 丢了 secondary 的招式要退回 desc 里的效果句(火焰牙的畏缩/灼伤)
    assert "灼伤" in dex.move_short_desc("firefang")


def test_mon_detail_hint_lists_move_effects():
    """`/宝可梦` 图片只能画招式名,效果要跟在消息文本里。"""
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        t = p._load(ev)
        t.data["party"][0]["moves"] = ["flamethrower", "tailwhip"]
        t.data["party"][0]["pp"] = {"flamethrower": 15, "tailwhip": 30}
        p._save(t)
        payload = p._mon_payload(p._load(ev), p._load(ev).party[0], index=1, party_size=1)
        hint = p._mon_hint(payload)
        assert "◆ 招式" in hint
        assert "喷射火焰" in hint and "灼伤" in hint
        assert "摇尾巴" in hint and "防御 -1" in hint
        assert "/招式 <序号>" in hint


# ══════════════════════════════════════════════════════════════════
# 背包 / 商店:分页 + 说明框跟着选中项
# ══════════════════════════════════════════════════════════════════
def _drawn_texts(monkeypatch, fn, *a, **kw) -> list[str]:
    """把 Screen.text 画过的字符串全记下来。

    界面渲染内部自己 new Screen,外面拿不到;spy 一层就能断言"到底画了什么字",
    比对比像素稳得多。
    """
    from pw import ui_menu as UIM
    from pw import ui_render as UI

    seen: list[str] = []
    for cls in (UI.Screen, getattr(UIM, "Screen", UI.Screen)):
        orig = cls.text

        def spy(self, x, y, s, _orig=orig, **k):
            seen.append(str(s))
            return _orig(self, x, y, s, **k)

        monkeypatch.setattr(cls, "text", spy, raising=False)
    fn(*a, **kw)
    return seen


def _many_items(n=14):
    keys = ["potion", "super-potion", "antidote", "revive", "poke-ball",
            "great-ball", "full-heal", "fire-stone", "pp-up", "x-attack",
            "max-potion", "ether", "escape-rope", "rare-candy"]
    from pw.items import BAG_ITEMS, effect_text

    out = []
    for i in range(min(n, len(keys))):
        k = keys[i]
        e = BAG_ITEMS.get(k) or {}
        out.append({"key": k, "zh": e.get("zh") or k, "count": i + 1,
                    "desc": e.get("desc") or "", "effect": effect_text(k),
                    "kind": e.get("kind") or "", "price": 100 * (i + 1)})
    return out


def test_parse_page_args():
    from test_commands import _MOD as _PKG

    mod = sys.modules.get("pw_plugin.main", _PKG)
    parse = mod._parse_page_args
    assert parse("") == ("", 0, 0)
    assert parse("12") == ("", 12, 0)
    assert parse("页 3") == ("", 0, 3)
    assert parse("第 3 页") == ("", 0, 3)          # 尾巴那个"页"不能当成口袋名
    assert parse("3页") == ("", 0, 3)
    assert parse("道具 4") == ("道具", 4, 0)
    assert parse("精灵球 页 2") == ("精灵球", 0, 2)
    assert parse("page 2") == ("", 0, 2)
    # 页码写法可以当页码用(numeric_is_page)
    assert parse("2", numeric_is_page=True) == ("", 0, 2)


def test_bag_selection_and_paging():
    """`selected` 要能定位到"第 N 件 / 第 N 页",而不是永远第 0 件。"""
    from pw import ui_render as UI

    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        t = p._load(ev)
        t.data["bag"] = {"potion": 3, "super-potion": 2, "antidote": 5, "revive": 1,
                         "poke-ball": 12, "great-ball": 4, "full-heal": 1,
                         "fire-stone": 2, "pp-up": 1, "x-attack": 2, "max-potion": 1,
                         "ether": 2}
        p._save(t)
        per = UI.BAG_PER_PAGE
        # 用**实际**条目数算期望:每个口袋装哪些 kind 由 KIND_TO_POCKET 决定,
        # 手写数字容易被自己坑(道具口袋其实只有 3 件)。
        n = len(p._bag_payload(t, "道具")["items"])
        assert n > 0
        # 不指定 → 第 0 件
        assert p._bag_payload(t, "道具")["selected"] == 0
        # 指定第 2 件
        assert p._bag_payload(t, "道具", index=2)["selected"] == 1
        # 指定第 2 页 → 该页第一件(够长时);够长时等于 per
        if n > per:
            assert p._bag_payload(t, "道具", page=2)["selected"] == per
        # 越界的序号/页码钳到最后一件,而不是报错或越界
        assert p._bag_payload(t, "道具", index=999)["selected"] == n - 1
        assert p._bag_payload(t, "道具", page=99)["selected"] == n - 1
        # 口袋名照旧可用,且精灵球口袋确实收着球
        balls = p._bag_payload(t, "精灵球")
        assert balls["pocket"] == "balls"
        assert any(x["key"] in ("poke-ball", "great-ball") for x in balls["items"])


def test_bag_desc_box_follows_the_selected_item(monkeypatch):
    """说明框必须写**选中那一件**的说明(用户:只能看到第一个 item 的说明)。"""
    from pw import ui_render as UI

    items = _many_items(14)
    # 选中第 12 件(第 3 页)
    texts = _drawn_texts(monkeypatch, UI.render_bag, items, money=3000,
                         active_pocket="items", selected=11, scale=1)
    blob = "\n".join(texts)
    sel_item = items[11]
    assert str(sel_item["zh"]) in blob, blob[:300]
    assert str(sel_item["desc"])[:10] in blob, f"说明框没写选中项的说明:\n{blob[-300:]}"
    # 页码提示也要画出来
    assert any("页" in x and "/共" in x for x in texts), texts[-6:]
    # 第 0 件的说明不该出现在说明框里(它在列表里只有名字)
    first_desc = str(items[0]["desc"])[:10]
    assert first_desc not in blob or first_desc in str(sel_item["desc"])


def test_shop_desc_box_follows_the_selected_item(monkeypatch):
    """满徽章时货架 80+ 种,说明框要跟着选中的那件走(以前永远第 0 件)。"""
    from pw import ui_menu as UIM
    from pw.items import effect_text

    entries = []
    for i, it in enumerate(_many_items(14)):
        entries.append({**it, "price": 100 * (i + 1),
                        "effect": effect_text(it["key"])})
    texts = _drawn_texts(monkeypatch, UIM.render_shop, entries, money=3000,
                         location_zh="深灰市", selected=13, scale=1)
    blob = "\n".join(texts)
    assert str(entries[13]["zh"]) in blob
    assert str(entries[13]["desc"])[:10] in blob, f"没写选中商品的说明:\n{blob[-300:]}"
    assert any("/共" in x for x in texts), texts[-6:]


def test_shop_buy_sell_still_works_with_numbers():
    """`/商店 买 伤药 2` 里的数字不能被当成"看第 2 件"。"""
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        t = p._load(ev)
        t.data["location"] = "pewter-city"      # 有商店的城镇
        t.data["bag"] = {"potion": 3}
        p._save(t)
        # 冻结"今天已滚动":否则世界事件(捡到钱包 +800₽)会让金钱断言随机失败
        from pw.util import game_day

        st = p._state("g10086")
        st.data["day"] = game_day()
        st.data["last_roll_day"] = game_day()
        st.data["player_events"] = {}
        p._save_state(st)
        money0 = p._load(ev).money
        out = _run(p, "/商店 买 伤药 2", "cmd_shop")
        t2 = p._load(ev)
        assert "买下" in out, out[:150]
        assert t2.count("potion") == 5, t2.count("potion")
        assert t2.money < money0
        out = _run(p, "/商店 卖 伤药 1", "cmd_shop")
        assert "卖" in out and p._load(ev).count("potion") == 4


def test_item_effect_text_is_human_readable():
    from pw.items import effect_text

    cases = {
        "potion": "回复 20 HP",
        "max-potion": "完全回复 HP",
        "antidote": "治愈中毒",
        "full-heal": "所有异常状态",
        "revive": "复活并回复 50% HP",
        "great-ball": "捕获率 ×1.5",
        "master-ball": "必定捕获",
        "ether": "回复 10 点 PP",
        "fire-stone": "进化",
        "x-attack": "提升 攻击",
        "rare-candy": "提升 1 级",
        "leftovers": "每回合回复",
    }
    for key, want in cases.items():
        got = effect_text(key)
        assert want in got, f"{key} 的效果文本缺少「{want}」:{got}"
    # 基础捕获率不该写成"捕获率 ×1"
    assert "捕获率" not in effect_text("poke-ball")
    # 引擎里没有消费方的效果要如实标出来
    for key in ("pp-up", "ability-capsule"):
        assert "暂未实现" in effect_text(key), key


def test_bag_text_fallback_lists_effects():
    """图片不可用时,文本回退也要带上效果(不能只有名字)。"""
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        t = p._load(ev)
        t.data["bag"] = {"potion": 3, "great-ball": 2, "pp-up": 1}
        p._save(t)
        out = _run(p, "/背包", "cmd_bag")
        assert "回复 20 HP" in out
        assert "捕获率 ×1.5" in out
        assert "暂未实现" in out


# ══════════════════════════════════════════════════════════════════
# /持有 —— 让宝可梦携带道具(此前 mon.item 只被读取,永远拿不到)
# ══════════════════════════════════════════════════════════════════
def _hold_setup(p, ev, species="onix", item="", bag=None, level=40):
    from pw.engine import create_pokemon

    t = p._load(ev)
    t.data["location"] = "pewter-city"
    mon = create_pokemon(species, level).to_dict()
    mon["id"] = "mH"
    mon["item"] = item
    t.data["party"] = [t.party[0], mon]
    t.data["bag"] = dict(bag or {})
    p._save(t)


def test_hold_equip_and_take_off():
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        _hold_setup(p, ev, bag={"leftovers": 1, "potion": 2})
        out = _run(p, "/持有", "cmd_hold")
        assert "携带情况" in out and "持有:" in out

        out = _run(p, "/持有 吃剩的东西 2", "cmd_hold")
        t = p._load(ev)
        assert "开始携带" in out and t.party[1]["item"] == "leftovers"
        assert t.count("leftovers") == 0, "装上去要真的从背包里扣掉"
        # 已携带时不重复装
        assert "已经携带" in _run(p, "/持有 吃剩的东西 2", "cmd_hold")
        # 换别的道具要先取下
        t.data["bag"]["metal-coat"] = 1
        p._save(t)
        assert "先 `/持有 取下" in _run(p, "/持有 金属膜 2", "cmd_hold")
        # 取下要放回背包
        out = _run(p, "/持有 取下 2", "cmd_hold")
        t = p._load(ev)
        assert "取下" in out and not t.party[1]["item"] and t.count("leftovers") == 1
        # 俗称也能解析
        assert "开始携带" in _run(p, "/持有 剩饭 2", "cmd_hold")


def test_hold_rejects_consumables_and_wrong_args():
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        _hold_setup(p, ev, bag={"potion": 2})
        assert "是消耗品" in _run(p, "/持有 伤药 2", "cmd_hold")
        assert "没有找到道具" in _run(p, "/持有 不存在的东西 2", "cmd_hold")
        t = p._load(ev)
        t.data["bag"]["metal-coat"] = 1
        p._save(t)
        assert "没有第 9 只" in _run(p, "/持有 金属膜 9", "cmd_hold")
        # 背包里没有(在别人身上)要提示
        _hold_setup(p, ev, item="leftovers", bag={})
        out = _run(p, "/持有 吃剩的东西 1", "cmd_hold")
        assert "背包里没有" in out


def test_levelHold_evolution_becomes_reachable():
    """携带升级进化(浑圆之石/锋锐之爪…)此前不可达,现在装上去就能进化。"""
    from pw import growth

    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        # 狃拉 + 锋锐之爪 + 夜晚 → 玛狃拉(evoType=levelHold)
        _hold_setup(p, ev, species="sneasel", bag={"razor-claw": 1}, level=40)
        assert "开始携带" in _run(p, "/持有 锋锐之爪 2", "cmd_hold")
        mon = B.dict_to_mon(p._load(ev).party[1])
        assert growth.auto_evolve(mon, daytime="night") == "weavile", "带着锋锐之爪在夜晚升级应进化成玛狃拉"
        # 不带道具就不该进化
        _hold_setup(p, ev, species="sneasel", bag={}, level=40)
        mon2 = B.dict_to_mon(p._load(ev).party[1])
        assert growth.auto_evolve(mon2, daytime="night") == ""
        # 带了道具但时机不对(白天)也不该进化
        _hold_setup(p, ev, species="sneasel", item="razor-claw", level=40)
        mon3 = B.dict_to_mon(p._load(ev).party[1])
        assert growth.auto_evolve(mon3, daytime="day") == ""


def test_trade_evolution_requires_and_consumes_held_item():
    """需要携带道具的 16 种通信进化:空手不能进化,带了要消耗掉。"""
    from pw.dex import get_dex

    dex = get_dex()
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        # 空手 → 拒绝,并说清要带什么
        _hold_setup(p, ev, species="onix", bag={"metal-coat": 1})
        out = _run(p, "/交换 2", "cmd_trade")
        assert "金属膜" in out and "大钢蛇" not in out, out
        # 装上再交换 → 进化 + 道具被消耗
        assert "开始携带" in _run(p, "/持有 金属膜 2", "cmd_hold")
        out = _run(p, "/交换 2", "cmd_trade")
        t = p._load(ev)
        assert dex.species[t.party[1]["species"]]["zh"] == "大钢蛇", out
        assert not t.party[1]["item"], "通信进化要消耗携带道具"


def test_trade_evolution_branches_follow_the_held_item():
    """珍珠贝两条分支必须按携带道具走(以前永远只给猎斑鱼)。"""
    from pw.dex import get_dex

    dex = get_dex()
    for item, want in (("deep-sea-tooth", "猎斑鱼"), ("deep-sea-scale", "樱花鱼")):
        with tempfile.TemporaryDirectory() as tmp:
            p, ev = _start(tmp)
            _hold_setup(p, ev, species="clamperl", item=item, level=40)
            out = _run(p, "/交换 2", "cmd_trade")
            t = p._load(ev)
            got = dex.species[t.party[1]["species"]]["zh"]
            assert got == want, f"带 {item} 应进化成 {want},实际 {got}({out})"


def test_plain_trade_evolution_still_works_without_item():
    from pw.dex import get_dex

    dex = get_dex()
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        for species, want in (("kadabra", "胡地"), ("haunter", "耿鬼"),
                              ("machoke", "怪力"), ("graveler", "隆隆岩")):
            _hold_setup(p, ev, species=species, level=40)
            out = _run(p, "/交换 2", "cmd_trade")
            t = p._load(ev)
            assert dex.species[t.party[1]["species"]]["zh"] == want, out


def test_trade_requires_pokemon_center():
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        _hold_setup(p, ev, species="kadabra")
        t = p._load(ev)
        t.data["location"] = "kanto-route-1"      # 野外没有宝可梦中心
        p._save(t)
        out = _run(p, "/交换 2", "cmd_trade")
        assert "宝可梦中心" in out, out


def test_dubious_disc_exists_and_is_buyable():
    """多边兽乙型需要的可疑补丁以前在数据里根本不存在。"""
    from pw.items import BAG_ITEMS, effect_text
    from pw.world import WorldMap

    assert "dubious-disc" in BAG_ITEMS
    assert effect_text("dubious-disc")
    stock: list[str] = []
    world = WorldMap()
    for badge in range(9):
        stock += world.shop_stock("pewter-city", badge)
    assert "dubious-disc" in stock, "满徽章商店都买不到可疑补丁"


def test_every_visible_bag_row_shows_its_own_effect(monkeypatch):
    """用户:背包/商店"只能看见第一个物品的说明"。

    说明框跟着选中项只是第一步 —— 默认选中的仍是第 0 件,玩家什么都不敲就只看到
    第一件的说明。现在**每一行都画出自己的效果**,所以看得到的每件道具都有说明。
    """
    from pw import ui_render as UI

    items = _many_items(12)
    texts = _drawn_texts(monkeypatch, UI.render_bag, items, money=3000,
                         active_pocket="items", selected=0, scale=1)
    blob = "\n".join(texts)
    # 第 1 页可见的 4 件,每件的效果都要画出来(而不只是第 0 件)
    for it in items[:UI.BAG_PER_PAGE]:
        eff = str(it.get("effect") or it.get("desc") or "")
        if not eff:
            continue
        assert any(eff[:8] in t for t in texts), (
            f"{it['zh']} 这一行没有画出效果(只画了第 0 件?):\n{blob[:400]}"
        )


def test_every_visible_shop_row_shows_its_own_effect(monkeypatch):
    from pw import ui_menu as UIM

    entries = _many_items(12)
    for i, it in enumerate(entries):
        it["price"] = 100 * (i + 1)
    texts = _drawn_texts(monkeypatch, UIM.render_shop, entries, money=3000,
                         location_zh="深灰市", selected=0, scale=1)
    for it in entries[:UIM.SHOP_PER_PAGE]:
        eff = str(it.get("effect") or it.get("desc") or "")
        if not eff:
            continue
        assert any(eff[:8] in t for t in texts), (
            f"{it['zh']} 这一行没有画出效果:\n{chr(10).join(texts)[:400]}"
        )


def test_bag_payload_carries_structured_effect():
    """行内展示要的是**结构化效果**,而不是只有一句 flavor 说明。"""
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _start(tmp)
        t = p._load(ev)
        t.data["bag"] = {"potion": 1, "great-ball": 1, "x-attack": 1}
        p._save(t)
        rows = {x["key"]: x for x in p._bag_payload(t, "回复")["items"]}
        assert "回复 20 HP" in rows["potion"]["effect"]
        rows = {x["key"]: x for x in p._bag_payload(t, "精灵球")["items"]}
        assert "捕获率" in rows["great-ball"]["effect"]
