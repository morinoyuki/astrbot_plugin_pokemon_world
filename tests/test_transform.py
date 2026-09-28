"""百变怪的「变身」:拟态对手的样子、招式与能力。

之前引擎里根本没实现变身 —— 百变怪只有这一招,放出来只有一行
「百变怪 使用了 变身!」,既没换样子也没换招式,等于白板。
这里锁住正作规则:复制物种/特性/数值/招式(PP 统一 5)/能力等级,
HP 与等级保留;换下场或战斗结束还原成百变怪(存档里必须是 Ditto)。
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
from pw.engine import Battle, Side, create_pokemon  # noqa: E402
from pw.util import game_day  # noqa: E402


def _duel(mon, foe, *, wild=True) -> Battle:
    b = Battle(player=Side("player", [mon]), enemy=Side("enemy", [foe]), wild=wild)
    b.start()
    return b


def test_ditto_transform_copies_species_moves_stats_and_ability():
    ditto = create_pokemon("ditto", 20)
    foe = create_pokemon("pikachu", 20)
    foe.stages = {"atk": 2}
    own_max = ditto.max_hp
    ditto.cur_hp = 30                              # 带伤变身:HP 不随新种族值走

    assert ditto.transform_into(foe) is True

    assert ditto.species == "pikachu"
    assert ditto.display == foe.entry.get("zh")
    assert ditto.moves == list(foe.moves)
    assert set(ditto.pp) == set(foe.moves)
    assert set(ditto.pp.values()) == {5}          # 正作:变身招式 PP 一律 5
    assert ditto.ability == foe.ability
    assert ditto.stats == foe.stats               # 数值照抄
    assert ditto.stages == {"atk": 2}             # 能力等级也复制
    assert ditto.level == 20                      # 自己的等级不变
    assert (ditto.max_hp, ditto.cur_hp) == (own_max, 30)   # HP 不变
    assert ditto.types == foe.types

    # 战斗里走一遍:日志要报出来,变身之后真的能用复制来的招式
    b = _duel(create_pokemon("ditto", 20), create_pokemon("pikachu", 20))
    b.step({"type": "move", "move": "transform"})
    assert any("变身成了" in line for line in b.log), b.log
    b.log = []
    b.step({"type": "move", "move": "spark"})
    assert any("使用了 电光" in line for line in b.log), b.log


def test_transform_can_only_happen_once_and_only_when_it_makes_sense():
    ditto = create_pokemon("ditto", 20)
    b = _duel(ditto, create_pokemon("pikachu", 20))
    b.step({"type": "move", "move": "transform"})
    b.log = []
    b.step({"type": "move", "move": "spark"})
    # 变身状态下招式表已被替换,自己那本「变身」已经不在手上了
    assert "transform" not in ditto.moves
    assert ditto.transform_backup, "变身状态丢失了"

    # 已经变身过 → transform_into 直接拒绝
    assert ditto.transform_into(create_pokemon("geodude", 20)) is False

    # 对手已倒下 / 自己已倒下也不能变
    fresh = create_pokemon("ditto", 20)
    dead = create_pokemon("pikachu", 20)
    dead.cur_hp = 0
    dead.fainted = True
    assert fresh.transform_into(dead) is False


def test_transform_ends_when_switched_out():
    ditto = create_pokemon("ditto", 20)
    buddy = create_pokemon("geodude", 20)
    b = Battle(player=Side("player", [ditto, buddy]), enemy=Side("enemy", [create_pokemon("pikachu", 20)]))
    b.start()
    b.step({"type": "move", "move": "transform"})
    assert ditto.species == "pikachu"
    b.step({"type": "switch", "index": 1})
    assert ditto.species == "ditto", "换下场后应该变回百变怪"
    assert not ditto.transform_backup
    assert list(ditto.pp) == ["transform"], "换下场后要拿回自己的招式表"


def test_transform_never_leaks_into_the_save():
    """写回存档要还原,但**不能动战斗中的那只**(每回合都会存档)。"""
    ditto = create_pokemon("ditto", 20)
    b = _duel(ditto, create_pokemon("pikachu", 20))
    b.step({"type": "move", "move": "transform"})

    saved = ditto.to_storage_dict()
    assert saved["species"] == "ditto"
    assert saved["moves"] == ["transform"]
    assert saved["transform_backup"] == {}
    assert saved["stats"] == create_pokemon("ditto", 20).stats
    # 关键:序列化不能就地还原,否则下一回合就“变身没了”
    assert ditto.species == "pikachu"
    assert ditto.transform_backup


def test_transformed_mon_renders_as_the_copied_species():
    ditto = create_pokemon("ditto", 20)
    b = _duel(ditto, create_pokemon("pikachu", 20))
    b.step({"type": "move", "move": "transform"})
    view = B._mon_view(ditto)
    assert view["species"] == "pikachu"
    assert view["name"] == ditto.entry.get("zh")
    assert view["types"] == ["电"]
    # 变身中的经验条按百变怪自己的成长曲线算(经验属于它自己)
    assert 0 <= B.exp_progress(ditto) <= 100


def test_imposter_ability_transforms_on_switch_in():
    """百变怪的隐藏特性「变身者」:上场即变身(正作行为)。"""
    ditto = create_pokemon("ditto", 20)
    ditto.ability = "imposter"
    b = _duel(create_pokemon("pikachu", 20), ditto)   # 百变怪在对面
    assert b.enemy.party[0].species == "pikachu"
    assert b.enemy.party[0].moves != ["transform"]
    assert any("变身者" in line for line in b.log), b.log


def test_battle_flow_transform_command_and_catch():
    """端到端:指令流里变身 → 招式提示换成复制来的招 → 捕获记录仍是百变怪。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False}
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        t = p._load(_Event())
        # 我方出战皮卡丘(招式里没有自爆这种同归于尽的招,方便后续捕获)
        t.data["party"][0] = create_pokemon("pikachu", 25).to_dict()
        t.add_item("master-ball", 1)
        p._save(t)
        t = p._load(_Event())
        B.start(t, [{"species": "ditto", "level": 20, "shiny": False}], kind="wild",
                wild=True, meta={"title": "野生的百变怪"}, day=game_day())
        p._save(t)

        ev = _Event("/对战 1")
        run_cmd(p, ev, p.cmd_battle)
        out = ev.outputs[0]
        assert "变身" in out, out
        t = p._load(_Event())
        foe = B.session(t)["battle"]["enemy"]["party"][0]
        assert foe["species"] == "pikachu" and foe["transform_backup"], foe

        # 复制来的招式就该是玩家皮卡丘的招式表
        assert set(foe["moves"]) == set(create_pokemon("pikachu", 25).moves), foe["moves"]

        ev2 = _Event("/捕捉 大师球")
        run_cmd(p, ev2, p.cmd_catch)
        t = p._load(_Event())
        saved = p.trainers.load(t.scope, t.uid)
        assert "ditto" in [m["species"] for m in saved["party"]], saved["party"]
        caught = next(m for m in saved["party"] if m["species"] == "ditto")
        assert caught["moves"] == ["transform"], "捕到的百变怪不能带着拟态后的招式"
        assert "ditto" in (t.data.get("dex_caught") or [])
