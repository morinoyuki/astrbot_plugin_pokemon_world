"""闪光(异色)宝可梦回归:概率抽取、持久化、展示与捕获记录。

闪光必须是**稀有变异体**:野生遭遇按概率出、配色来自官方异色图
(PokeAPI `shiny/`),捕获后永久保留(存档往返不丢),并且在战斗画面、
队伍/仓库/资料页、捕获卡与图鉴里都看得出来。
"""

from __future__ import annotations

import os
import random
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402

from pw import battle as B  # noqa: E402
from pw.engine import Pokemon, create_pokemon, start_battle  # noqa: E402
from pw.player import Trainer, mon_to_dict  # noqa: E402


def _trainer(*, shiny=False, species="heracross", moves=("tackle",)):
    mon = create_pokemon(species, 30, moves=list(moves), shiny=shiny)
    data = {"party": [mon_to_dict(mon)], "uid": "u1", "scope": "s1"}
    return Trainer(data, uid="u1", scope="s1")


def _wild_hit(shiny=True):
    return {
        "species": "pikachu",
        "zh": "皮卡丘",
        "level": 5,
        "types": ["Electric"],
        "methods": ["walk"],
        "shiny": bool(shiny),
    }


# ══════════════════════════════════════════════════════════════════
# 数据与持久化
# ══════════════════════════════════════════════════════════════════
def test_create_pokemon_shiny_default_off_and_roundtrip():
    normal = create_pokemon("pikachu", 10)
    assert normal.shiny is False, "普通个体不能默认闪光"
    shiny = create_pokemon("pikachu", 10, shiny=True)
    assert shiny.shiny is True
    # 闪光不影响数值与招式
    assert shiny.stats == normal.stats
    assert shiny.moves == normal.moves

    d = shiny.to_dict()
    assert d["shiny"] is True
    assert Pokemon.from_dict(d).shiny is True
    # 老存档没有 shiny 字段 → 按非闪光读(兼容旧档,不能报错)
    old = dict(d)
    old.pop("shiny")
    assert Pokemon.from_dict(old).shiny is False
    # 训练家存档往返(写回 → 读档)
    t = Trainer({"party": [mon_to_dict(shiny)], "uid": "u1", "scope": "s1"},
                uid="u1", scope="s1")
    again = Trainer(t.data, uid="u1", scope="s1")
    assert again.party[0]["shiny"] is True


def test_roll_wild_shiny_rate_and_determinism():
    t = _trainer()
    t.data["location"] = "kanto-route-1"
    base = B.roll_wild(t, rng=random.Random(7), shiny_rate=0)
    always = B.roll_wild(t, rng=random.Random(7), shiny_rate=1)
    assert base["shiny"] is False and always["shiny"] is True
    # 闪光掷骰在物种/等级确定之后:开不开闪光不影响原结果
    assert (base["species"], base["level"]) == (always["species"], always["level"])
    # 同种子 → 同结果(确定性)
    a = B.roll_wild(t, rng=random.Random(11), shiny_rate=512)
    b = B.roll_wild(t, rng=random.Random(11), shiny_rate=512)
    assert a["shiny"] == b["shiny"] and a["species"] == b["species"]


def test_shiny_sprite_paths_and_fallback(monkeypatch):
    from pw import sprites

    assert sprites.shiny_available_count() > 1000, "闪光精灵图没构建(跑 build_shiny_sprites.py)"
    path = sprites.sprite_path("pikachu", shiny=True)
    assert "sprites_shiny" in path and os.path.exists(path)
    back = sprites.back_sprite_path("pikachu", shiny=True)
    assert "sprites_shiny_back" in back and os.path.exists(back)
    # 闪光图缺失时必须回退普通图,绝不能把宝可梦画没
    monkeypatch.setattr(sprites, "_available_shiny", lambda: frozenset())
    monkeypatch.setattr(sprites, "_available_shiny_back", lambda: frozenset())
    assert sprites.sprite_path("pikachu", shiny=True) == sprites.sprite_path("pikachu")
    assert sprites.sprite_path("pikachu", shiny=True) != ""


# ══════════════════════════════════════════════════════════════════
# 战斗与捕获
# ══════════════════════════════════════════════════════════════════
def test_shiny_marks_in_team_status_view_and_status_text():
    t = _trainer(shiny=True)
    assert "✨" in B.team_status(t)
    B.start(t, [{"species": "pikachu", "level": 5, "shiny": True}],
            kind="wild", wild=True,
            meta={"kind": "wild", "title": "✨ 野生的皮卡丘(闪光!)"}, day=1)
    view = B.view(t)
    assert view["foe"]["shiny"] is True
    assert view["foe"]["name"].startswith("✨")
    assert "✨" in B.status_text(t)
    # 非闪光没有标记
    t2 = _trainer(shiny=False)
    B.start(t2, [{"species": "pikachu", "level": 5}], kind="wild", wild=True, day=1)
    assert B.view(t2)["foe"]["shiny"] is False
    assert not B.view(t2)["foe"]["name"].startswith("✨")


def test_catching_shiny_records_dex_and_announces():
    t = _trainer()
    mon = create_pokemon("pikachu", 5, shiny=True)
    battle = start_battle([create_pokemon("snorlax", 50)], [mon], wild=True)
    battle.captured = mon.to_dict()
    res = B.TurnResult()
    B._finish_catch(t, battle, res)
    assert t.shiny_caught("pikachu") is True
    assert t.shiny_count() == 1
    assert any("闪光" in line for line in res.rewards), res.rewards
    # 普通捕获不进闪光图鉴
    t2 = _trainer()
    plain = create_pokemon("pikachu", 5)
    battle2 = start_battle([create_pokemon("snorlax", 50)], [plain], wild=True)
    battle2.captured = plain.to_dict()
    res2 = B.TurnResult()
    B._finish_catch(t2, battle2, res2)
    assert t2.shiny_count() == 0
    assert not any("闪光" in line for line in res2.rewards)


def test_legend_shiny_is_deterministic_per_day():
    from pw import legendary

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"shiny_rate": 1}
        t = _trainer()
        site = {"species": "mewtwo", "zh": "超梦", "level": 70,
                "location": "kanto-route-1", "region": "kanto"}
        notice: list[str] = []
        meta = p._maybe_shiny_legend(
            t, site, legendary.legendary_meta(t, site), 3, notice
        )
        assert meta["shiny"] is True
        assert meta["team"][0]["shiny"] is True
        assert notice and "闪光" in notice[0]
        # 同一天再掷结果一致(不能靠反复逃跑刷闪)
        meta2 = p._maybe_shiny_legend(
            t, site, legendary.legendary_meta(t, site), 3, []
        )
        assert meta2["shiny"] is True
        # 关闭闪光后绝不出现
        p.config = {"shiny_rate": 0}
        meta3 = p._maybe_shiny_legend(
            t, site, legendary.legendary_meta(t, site), 3, []
        )
        assert "shiny" not in meta3


# ══════════════════════════════════════════════════════════════════
# 渲染:闪光配色真的画进图里
# ══════════════════════════════════════════════════════════════════
def test_battle_and_ui_images_use_shiny_sprite():
    from pw import battle_render as BR
    from pw import ui_info as UII
    from pw import ui_render as UI

    shiny = {"species": "pikachu", "name": "✨皮卡丘", "shiny": True,
             "level": 10, "cur_hp": 20, "max_hp": 30}
    plain = {**shiny, "name": "皮卡丘", "shiny": False}
    a = BR.render_battle(shiny, plain, ["测试"], scale=2)
    b = BR.render_battle(plain, plain, ["测试"], scale=2)
    assert a.startswith(b"\x89PNG") and b.startswith(b"\x89PNG")
    assert a != b, "闪光与普通的对战画面一模一样(精灵图没换)"
    gotcha = UII.render_gotcha(shiny, scale=2)
    assert gotcha.startswith(b"\x89PNG")
    assert gotcha != UII.render_gotcha(plain, scale=2)
    assert UI.render_party([shiny], scale=2).startswith(b"\x89PNG")
    assert UI.render_party([shiny], scale=2) != UI.render_party([plain], scale=2)
    assert UI.render_box([shiny], scale=2).startswith(b"\x89PNG")
    assert UI.render_mon_summary(
        {**shiny, "types": ["Electric"], "moves": []}, scale=2
    ).startswith(b"\x89PNG")


# ══════════════════════════════════════════════════════════════════
# 指令层:探索遇到闪光的提示 + 队伍/图鉴可见
# ══════════════════════════════════════════════════════════════════
def test_explore_wild_shiny_shows_marker(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "quest_enable": False}
        run_cmd(p, _Event("/开始 小智 新叶喵"), p.cmd_start)
        # main.py 通过包内相对导入使用 `pw_plugin.pw.battle`,与测试导入的
        # `pw.battle` 不是同一个模块对象 —— 必须补丁到插件真正调用的那个。
        plugin_battle = sys.modules["pw_plugin.pw.battle"]
        monkeypatch.setattr(plugin_battle, "roll_wild",
                            lambda *a, **kw: _wild_hit(True))
        found = ""
        # 默认探索是掷骰决定的(野生/训练家/道具),换 steps 多试几轮命中野生分支
        for steps in range(0, 1600, 40):
            t = p._load(_Event())
            t.data.pop("battle", None)
            t.data["steps"] = steps
            p._save(t)
            ev = _Event("/探索 野生")
            run_cmd(p, ev, p.cmd_explore)
            out = "\n".join(ev.outputs)
            if "闪光" in out:
                found = out
                break
        assert found, "1600 步内没触发野生遭遇"
        assert "✨" in found
        assert "皮卡丘" in found


def test_party_dex_and_mon_page_show_shiny():
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "quest_enable": False}
        run_cmd(p, _Event("/开始 小智 新叶喵"), p.cmd_start)
        t = p._load(_Event())
        t.party[0] = mon_to_dict(
            create_pokemon("pikachu", 10, moves=["tackle"], shiny=True)
        )
        t.mark_caught("pikachu")
        t.mark_shiny("pikachu")
        p._save(t)

        ev = _Event("/队伍")
        run_cmd(p, ev, p.cmd_team)
        assert "✨" in "\n".join(ev.outputs)

        ev = _Event("/宝可梦 1")
        run_cmd(p, ev, p.cmd_mon)
        assert "✨" in "\n".join(ev.outputs)

        ev = _Event("/图鉴")
        run_cmd(p, ev, p.cmd_dex)
        assert "闪光" in "\n".join(ev.outputs)

        ev = _Event("/图鉴 皮卡丘")
        run_cmd(p, ev, p.cmd_dex)
        assert "闪光" in "\n".join(ev.outputs)
