"""信息类界面渲染测试(Pillow)。

运行:
    /mnt/e/astrbot_plugin_life_sim/.venv/bin/python -m pytest tests/test_ui_info.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from io import BytesIO

from PIL import Image, ImageStat

from pw import ui_info as UI

SCALE = 3
SIZE = (240 * SCALE, 160 * SCALE)

SITES = [
    {"species": "articuno", "zh": "急冻鸟", "location": "seafoam-islands",
     "location_zh": "双子岛", "level": 50, "need": 4, "champion": False},
    {"species": "zapdos", "zh": "闪电鸟", "location": "kanto-power-plant",
     "location_zh": "无人发电厂", "level": 50, "need": 5, "champion": False},
    {"species": "moltres", "zh": "火焰鸟", "location": "kanto-victory-road-2",
     "location_zh": "冠军之路", "level": 50, "need": 7, "champion": False},
    {"species": "mewtwo", "zh": "超梦", "location": "cerulean-cave",
     "location_zh": "华蓝洞窟", "level": 70, "need": 0, "champion": True},
]
MON = {"species": "gyarados", "name": "暴鲤龙", "level": 105, "cur_hp": 345,
       "max_hp": 353, "gender": "F", "status": "", "exp_pct": 38.0}


def _check(data: bytes) -> Image.Image:
    assert data, "渲染返回空字节"
    assert data.startswith(b"\x89PNG"), "不是 PNG 头"
    with Image.open(BytesIO(data)) as im:
        assert im.size == SIZE, f"尺寸不对:{im.size}"
        colors = len(im.convert("RGB").getcolors(maxcolors=1 << 20) or [])
        assert colors > 300, f"画面过于单调,只有 {colors} 种颜色"
        return im.copy()


def _colors(data: bytes) -> int:
    with Image.open(BytesIO(data)) as im:
        return len(im.convert("RGB").getcolors(maxcolors=1 << 20) or [])


def _crop(data: bytes, box):
    """box 用逻辑坐标。"""
    x0, y0, x1, y1 = box
    with Image.open(BytesIO(data)) as im:
        return im.convert("RGB").crop(
            (int(x0 * SCALE), int(y0 * SCALE), int(x1 * SCALE), int(y1 * SCALE))
        )


def _saturation(img: Image.Image) -> float:
    """平均饱和度(max-min),用来区分彩色精灵与灰阶剪影。"""
    raw = img.tobytes()
    total = 0
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        total += max(r, g, b) - min(r, g, b)
    return total / (img.width * img.height)


# ══════════════════════════════════════════════════════════════════
# 六个界面:都能出图、尺寸正确、不空白
# ══════════════════════════════════════════════════════════════════
def test_render_legendaries():
    data = UI.render_legendaries("关都", SITES, caught=["articuno"], ready=["zapdos"],
                                 locked=["mewtwo"], badges=5, total_gyms=8, day=12,
                                 scale=SCALE)
    _check(data)


def test_render_news():
    data = UI.render_news(12, world_events=["火箭队又在玉虹市出现了!", "森林很热闹。"],
                          player_events=["你击败了短裤小子 小明。", "暴鲤龙 升到了 Lv105。"],
                          weather_zh="晴天", region_zh="关都", location_zh="玉虹市",
                          locks=["世界大赛(需 8 枚徽章)"], scale=SCALE)
    _check(data)


def test_render_tournament():
    data = UI.render_tournament(["八强赛", "四强赛", "决赛"], best=1, current=1,
                                titles=["地区的四天王"], last_foe="劲敌 小茂",
                                is_champion=False, scale=SCALE)
    _check(data)


def test_render_tournament_champion():
    data = UI.render_tournament(["八强赛", "四强赛", "决赛"], best=2, current=2,
                                titles=["地区的四天王", "别区冠军", "世界冠军"],
                                last_foe="世界冠军 阿渡", is_champion=True, scale=SCALE)
    _check(data)


def test_render_battle_result():
    data = UI.render_battle_result(
        outcome="win", title="道馆战",
        lines=["暴鲤龙 使用了 水炮!", "大岩蛇 倒下了!", "你赢得了 灰色徽章!"],
        rewards=["灰色徽章", "3000₽", "招式机 岩石封锁"],
        growth=["暴鲤龙 升到了 Lv105!"], mon=MON, scale=SCALE,
    )
    _check(data)


def test_render_battle_result_without_mon():
    data = UI.render_battle_result(outcome="loss", title="野斗",
                                   lines=["皮卡丘 倒下了……"], scale=SCALE)
    _check(data)


def test_render_growth():
    data = UI.render_growth(MON, before_level=104, after_level=105,
                            learned=["水炮", "咬碎"], pending=["龙之舞"], scale=SCALE)
    _check(data)


def test_render_growth_evolution():
    data = UI.render_growth({"species": "gyarados", "name": "暴鲤龙", "level": 21},
                            before_level=20, after_level=21, learned=["咬碎"],
                            evolved_from_zh="鲤鱼王", evolved_to_zh="暴鲤龙", scale=SCALE)
    _check(data)


def test_render_gotcha():
    data = UI.render_gotcha(MON, ball_zh="超级球", dex_line="图鉴已记录:31 种", scale=SCALE)
    _check(data)


# ══════════════════════════════════════════════════════════════════
# 全空输入:仍然是合法 PNG,不抛异常
# ══════════════════════════════════════════════════════════════════
def test_all_empty_inputs_still_render():
    outs = [
        UI.render_legendaries("", [], scale=SCALE),
        UI.render_news(0, scale=SCALE),
        UI.render_tournament([], scale=SCALE),
        UI.render_battle_result(outcome="", scale=SCALE),
        UI.render_growth({}, scale=SCALE),
        UI.render_gotcha({}, scale=SCALE),
    ]
    for data in outs:
        assert data.startswith(b"\x89PNG"), "空输入没有回退成合法 PNG"
        with Image.open(BytesIO(data)) as im:
            assert im.size == SIZE


def test_bad_inputs_never_raise():
    """脏数据(类型错误)也必须返回 PNG 或空字节,而不是抛异常。"""
    outs = [
        UI.render_legendaries(None, [{"species": None, "level": "x"}], caught=None,
                              ready=None, locked=None, badges="x", day=None, scale=SCALE),
        UI.render_news(None, world_events=[None, 1], locks=[None], scale=SCALE),
        UI.render_tournament([None, 1, 2], best="a", current=None, scale=SCALE),
        UI.render_battle_result(outcome=None, lines=[None], rewards=[None], mon="nope",
                                scale=SCALE),
        UI.render_growth(None, before_level="a", scale=SCALE),
        UI.render_gotcha(None, scale=SCALE),
    ]
    for data in outs:
        assert data == b"" or data.startswith(b"\x89PNG")


# ══════════════════════════════════════════════════════════════════
# 传说图鉴:锁定隐藏名字 + 收服态与未收服态不同
# ══════════════════════════════════════════════════════════════════
def test_legendaries_locked_hides_name_and_level():
    """锁定行的名字 / 等级格子应变成「???」,与未锁定行明显不同。"""
    site = [SITES[0]]
    shown = UI.render_legendaries("关都", site, caught=["articuno"], badges=6, day=1,
                                  scale=SCALE)
    hidden = UI.render_legendaries("关都", site, locked=["articuno"], badges=1, day=1,
                                   scale=SCALE)
    # 名字格(第一个 row:x 34..92, y 20..34)
    name_cell_shown = _crop(shown, (34, 21, 92, 34))
    name_cell_hidden = _crop(hidden, (34, 21, 92, 34))
    diff = sum(
        abs(a - b)
        for a, b in zip(name_cell_shown.tobytes(), name_cell_hidden.tobytes(), strict=True)
    )
    assert diff > 20000, f"锁定行的名字格几乎没变化(diff={diff})"
    # 等级格(96..120)同理
    lv_shown = _crop(shown, (96, 21, 120, 34))
    lv_hidden = _crop(hidden, (96, 21, 120, 34))
    assert lv_shown.tobytes() != lv_hidden.tobytes()


def test_legendaries_caught_differs_from_uncaught():
    """已收服行(彩色精灵 + ●)必须与未收服行(剪影)不同。"""
    site = [SITES[0]]
    caught = UI.render_legendaries("关都", site, caught=["articuno"], badges=6, day=1,
                                   scale=SCALE)
    plain = UI.render_legendaries("关都", site, badges=6, day=1, scale=SCALE)
    assert caught != plain, "收服/未收服渲染完全相同"
    # 精灵槽(8..30, 21..34)颜色统计应不同:彩色图明显更鲜艳
    slot_caught = _crop(caught, (8, 21, 30, 34))
    slot_plain = _crop(plain, (8, 21, 30, 34))
    sat_caught = _saturation(slot_caught)
    sat_plain = _saturation(slot_plain)
    assert sat_caught > sat_plain + 5, (
        f"收服后精灵槽没有变彩色:{sat_caught:.1f} vs {sat_plain:.1f}"
    )


# ══════════════════════════════════════════════════════════════════
# 成长 / 进化:有进化时画面不同
# ══════════════════════════════════════════════════════════════════
def test_growth_evolution_differs_from_plain_levelup():
    plain = UI.render_growth(MON, before_level=20, after_level=21, learned=["咬碎"],
                             scale=SCALE)
    evo = UI.render_growth({"species": "gyarados", "name": "暴鲤龙", "level": 21},
                           before_level=20, after_level=21, learned=["咬碎"],
                           evolved_from_zh="鲤鱼王", evolved_to_zh="暴鲤龙", scale=SCALE)
    assert evo != plain, "进化画面与普通升级画面完全相同"
    assert evo.startswith(b"\x89PNG") and plain.startswith(b"\x89PNG")


def test_gotcha_has_spotlight_not_flat_background():
    """捕获画面应该是聚光灯 + 光芒,而不是纯背景色。"""
    data = UI.render_gotcha(MON, scale=SCALE)
    assert _colors(data) > 500
    # 左侧聚光灯区域内应出现亮色(接近白色)的高光
    spot = _crop(data, (40, 60, 120, 100))
    mean = ImageStat.Stat(spot).mean
    assert sum(mean) / 3 > 150, f"聚光灯不够亮:{mean}"
