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
