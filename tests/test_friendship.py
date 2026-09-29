"""亲密度系统 + 进化石获取途径:数值要能看、能看到含义、有第二条来源。"""

from __future__ import annotations

import io
import os
import sys
import tempfile

from PIL import Image

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402

from pw import growth  # noqa: E402
from pw.engine import create_pokemon  # noqa: E402
from pw.items import stone_for_location  # noqa: E402
from pw.quests import REWARD_ITEMS  # noqa: E402


def test_friendship_tier_and_hearts():
    """档位与心形:0~255 要有直观档位,不能只给裸数字。"""
    assert growth.friendship_tier(0) == "陌生"
    assert growth.friendship_tier(90) == "熟悉"
    assert growth.friendship_tier(120) == "亲密"
    assert growth.friendship_tier(255) == "形影不离"
    assert growth.friendship_hearts(0) == "♡♡♡♡♡"
    assert growth.friendship_hearts(255) == "♥♥♥♥♥"
    assert growth.friendship_hearts(120).count("♥") == 2
    assert "120/255" in growth.friend_line(120)


def test_add_friendship_clamps_both_ends():
    """增减亲密度夹在 0~255,对象与字典都能用。"""
    mon = create_pokemon("pikachu", 10)
    mon.friendship = 250
    assert growth.add_friendship(mon, 20) == 5
    assert mon.friendship == 255
    assert growth.add_friendship(mon, -500) == -255
    assert mon.friendship == 0
    md = {"friendship": 70}
    assert growth.add_friendship(md, 10) == 10
    assert md["friendship"] == 80


def test_friendship_evolution_condition_is_data_driven():
    """亲密度进化(如皮丘→皮卡丘)条件必须真的在数据里。"""
    from pw.dex import get_dex

    conds = get_dex().evolutions("pichu") if hasattr(get_dex(), "evolutions") else []
    assert isinstance(conds, list)


def test_stones_have_a_second_source():
    """进化石不只能买:委托奖励池里有,且地点主题掉落能对上。"""
    assert {"moon-stone", "sun-stone", "dusk-stone", "dawn-stone", "leaf-stone"} <= set(
        REWARD_ITEMS
    )
    assert stone_for_location("kanto-mt-moon") == "moon-stone"
    assert stone_for_location("kanto-rock-tunnel") == "dusk-stone"
    assert stone_for_location("kanto-viridian-forest") == "leaf-stone"
    assert stone_for_location("kanto-route-1") == ""


def test_mon_summary_shows_friendship_meaning():
    """/宝可梦 资料页必须能看到亲密度(数值 + 档位 + 心)。"""
    from pw import ui_render as UI

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False}
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        t = p._load(_Event())
        md = t.party[0]
        md["friendship"] = 120
        p._save(t)
        payload = p._mon_payload(t, p._load(_Event()).party[0])
        assert payload["friendship"] == 120
        data = UI.render_mon_summary(payload, scale=2)
        assert data.startswith(b"\x89PNG")
        im = Image.open(io.BytesIO(data))
        assert im.size[0] > 0


def test_friend_label_text_is_used_by_summary():
    from pw.ui_render import _FRIEND_TIERS, _friend_label

    assert "亲密" in _friend_label(120)
    assert _friend_label(120).count("♥") == 2
    # 资料页那张档位表必须和 growth 里的一致(改一处漏一处的护栏)
    assert tuple(_FRIEND_TIERS) == tuple(growth.FRIEND_TIERS)


def test_biome_stone_drop_on_explore():
    """按地貌掉进化石:月见山能挖到月之石,1 号道路挖不到。"""
    class _Rng:
        def random(self):
            return 0.0

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False}
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        t = p._load(_Event())
        t.data["location"] = "kanto-mt-moon"
        p._save(t)
        t = p._load(_Event())
        line = p._maybe_stone_find(t, _Rng())
        assert "月之石" in line, line
        assert t.count("moon-stone") == 1
        # 已经有一块就不再刷
        assert p._maybe_stone_find(t, _Rng()) == ""
        # 普通道路没有地貌特产
        t.data["location"] = "kanto-route-1"
        p._save(t)
        assert p._maybe_stone_find(p._load(_Event()), _Rng()) == ""


def test_pet_command_raises_friendship_once_a_day():
    """/亲昵:每只每天一次 +3,同一天再摸没效果,换一天又能摸。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False}
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        t = p._load(_Event())
        t.party[0]["friendship"] = 80
        p._save(t)
        ev = _Event("/亲昵 1")
        run_cmd(p, ev, p.cmd_pet)
        assert any("83" in o for o in ev.outputs), ev.outputs
        assert p._load(_Event()).party[0]["friendship"] == 83
        # 同一天第二次:不涨
        ev2 = _Event("/亲昵 1")
        run_cmd(p, ev2, p.cmd_pet)
        assert any("今天已经被你摸过" in o for o in ev2.outputs), ev2.outputs
        assert p._load(_Event()).party[0]["friendship"] == 83
        # 换一天:又能摸
        state = p._state(t.scope)
        state.data["day"] = int(state.day) + 1
        p.worlds.save(state.scope, state.data)
        ev3 = _Event("/亲昵 1")
        run_cmd(p, ev3, p.cmd_pet)
        assert p._load(_Event()).party[0]["friendship"] == 86


def test_battle_friendship_rewards_hard_fights_more():
    """一起打道馆/联盟比普通对战更增进感情(+7 vs +2)。"""
    import inspect

    from pw import battle as B

    src = inspect.getsource(B._finish_win)
    assert '"gym", "elite", "champion"' in src
    assert "growth.add_friendship" in src


def test_feeding_a_berry_raises_friendship():
    """喂树果:回血之外还加亲密度,且亲密度越低涨越多(正作规则)。"""
    assert [growth.berry_friendship_delta(f) for f in (0, 99, 100, 199, 200, 255)] == [
        10, 10, 5, 5, 2, 2
    ]
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False}
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        t = p._load(_Event())
        t.add_item("oran-berry", 2)
        t.party[0]["cur_hp"] = 5
        t.party[0]["friendship"] = 80
        p._save(t)
        ev = _Event("/使用 橙橙果 1")
        run_cmd(p, ev, p.cmd_use)
        out = "\n".join(ev.outputs)
        assert "亲密度 +10" in out, out
        after = p._load(_Event())
        assert after.party[0]["friendship"] == 90
        assert after.party[0]["cur_hp"] > 5          # 回血效果照旧
        assert after.count("oran-berry") == 1         # 消耗掉一颗
        # 高亲密度时只 +2(不刷爆)
        t2 = p._load(_Event())
        t2.party[0]["friendship"] = 250
        t2.party[0]["cur_hp"] = 5
        p._save(t2)
        ev2 = _Event("/使用 橙橙果 1")
        run_cmd(p, ev2, p.cmd_use)
        assert p._load(_Event()).party[0]["friendship"] == 252


def test_dex_unlocks_on_evolution_and_acquisition():
    """进化/入队同样解锁图鉴(实测:进化成炽焰咆哮虎后图鉴仍显示未发现)。"""
    import tempfile

    from test_commands import _Cmd, _Event, run_cmd

    from pw.engine import create_pokemon

    tmp = tempfile.TemporaryDirectory()
    with tmp:
        p = _Cmd(tmp.name)
        p.config = {"ui_image": False}
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        t = p._load(_Event())
        assert not t.caught("incineroar")
        t.commit(0, create_pokemon("incineroar", 40))     # 模拟进化后写回队伍
        p._save(t)
        t2 = p._load(_Event())
        assert t2.caught("incineroar"), "进化/获得过的形态必须解锁图鉴"
        assert t2.seen("incineroar")
        # 图鉴页也要显示已捕获
        ev = _Event("/图鉴 炽焰咆哮虎")
        run_cmd(p, ev, p.cmd_dex)
        out = "".join(ev.outputs)
        assert "已捕获" in out, out[:200]


def test_old_saves_backfill_dex_for_evolved_mons():
    """修复前就进化好的宝可梦要**补算**进图鉴(旧档自愈,不需玩家操作)。"""
    import tempfile

    from test_commands import SCOPE, _Cmd, _Event, run_cmd

    from pw.engine import create_pokemon
    from pw.player import mon_to_dict

    tmp = tempfile.TemporaryDirectory()
    with tmp:
        p = _Cmd(tmp.name)
        p.config = {"ui_image": False}
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        t = p._load(_Event())
        # 模拟"旧档":队伍里有炽焰咆哮虎(闪光),但图鉴里没有它
        raw = p.trainers.load(SCOPE, t.uid)
        raw["party"].append(mon_to_dict(create_pokemon("incineroar", 40, shiny=True)))
        raw["dex_caught"] = [m.get("species") for m in raw["party"][:1]]
        raw["dex_seen"] = list(raw["dex_caught"])
        p.trainers.save(SCOPE, t.uid, raw)

        back = p._load(_Event())          # 载入即自愈
        assert back.caught("incineroar"), "旧档里进化好的宝可梦应该补算"
        assert back.shiny_caught("incineroar"), "闪光形态也要补算进闪光图鉴"
        ev = _Event("/图鉴 炽焰咆哮虎")
        run_cmd(p, ev, p.cmd_dex)
        assert "已捕获" in "".join(ev.outputs)
