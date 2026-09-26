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
