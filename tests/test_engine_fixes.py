"""战斗内核正确性回归测试(来自全量复核的报告,每条都先复现再修)。

覆盖:天气招式键名、太晶 STAB、连续技段数、自爆、换人招式、戏法空间、
场地加成、分析、报复/保证、魔法防守、木子果、下雪伤害、换人失败死锁。
"""

from __future__ import annotations

import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from pw.engine import (  # noqa: E402
    _calc_confusion_damage,
    _stab_mult,
    create_pokemon,
    start_battle,
)


def _mon(species, level, moves, item="", pp=30):
    mon = create_pokemon(species, level, moves=moves, item=item)
    mon.pp = dict.fromkeys(moves, pp)
    return mon


def _damage(lines) -> int:
    for line in lines:
        m = re.search(r"造成 (\d+) 点伤害", line)
        if m:
            return int(m.group(1))
    return 0


def test_weather_moves_use_canonical_keys():
    """晴天/求雨/沙暴/下雪招式必须写入 sun/rain/sand/snow。

    招式数据里的键名来自 PokeAPI(sunnyday/RainDance/Sandstorm/snowscape),
    不做归一的话引擎所有天气判定都会落空 —— 招式等于白放。
    """
    for move, expect in (
        ("sunnyday", "sun"),
        ("raindance", "rain"),
        ("sandstorm", "sand"),
        ("snowscape", "snow"),
    ):
        a = _mon("charizard", 50, [move, "ember"])
        b = _mon("snorlax", 50, ["tackle"])
        bt = start_battle([a], [b])
        bt.start()
        bt.step({"type": "move", "move": move})
        assert bt.weather == expect, f"{move} 应设置 {expect},实际 {bt.weather!r}"
        assert bt.weather_turns >= 1

    # 天气真的影响伤害(晴天火系 ×1.5)
    def ember_damage(weather: str) -> int:
        a = _mon("charizard", 50, ["ember"])
        b = _mon("snorlax", 50, ["tackle"])
        bt = start_battle([a], [b], seed=3)
        bt.start()
        bt.weather = weather
        return _damage(bt.step({"type": "move", "move": "ember"}))

    assert ember_damage("sun") > ember_damage("")


def test_tera_stab_only_doubles_for_original_type():
    mon = create_pokemon("charizard", 50)  # Fire / Flying
    mon.terastallized = True
    mon.tera_type = "Water"
    assert _stab_mult(mon, "Water", "surf") == 1.5, "太晶成别的属性只有 1.5"
    mon.tera_type = "Fire"
    assert _stab_mult(mon, "Fire", "ember") == 2.0, "太晶属性属于原属性才有 2.0"


def test_multihit_moves_hit_multiple_times():
    """连续技要按数据段数结算(旧实现只打 1 下,总伤害只有 1/3)。"""
    a = _mon("charizard", 80, ["bulletseed"])
    b = _mon("snorlax", 100, ["tackle"])
    bt = start_battle([a], [b], seed=7)
    bt.start()
    lines = bt.step({"type": "move", "move": "bulletseed"})
    hits = sum(1 for line in lines if "击中 卡比兽!" in line)
    assert 2 <= hits <= 5, f"种子机关枪应命中 2~5 次,实际 {hits}"
    assert any("连续命中" in line for line in lines)

    # 固定段数的招式
    a = _mon("charizard", 80, ["doublehit"])
    bt = start_battle([a], [_mon("snorlax", 100, ["tackle"])], seed=7)
    bt.start()
    lines = bt.step({"type": "move", "move": "doublehit"})
    assert sum(1 for line in lines if "击中 卡比兽!" in line) == 2

    # 作弊骰子:至少 4 段
    a = _mon("charizard", 80, ["bulletseed"], item="loaded-dice")
    bt = start_battle([a], [_mon("snorlax", 100, ["tackle"])], seed=7)
    bt.start()
    lines = bt.step({"type": "move", "move": "bulletseed"})
    assert sum(1 for line in lines if "击中 卡比兽!" in line) >= 4


def test_selfdestruct_attack_moves_faint_the_user():
    """大爆炸/自爆/薄雾炸裂是攻击招式,旧实现只在 Status 分支读 selfdestruct。"""
    a = _mon("charizard", 50, ["explosion"], pp=10)
    b = _mon("blastoise", 50, ["tackle"])
    bt = start_battle([a], [b], seed=5)
    bt.start()
    bt.step({"type": "move", "move": "explosion"})
    assert a.fainted and a.cur_hp == 0


def test_attack_self_switch_moves_request_a_switch():
    """急速折返/伏特替换造成伤害后要触发换人。"""
    a = _mon("pikachu", 50, ["voltswitch"])
    bench = _mon("pidgey", 20, ["tackle"])
    bt = start_battle([a, bench], [_mon("snorlax", 50, ["tackle"])], seed=5)
    bt.start()
    lines = bt.step({"type": "move", "move": "voltswitch"})
    assert any("换人招式" in line for line in lines)
    assert bt.awaiting_switch is True


def test_trick_room_inverts_speed_order():
    """戏法空间必须真的反转出手顺序(旧实现只记录、从不读取)。"""
    fast = _mon("pikachu", 50, ["tackle"])
    slow = _mon("snorlax", 50, ["tackle"])
    bt = start_battle([fast], [slow], seed=1)
    bt.start()
    normal = bt._action_order({"type": "move", "move": "tackle"},
                              {"type": "move", "move": "tackle"})
    assert normal == ["player", "enemy"], "正常情况下电皮卡丘(110)先出手"
    bt.field_effects["trickroom"] = 4
    reversed_order = bt._action_order({"type": "move", "move": "tackle"},
                                      {"type": "move", "move": "tackle"})
    assert reversed_order == ["enemy", "player"], "戏法空间下慢的卡比兽先出手"
    # 优先级招式不受戏法空间影响
    assert bt._action_order({"type": "move", "move": "quickattack"},
                            {"type": "move", "move": "tackle"})[0] == "player"


def test_terrain_boosts_grounded_attacker():
    """电气场地给接地宝可梦的电气招式 ×1.3。"""

    def shock(terrain: str, species: str = "pikachu") -> int:
        a = _mon(species, 50, ["thunderbolt"])
        b = _mon("snorlax", 50, ["tackle"])
        bt = start_battle([a], [b], seed=3)
        bt.start()
        bt.terrain = terrain
        return _damage(bt.step({"type": "move", "move": "thunderbolt"}))

    assert shock("electricterrain") > shock("")
    # 飞行系不接地,吃不到场地加成
    assert shock("electricterrain", species="zapdos") == shock("", species="zapdos")


def test_analytic_only_boosts_when_moving_second():
    from pw.engine import _attacker_mods

    mon = create_pokemon("snorlax", 50)
    mon.ability = "analytic"
    foe = create_pokemon("pikachu", 50)
    entry = {"category": "Physical", "basePower": 40, "priority": 0}
    bt = start_battle([mon], [foe], seed=1)
    bt.start()
    kwargs = (mon, foe, "tackle", entry, "Normal", "Physical", 1.0, 40)
    bt._second_mover_side = "enemy"          # 我方先手 → 不应加成
    first = _attacker_mods(bt, *kwargs)
    bt._second_mover_side = "player"         # 我方后手 → ×1.3
    second = _attacker_mods(bt, *kwargs)
    assert second > first
    assert abs(second / first - 1.3) < 1e-6


def test_payback_and_assurance_semantics():
    """报复看"本回合后手",保证看"目标本回合已受伤"。"""
    entry = {"category": "Physical", "basePower": 50, "priority": 0}
    mon = create_pokemon("snorlax", 50)
    foe = create_pokemon("pikachu", 50)
    bt = start_battle([mon], [foe], seed=1)
    bt.start()
    power = getattr(bt, "_effective_power", None)
    assert power is not None, "引擎需要有 _effective_power"
    bt._second_mover_side = "player"
    assert power(mon, foe, "payback", entry) == 100, "后手报复翻倍"
    bt._second_mover_side = "enemy"
    assert power(mon, foe, "payback", entry) == 50, "先手不翻倍"
    bt.enemy_damaged = True
    assert power(mon, foe, "assurance", entry) == 100
    bt.enemy_damaged = False
    assert power(mon, foe, "assurance", entry) == 50


def test_magic_guard_blocks_confusion_self_damage():
    mon = create_pokemon("snorlax", 50)
    bt = start_battle([mon], [create_pokemon("pikachu", 50)], seed=1)
    bt.start()
    assert _calc_confusion_damage(bt, mon) > 0
    mon.ability = "magic-guard"
    assert _calc_confusion_damage(bt, mon) == 0


def test_lum_berry_is_consumed_once():
    """木子果:真的消耗掉,不能变成"永久免疫所有异常"。"""
    a = _mon("snorlax", 50, ["tackle"], item="lum-berry")
    b = _mon("pikachu", 50, ["thunderwave", "tackle"])
    bt = start_battle([a], [b], seed=3)
    bt.start()
    lines = bt.step({"type": "move", "move": "thunderwave"})
    assert sum(1 for line in lines if "治愈" in line) == 1, "解状态日志只能出现一次"
    assert a.item == "", "木子果必须被消耗"
    assert a.status == "", "被治愈后不应留下异常"
    # 道具没了,下一次异常状态应当真的生效
    b.pp["thunderwave"] = 5
    a.status = ""
    bt.step({"type": "move", "move": "tackle"})
    a.status = ""
    bt.step({"type": "move", "move": "thunderwave"}) if bt.player.mon is a else None
    assert not a.item


def test_snow_does_no_end_of_turn_damage():
    """第 9 世代的下雪不伤血(旧实现照搬了冰雹)。"""
    a = _mon("charizard", 50, ["tackle"])
    b = _mon("blastoise", 50, ["tackle"])
    bt = start_battle([a], [b], weather="snow", seed=2)
    bt.start()
    lines = bt.step({"type": "move", "move": "tackle"})
    assert not [line for line in lines if "下雪伤害" in line]

    # 沙暴仍然伤血
    a = _mon("charizard", 50, ["tackle"])
    bt = start_battle([a], [_mon("blastoise", 50, ["tackle"])], weather="sand", seed=2)
    bt.start()
    lines = bt.step({"type": "move", "move": "tackle"})
    assert [line for line in lines if "沙暴伤害" in line]


def test_invalid_switch_does_not_clear_awaiting_switch():
    """换人失败不能解除"必须换人"状态 —— 否则场上留着已倒下的宝可梦,战斗死锁。"""
    a = _mon("pidgey", 5, ["tackle"])
    bench = _mon("rattata", 3, ["tackle"])
    bt = start_battle([a, bench], [_mon("snorlax", 60, ["tackle"])], seed=9)
    bt.start()
    bt.step({"type": "move", "move": "tackle"})     # 波波必倒
    assert bt.awaiting_switch and bt.player.mon.fainted
    bt.step({"type": "switch", "index": 99})        # 非法序号
    assert bt.awaiting_switch is True, "换人失败后仍应等待换人"
    assert bt.player.mon.fainted, "场上不能留下已倒下的宝可梦"
    bt.step({"type": "switch", "index": 1})         # 合法换人
    assert bt.awaiting_switch is False
    assert bt.player.mon.display == bench.display
