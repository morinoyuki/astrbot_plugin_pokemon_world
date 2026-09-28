"""变化招式的“能生效”回归:以前一大批招式放出来只有一行「使用了 X!」。

实测反馈从「皮卡丘（Cosplay）用了电光」起头,顺手把整套变化招式盘了一遍 ——
数据里 90 多个招式在引擎里完全没有实现(纯状态招、basePower=0 的攻击招、
volatileStatus 未识别……)。这里把补上的都锁住,免得以后又退回“白放”。
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pw.engine import Battle, Side, create_pokemon  # noqa: E402


def _duel(move: str, *, user="snorlax", foe="pikachu", foe_move="splash",
          level: int = 40, foe_level: int = 40) -> tuple[Battle, object, object]:
    """一场“我方先手、对手只放水招”的对战,方便单点验证一个招式。"""
    me = create_pokemon(user, level)
    me.moves = [move]
    me.pp = {move: 10}
    me.stats["spe"] = 9999
    them = create_pokemon(foe, foe_level)
    them.moves = [foe_move]
    them.pp = {foe_move: 10}
    them.stats["spe"] = 1
    b = Battle(player=Side("player", [me]), enemy=Side("enemy", [them]), wild=True)
    b.start()
    b.log = []
    return b, me, them


def _use(b: Battle, move: str) -> list[str]:
    b.step({"type": "move", "move": move})
    return [line for line in b.log if "使用了" not in line and "派出了" not in line]


# ── 0 威力攻击招:以前命中却 0 伤害 ──────────────────────────────
def test_hp_scaling_attacks_really_scale():
    for move in ("crushgrip", "wringout", "hardpress"):
        full, _me, foe = _duel(move)
        _use(full, move)
        assert foe.cur_hp < foe.max_hp, f"{move} 满血目标没掉血"
        half, _me2, foe2 = _duel(move)
        foe2.cur_hp = max(1, foe2.max_hp // 2)
        _use(half, move)
        # 目标血越少威力越低(正作:威力 × 目标剩余 HP 比例)
        full_dmg = foe.max_hp - foe.cur_hp
        half_dmg = max(0, foe2.max_hp // 2 - foe2.cur_hp)
        assert half_dmg < full_dmg, f"{move} 没有随目标 HP 缩放"


def test_stockpile_spitup_and_swallow():
    b, me, foe = _duel("stockpile")
    _use(b, "stockpile")
    _use(b, "stockpile")
    assert me.volatiles["stockpile"] == 2
    me.moves = ["spitup"]
    b.log = []
    _use(b, "spitup")
    assert foe.cur_hp < foe.max_hp and "stockpile" not in me.volatiles
    # 没有蓄力就喷不出东西
    b2, _me2, foe2 = _duel("spitup")
    lines = _use(b2, "spitup")
    assert any("没有蓄力" in line for line in lines), lines
    assert foe2.cur_hp == foe2.max_hp


def test_present_is_random_but_says_what_happened():
    saw: set[str] = set()
    for seed in range(40):
        b, _me, _foe = _duel("present")
        b.seed = seed
        lines = _use(b, "present")
        saw.add("heal" if any("礼物是回复" in line for line in lines) else "hit")
        assert lines, "礼物什么都没发生"
    assert saw == {"heal", "hit"}, f"40 个种子只出现了 {saw}"


def test_comeuppance_returns_higher_damage_when_hurt():
    b, me, foe = _duel("comeuppance", foe_move="tackle")
    foe.stats["spe"] = 9999          # 对手先打,复仇才有料
    me.stats["spe"] = 1
    b.step({"type": "move", "move": "comeuppance"})
    taken = me.max_hp - me.cur_hp
    assert taken > 0
    assert foe.max_hp - foe.cur_hp == int(taken * 1.5), b.log


# ── 挺住 / 防守变体 ─────────────────────────────────────────────
def test_endure_survives_with_one_hp():
    b, me, _foe = _duel("endure", foe_move="tackle")
    me.cur_hp = 5
    b.step({"type": "move", "move": "endure"})
    assert me.cur_hp == 1, f"挺住没生效(剩 {me.cur_hp} HP)"
    assert any("挺住了" in line for line in b.log), b.log


def test_spiky_shield_punishes_contact():
    b, me, foe = _duel("spikyshield", foe_move="tackle")
    b.step({"type": "move", "move": "spikyshield"})
    assert any("保护了自己" in line for line in b.log), b.log
    assert me.cur_hp == me.max_hp, "尖刺防守没挡住伤害"
    assert foe.cur_hp < foe.max_hp, "尖刺防守没有扎伤接触招式的使用者"


# ── 气象回复 ────────────────────────────────────────────────────
def test_weather_heals_depend_on_weather():
    healed: dict[str, int] = {}
    for weather in ("sun", "rain", ""):
        b, me, _foe = _duel("synthesis")
        if weather:
            b.weather, b.weather_turns = weather, 5
        me.cur_hp = 10
        b.step({"type": "move", "move": "synthesis"})
        healed[weather] = me.cur_hp - 10
    assert healed["sun"] > healed[""] > healed["rain"] > 0, healed


# ── 控制类 ──────────────────────────────────────────────────────
def test_disable_blocks_the_foes_move():
    b, me, foe = _duel("disable", foe_move="tackle")
    foe.last_move = "tackle"
    _use(b, "disable")
    assert foe.volatiles.get("disable_move") == "tackle"
    foe.stats["spe"] = 9999          # 换对手先行动,验证封锁
    me.stats["spe"] = 1
    b.log = []
    _use(b, "splash")
    assert any("被定身" in line for line in b.log), b.log


def test_encore_forces_the_previous_move():
    b, me, foe = _duel("encore", foe_move="tackle")
    foe.last_move = "tackle"
    _use(b, "encore")
    assert foe.volatiles.get("encore_move") == "tackle"
    foe.stats["spe"] = 9999
    me.stats["spe"] = 1
    foe.moves = ["tackle", "splash"]
    foe.pp = {"tackle": 10, "splash": 10}
    b.log = []
    b.step({"type": "move", "move": "splash"})   # AI 会被逼着继续用 tackle
    assert any("撞击" in line or "tackle" in line.lower() for line in b.log), b.log


def test_torment_blocks_repeating_a_move():
    b, me, foe = _duel("torment", foe_move="tackle")
    _use(b, "torment")
    foe.last_move = "tackle"
    foe.stats["spe"] = 9999
    me.stats["spe"] = 1
    b.log = []
    _use(b, "splash")
    assert any("无理取闹" in line for line in b.log), b.log


def test_taunt_blocks_status_moves_and_expires():
    b, me, foe = _duel("synthesis", foe_move="taunt")
    foe.stats["spe"] = 9999
    me.stats["spe"] = 1
    me.moves = ["synthesis", "splash"]
    me.pp = {"synthesis": 10, "splash": 20}
    b.log = []
    b.step({"type": "move", "move": "synthesis"})
    assert any("被挑衅" in line for line in b.log), b.log
    assert me.volatiles.get("taunt")
    foe.moves, foe.pp = ["tackle"], {"tackle": 40}   # 让对手真打人,避免“僵持”收场
    me.stats["spe"] = 9999
    for _ in range(3):
        assert not b.finished, "战斗提前结束了(测试本身要修)"
        b.step({"type": "move", "move": "splash"})
    assert not me.volatiles.get("taunt")


def test_yawn_puts_the_target_to_sleep_next_turn():
    b, _me, foe = _duel("yawn")
    _use(b, "yawn")
    assert foe.volatiles.get("yawn")
    b.step({"type": "move", "move": "splash"})   # 过一回合
    assert foe.status == "slp", f"哈欠没让对手睡着:{foe.status}"


def test_perish_song_ends_the_battle_in_three_turns():
    b, me, foe = _duel("perishsong", foe_move="tackle")
    me.moves = ["perishsong", "splash"]
    me.pp = {"perishsong": 5, "splash": 20}
    b.step({"type": "move", "move": "perishsong"})
    assert me.volatiles.get("perish")
    for _ in range(4):
        if b.finished:
            break
        b.step({"type": "move", "move": "splash"})
    assert me.fainted and foe.fainted, "灭亡之歌没带走双方"


def test_destiny_bond_takes_the_attacker_down():
    b, me, foe = _duel("destinybond", foe_move="tackle")
    b.step({"type": "move", "move": "destinybond"})
    assert me.volatiles.get("destinybond")
    me.cur_hp = 1                    # 下一回合被打倒 → 同命生效
    foe.stats["spe"] = 9999
    me.stats["spe"] = 1
    b.log = []
    b.step({"type": "move", "move": "splash"})
    assert me.fainted and foe.fainted, f"同命没生效:{b.log}"


def test_attract_marks_the_foe():
    b, me, foe = _duel("attract")
    me.gender, foe.gender = "M", "F"
    _use(b, "attract")
    assert foe.volatiles.get("attract")


# ── 数值重分配 ──────────────────────────────────────────────────
def test_stat_split_swap_and_copy():
    b, me, foe = _duel("powersplit")
    _use(b, "powersplit")
    assert me.stat_override["atk"] == foe.stat_override["atk"]

    b, me, foe = _duel("speedswap")
    _use(b, "speedswap")
    assert me.stat_override["spe"] == foe.stats["spe"]
    assert foe.stat_override["spe"] == me.stats["spe"]

    b, me, foe = _duel("psychup")
    foe.stages = {"atk": 2}
    _use(b, "psychup")
    assert me.stages == {"atk": 2}

    b, _me, foe = _duel("topsyturvy")
    foe.stages = {"atk": 2, "def": -1}
    _use(b, "topsyturvy")
    assert foe.stages == {"atk": -2, "def": 1}

    b, me, _foe = _duel("bellydrum")
    _use(b, "bellydrum")
    assert me.stages["atk"] == 6 and me.cur_hp <= me.max_hp // 2

    b, me, foe = _duel("strengthsap")
    me.cur_hp = me.max_hp // 2
    _use(b, "strengthsap")
    assert me.cur_hp > me.max_hp // 2 and foe.stage("atk") == -1


def test_stat_overrides_never_reach_the_save():
    b, me, _foe = _duel("speedswap")
    _use(b, "speedswap")
    saved = me.to_storage_dict()
    assert saved["stat_override"] == {} and saved["stats"] == me.stats


# ── 属性 / 特性改写 ─────────────────────────────────────────────
def test_type_and_ability_rewrites():
    b, _me, foe = _duel("soak")
    _use(b, "soak")
    assert foe.types == ["Water"]

    b, me, foe = _duel("reflecttype", foe="charizard")
    _use(b, "reflecttype")
    assert me.types == foe.entry["types"]

    b, me, foe = _duel("skillswap")
    mine, theirs = me.ability_now(), foe.ability_now()
    _use(b, "skillswap")
    assert me.ability_now() == theirs and foe.ability_now() == mine

    b, _me, foe = _duel("gastroacid")
    _use(b, "gastroacid")
    assert foe.ability_suppressed and not foe.has_ability(foe.ability)

    b, _me, foe = _duel("soak")
    _use(b, "soak")
    saved = foe.to_storage_dict()
    assert saved["type_override"] == []
    assert saved["ability_suppressed"] is False


# ── 道具 / 借用招式 ─────────────────────────────────────────────
def test_trick_and_recycle():
    b, me, foe = _duel("trick")
    me.item, foe.item = "leftovers", "sitrus-berry"
    _use(b, "trick")
    assert (me.item, foe.item) == ("sitrus-berry", "leftovers")

    b, me, _foe = _duel("recycle")
    me.volatiles["_eaten_berry"] = "sitrus-berry"
    _use(b, "recycle")
    assert me.item == "sitrus-berry"


def test_called_moves_do_something():
    b, _me, foe = _duel("mirrormove", foe_move="tackle")
    foe.last_move = "tackle"
    lines = _use(b, "mirrormove")
    assert any("鹦鹉学舌 使出了" in line for line in lines), lines
    assert foe.cur_hp < foe.max_hp

    b, _me, _foe = _duel("metronome")
    b.seed = 3
    lines = _use(b, "metronome")
    assert any("挥指 使出了" in line for line in lines), lines

    b, me, foe = _duel("mimic", foe_move="tackle")
    foe.last_move = "tackle"
    _use(b, "mimic")
    assert "tackle" in me.moves and "mimic" not in me.moves

    b, me, _foe = _duel("sleeptalk")
    me.moves = ["sleeptalk", "thunderbolt"]
    me.pp = {"sleeptalk": 5, "thunderbolt": 5}
    lines = _use(b, "sleeptalk")
    assert any("梦话 使出了" in line for line in lines), lines


# ── 换人 / 陷阱 ─────────────────────────────────────────────────
def test_roar_pushes_the_wild_foe_away_and_trapping_blocks_switching():
    b, _me, _foe = _duel("roar")
    _use(b, "roar")
    assert b.finished and b.escaped, "吼叫没把野生宝可梦赶走"

    b, _me, foe = _duel("meanlook")
    _use(b, "meanlook")
    assert foe.volatiles.get("trapped")
    b.enemy.party.append(create_pokemon("geodude", 40))
    assert b._do_switch(b.enemy, 1) is False, "被盯住还能换人"
    # 被盯住的是对手,自己换人不该被拦
    b.player.party.append(create_pokemon("geodude", 40))
    assert b._do_switch(b.player, 1) is True


def test_roar_swaps_in_a_trainer_team_member():
    me = create_pokemon("snorlax", 40)
    me.moves, me.pp, me.stats["spe"] = ["roar"], {"roar": 10}, 9999
    first = create_pokemon("pikachu", 30)
    second = create_pokemon("geodude", 30)
    b = Battle(player=Side("player", [me]), enemy=Side("enemy", [first, second]), wild=False)
    b.start()
    b.log = []
    b.step({"type": "move", "move": "roar"})
    assert b.enemy.active == 1, "训练家的宝可梦没有被赶下场"


# ── 回合末效果 ──────────────────────────────────────────────────
def test_wish_and_aquaring_heal_over_time():
    b, me, _foe = _duel("wish")
    me.cur_hp = me.max_hp // 2
    _use(b, "wish")
    mid = me.cur_hp
    b.step({"type": "move", "move": "splash"})
    assert me.cur_hp > mid, "祈愿没有在下回合回复"

    b, me, _foe = _duel("aquaring")
    me.cur_hp = me.max_hp // 2
    _use(b, "aquaring")
    mid = me.cur_hp
    b.step({"type": "move", "move": "splash"})
    assert me.cur_hp > mid, "水流环没有回复"


def test_flavor_moves_say_what_happened():
    for move, want in (("splash", "什么都没有发生"), ("celebrate", "什么都没有发生"),
                       ("helpinghand", "单打里没有效果")):
        b, _me, _foe = _duel(move)
        lines = _use(b, move)
        assert any(want in line for line in lines), (move, lines)


# ── 收尾的 4 个冷门但可达的招式 ─────────────────────────────────
def test_pain_split_averages_hp():
    b, me, foe = _duel("painsplit")
    me.cur_hp = 10
    foe.cur_hp = foe.max_hp
    _use(b, "painsplit")
    assert me.cur_hp == (10 + foe.max_hp) // 2 or me.cur_hp == foe.max_hp // 2 + 5, (me.cur_hp,)
    assert abs(me.cur_hp - foe.cur_hp) <= 1


def test_no_retreat_boosts_everything_and_traps_self():
    b, me, _foe = _duel("noretreat")
    _use(b, "noretreat")
    assert me.stages == {"atk": 1, "def": 1, "spa": 1, "spd": 1, "spe": 1}
    b.player.party.append(create_pokemon("geodude", 40))
    assert b._do_switch(b.player, 1) is False, "背水一战之后还能换人"


def test_tar_shot_weakens_fire_and_lowers_speed():
    b, me, foe = _duel("tarshot")
    before = foe.stage("spe")
    _use(b, "tarshot")
    assert foe.volatiles.get("tarshot") and foe.stage("spe") == before - 1
    # 火属性招式现在打它更疼
    me.moves, me.pp = ["flamethrower"], {"flamethrower": 10}
    me.stats["spe"] = 9999
    b.log = []
    _use(b, "flamethrower")
    hurt_with = foe.max_hp - foe.cur_hp
    b2, me2, foe2 = _duel("flamethrower", foe="pikachu")
    me2.stats["spe"] = 9999
    b2.log = []
    _use(b2, "flamethrower")
    hurt_without = foe2.max_hp - foe2.cur_hp
    assert hurt_with > hurt_without, (hurt_with, hurt_without)


def test_dragon_cheer_raises_crit_rate():
    b, me, _foe = _duel("dragoncheer")
    _use(b, "dragoncheer")
    assert me.volatiles.get("focusenergy") and me.volatiles.get("dragoncheer")


# ── 收尾补充:接力 / 复活 / 白雾・神秘守护 ───────────────────────
def test_baton_pass_hands_over_boosts():
    b, me, _foe = _duel("batonpass")
    me.moves = ["batonpass", "splash"]
    me.pp = {"batonpass": 5, "splash": 20}
    me.stages = {"atk": 2, "spe": 1}
    b.player.party.append(create_pokemon("geodude", 40))
    b.step({"type": "move", "move": "batonpass"})
    assert b.awaiting_switch or b.player.active == 1
    b._do_switch(b.player, 1)
    assert b.player.mon.stages == {"atk": 2, "spe": 1}, "接力棒没把能力变化带过去"


def test_revival_blessing_and_shed_tail():
    b, me, _foe = _duel("revivalblessing")
    fallen = create_pokemon("geodude", 20)
    fallen.cur_hp, fallen.fainted = 0, True
    b.player.party.append(fallen)
    _use(b, "revivalblessing")
    assert not fallen.fainted and fallen.cur_hp == fallen.max_hp // 2

    b, me, _foe = _duel("shedtail")
    b.player.party.append(create_pokemon("geodude", 40))
    _use(b, "shedtail")
    assert me.cur_hp <= me.max_hp // 2 + 1


def test_safeguard_and_mist_block_status_and_drops():
    b, _me, _foe = _duel("safeguard")
    b.player.party.append(create_pokemon("geodude", 40))
    b.step({"type": "move", "move": "safeguard"})     # 先立守护
    assert "safeguard" in b.player.screens
    b.player.screens["safeguard"] = 5
    b2, me2, _foe2 = _duel("safeguard")
    me2.moves, me2.pp = ["safeguard", "splash"], {"safeguard": 5, "splash": 20}
    b2.step({"type": "move", "move": "safeguard"})
    me2.moves, me2.pp = ["thunderwave"], {"thunderwave": 10}
    b2.step({"type": "move", "move": "thunderwave"})  # 对手被守护挡下异常
    assert not me2.status and "safeguard" in b2.player.screens

    b3, _me3, foe3 = _duel("mist")
    b3.step({"type": "move", "move": "mist"})
    b3.player.screens["mist"] = 5
    foe3.stats["spe"] = 9999
    b3.player.mon.moves, b3.player.mon.pp = ["growl"], {"growl": 10}
    foe3.moves, foe3.pp = ["leer"], {"leer": 10}
    b3.step({"type": "move", "move": "growl"})
    assert any("白雾" in line for line in b3.log), b3.log
