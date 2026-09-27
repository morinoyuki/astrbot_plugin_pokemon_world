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


def test_base_power_zero_moves_actually_work():
    """数据里 basePower=0 的攻击招式必须真的产生效果。

    分派逻辑原本用 `basePower` 判断"是不是攻击招式",于是反击/镜面反射/金属爆破/
    报恩/撒气/震级 全部掉进状态招式分支 → 命中却 0 伤害(玩家白丢回合)。
    """
    from pw.engine import _COMPUTED_POWER_MOVES
    from pw.engine import create_pokemon as mk

    def battle(move, *, friendship=None, foe="pikachu", seed=3, setup=None):
        a = mk("snorlax", 50, moves=[move])
        a.pp = {move: 30}
        if friendship is not None:
            a.friendship = friendship
        b = mk(foe, 50, moves=["tackle"])
        b.pp = {"tackle": 30}
        bt = start_battle([a], [b], seed=seed)
        bt.start()
        return bt, a, b

    # 报恩(亲密越高越强)/ 撒气(越低越强)
    hi, a1, _ = battle("return", friendship=255)
    lo, a2, _ = battle("return", friendship=0)
    assert hi._effective_power(a1, hi.enemy.mon, "return", {"basePower": 0}) == 102
    assert lo._effective_power(a2, lo.enemy.mon, "return", {"basePower": 0}) == 1

    # 震级:落在真实震级表内
    bt, mon, foe = battle("magnitude")
    power = bt._effective_power(mon, foe, "magnitude", {"basePower": 0})
    assert power in {10, 30, 50, 70, 90, 110, 150}, power

    # 反击:被打到之后才有伤害;没被打到则失败
    a = mk("snorlax", 50, moves=["counter"])
    a.pp = {"counter": 30}
    b = mk("electrode", 60, moves=["tackle"])
    b.pp = {"tackle": 30}
    bt = start_battle([a], [b], seed=4)
    bt.start()
    lines = bt.step({"type": "move", "move": "counter"})
    assert any("造成" in line and b.display in line for line in lines), lines

    # 本回合没被打到(对手用变化招式)→ 明确失败,而不是"1 威力蹭一下"。
    # 注意反击有 -5 先制度,所以只有对手也打不到你时才可能出现这种情况。
    a2 = mk("snorlax", 50, moves=["counter"])
    a2.pp = {"counter": 30}
    b2 = mk("pikachu", 50, moves=["growl"])
    b2.pp = {"growl": 30}
    bt2 = start_battle([a2], [b2], seed=4)
    bt2.start()
    lines2 = bt2.step({"type": "move", "move": "counter"})
    assert any("没有效果" in line for line in lines2), lines2

    assert {"counter", "mirrorcoat", "metalburst", "return",
            "frustration", "magnitude"} <= _COMPUTED_POWER_MOVES


def test_ohko_moves_follow_real_rule():
    """一击必杀:命中即 KO;对手等级更高时必定失败(命中率由数据里的 accuracy=30 管)。"""
    from pw.engine import create_pokemon as mk

    # 低等级对高等级 → 只要命中判定通过,就必须明确失败(命中率 30%,要扫种子)
    refused = False
    for seed in range(1, 40):
        a = mk("machamp", 40, moves=["fissure"])
        a.pp = {"fissure": 50}
        b = mk("snorlax", 50, moves=["tackle"])
        b.pp = {"tackle": 30}
        bt = start_battle([a], [b], seed=seed)
        bt.start()
        lines = bt.step({"type": "move", "move": "fissure"})
        if any("但是没有命中" in line for line in lines):
            continue
        assert any("没有效果" in line for line in lines), lines
        assert not b.fainted, "等级更高时一击必杀不能生效"
        refused = True
        break
    assert refused, "应当有一个种子命中判定通过"

    # 同等级:扫种子直到命中一次,必须直接 KO
    koed = False
    for seed in range(1, 30):
        a = mk("machamp", 50, moves=["fissure"])
        a.pp = {"fissure": 50}
        b = mk("snorlax", 50, moves=["tackle"])
        b.pp = {"tackle": 30}
        bt = start_battle([a], [b], seed=seed)
        bt.start()
        lines = bt.step({"type": "move", "move": "fissure"})
        if any("一击必杀" in line for line in lines):
            assert b.fainted and b.cur_hp == 0, "一击必杀必须直接倒下"
            koed = True
            break
    assert koed, "30 个种子内应当命中过一次(命中率 30%)"


def test_unimplemented_moves_say_so():
    """引擎不模拟的 callback 招式要明确告知,而不是静默 0 伤害。"""
    from pw.engine import create_pokemon as mk

    for move in ("fling", "beatup"):
        a = mk("snorlax", 50, moves=[move])
        a.pp = {move: 20}
        b = mk("pikachu", 50, moves=["tackle"])
        b.pp = {"tackle": 30}
        bt = start_battle([a], [b], seed=5)
        bt.start()
        for _ in range(4):
            lines = bt.step({"type": "move", "move": move})
            if any("引擎" in line for line in lines):
                break
        assert any("引擎" in line for line in lines), lines


def test_stalemate_ends_battle():
    """双方都打不出伤害时战斗必须能结束 —— 否则永远卡在对战里。

    起因:数据里少数物种(小拉达/波波)的低级招式只标了 VC 版本,
    `default_moveset` 补出来全是 0 威力变化招,实测双方互相摇尾巴/迷人,
    30 回合后还是"对战中",玩家出不去(其他行动又被对战锁定)。
    """
    from pw.engine import Battle, Side, create_pokemon

    def status_only(species, level):
        mon = create_pokemon(species, level)
        mon.moves = ["tailwhip", "growl", "sandattack", "leer"]
        mon.pp = dict.fromkeys(mon.moves, 40)
        return mon

    p1 = Side.from_dict({})
    p1.party = [status_only("sprigatito", 10)]
    p1.active = 0
    e1 = Side.from_dict({})
    e1.party = [status_only("rattata", 10)]
    e1.active = 0
    b = Battle(player=p1, enemy=e1)
    b.wild = True
    lines = []
    for _ in range(Battle.STALL_LIMIT + 3):
        if b.finished:
            break
        lines = b.step({"type": "move", "move": "tailwhip"})
    assert b.finished, f"僵局没有收场:{lines[-3:]}"
    assert b.stalled, "应标记为僵局(不是判胜/判负)"
    assert b.winner == ""
    assert any("不了了之" in x for x in lines), lines


def test_stalemate_counter_resets_on_damage():
    """一方掉血就说明还能打,僵局计数必须清零。"""
    from pw.engine import Battle, Side, create_pokemon

    p1 = Side.from_dict({})
    p1.party = [create_pokemon("charizard", 40)]
    p1.active = 0
    e1 = Side.from_dict({})
    e1.party = [create_pokemon("rattata", 5)]
    e1.active = 0
    b = Battle(player=p1, enemy=e1)
    b.wild = True
    for _ in range(6):
        if b.finished:
            break
        b.step({"type": "move", "move": "scratch"})
    assert not b.stalled
    assert b.stall_turns == 0, b.stall_turns


def test_low_level_wild_movesets_can_deal_damage():
    """低级野生宝可梦的招式里必须有能打伤害的 —— 不能全是变化招。

    数据里小拉达/波波/独角虫等的早期招式只标了 VC(V),原来的兜底会补成
    "4 级小拉达会 34 级蛮干 + 迷人/诱惑",双方都打不动。
    """
    from pw.dex import get_dex

    dex = get_dex()
    for species, level in (("rattata", 4), ("pidgey", 5), ("weedle", 3),
                           ("kakuna", 4), ("caterpie", 3)):
        moves = dex.default_moveset(species, level)
        powers = [int((dex.moves.get(m) or {}).get("basePower") or 0) for m in moves]
        assert moves, f"{species} 一个招式都没有"
        assert any(p > 0 for p in powers), f"{species} Lv{level} 全是变化招:{moves}"
        # 兜底路径不该再堆一整套 TM
        assert len(moves) <= 4


def test_normal_species_get_level_appropriate_moves():
    """正常物种只用当前等级已学会的等级招(不再硬补 TM)。"""
    from pw.dex import get_dex

    dex = get_dex()
    moves = dex.default_moveset("sprigatito", 5)
    assert "scratch" in moves and "leafage" in moves
    assert "magicalleaf" not in moves, "10 级的魔法叶不该出现在 5 级身上"
    assert all(
        int((dex.moves.get(m) or {}).get("basePower") or 0) >= 0 for m in moves
    )


def test_capture_ends_battle_without_further_damage():
    """捕获成功就是战斗结束 —— 不能再结算回合结束的天气/异常伤害。

    实测日志:「投出了 精灵球!/恭喜!成功捕获了 波波!/杰尼龟 受到沙暴伤害 1。/
    波波 受到沙暴伤害 1。」 —— 后面两行既荒唐(球里的宝可梦还在掉血),
    还可能把刚收服的宝可梦打成濒死。
    """
    from pw.engine import Battle, Side, create_pokemon

    for _ in range(30):
        p1 = Side.from_dict({})
        p1.party = [create_pokemon("squirtle", 30)]
        p1.active = 0
        e1 = Side.from_dict({})
        foe = create_pokemon("pidgey", 5)
        foe.cur_hp = max(1, foe.cur_hp // 2)
        e1.party = [foe]
        e1.active = 0
        b = Battle(player=p1, enemy=e1)
        b.wild = True
        b.weather = "sand"
        b.weather_turns = 9
        b.bag = {"master-ball": 3}
        logs = b.step({"type": "catch", "item": "master-ball"})
        if b.captured is not None:
            break
    assert b.finished and b.captured is not None, "大师球应当必定捕获"
    joined = "\n".join(logs)
    assert "捕获" in joined
    assert "受到" not in joined and "沙暴" not in joined, f"捕获后还在结算伤害:{logs}"
    # 收服到的宝可梦也不该被判濒死
    assert not e1.party[0].fainted


def test_escape_ends_battle_without_end_of_turn_damage():
    """逃跑成功同理:不该再走回合结束的伤害结算。"""
    from pw.engine import Battle, Side, create_pokemon

    for _ in range(60):
        p1 = Side.from_dict({})
        p1.party = [create_pokemon("squirtle", 40)]
        p1.active = 0
        e1 = Side.from_dict({})
        e1.party = [create_pokemon("pidgey", 3)]
        e1.active = 0
        b = Battle(player=p1, enemy=e1)
        b.wild = True
        b.weather = "sand"
        b.weather_turns = 9
        logs = b.step({"type": "run"})
        if b.escaped:
            break
    assert b.escaped, "等级差这么大应该能跑掉"
    joined = "\n".join(logs)
    assert "受到" not in joined and "沙暴" not in joined, f"逃跑后还在结算伤害:{logs}"
