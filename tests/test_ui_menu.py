"""五个菜单界面渲染测试(Pillow)。

运行:
    /mnt/e/astrbot_plugin_life_sim/.venv/bin/python -m pytest tests/test_ui_menu.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from io import BytesIO

from PIL import Image, ImageStat

from pw import ui_menu as UM

SCALE = 3

NODES = [
    {"key": "pallet-town", "zh": "真新镇", "kind": "town", "tier": 1,
     "next": ["kanto-route-1"], "hub": True},
    {"key": "kanto-route-1", "zh": "1号道路", "kind": "route", "tier": 1,
     "next": ["viridian-city"]},
    {"key": "viridian-city", "zh": "常磐市", "kind": "town", "tier": 1,
     "next": ["viridian-forest"]},
    {"key": "viridian-forest", "zh": "常磐森林", "kind": "area", "tier": 1,
     "next": ["pewter-city"]},
    {"key": "pewter-city", "zh": "深灰市", "kind": "town", "tier": 1,
     "next": ["kanto-route-3"]},
    {"key": "kanto-route-3", "zh": "3号道路", "kind": "route", "tier": 2,
     "next": ["cerulean-city"]},
    {"key": "cerulean-city", "zh": "华蓝市", "kind": "town", "tier": 2, "next": []},
]
GYMS = [
    {"order": 1, "location": "pewter-city", "leader": "小刚", "type": "Rock",
     "badge": "灰色徽章", "title": "深灰道馆"},
    {"order": 2, "location": "cerulean-city", "leader": "小霞", "type": "Water",
     "badge": "蓝色徽章", "title": "华蓝道馆"},
]
SHOP = [
    {"key": "potion", "zh": "伤药", "price": 300, "desc": "回复一只宝可梦 20 点 HP。",
     "kind": "medicine", "count": 3},
    {"key": "super-potion", "zh": "好伤药", "price": 700, "desc": "回复一只宝可梦 50 点 HP。",
     "kind": "medicine", "count": 0},
    {"key": "pokeball", "zh": "精灵球", "price": 200, "desc": "用来捕捉野生宝可梦的球。",
     "kind": "ball", "count": 12},
    {"key": "antidote", "zh": "解毒药", "price": 100, "desc": "解除中毒状态。",
     "kind": "status", "count": 1},
    {"key": "revive", "zh": "活力碎片", "price": 1500, "desc": "让濒死的宝可梦回复一半 HP。",
     "kind": "revive", "count": 0},
    {"key": "escape-rope", "zh": "脱洞绳", "price": 550, "desc": "从洞穴中立刻返回入口。",
     "kind": "items", "count": 2},
]
GYM = {
    "order": 1, "leader": "小刚", "leader_en": "Brock", "type": "Rock",
    "badge": "灰色徽章", "title": "深灰道馆",
    "team": [{"species": "geodude", "level": 12}, {"species": "onix", "level": 14}],
}
ELITE4 = [
    {"order": 1, "name": "科拿", "type": "Ice",
     "team": [{"species": "dewgong", "level": 52}, {"species": "cloyster", "level": 51}]},
    {"order": 2, "name": "希巴", "type": "Fighting",
     "team": [{"species": "onix", "level": 51}]},
    {"order": 3, "name": "菊子", "type": "Ghost",
     "team": [{"species": "gengar", "level": 54}]},
    {"order": 4, "name": "阿渡", "type": "Dragon",
     "team": [{"species": "dragonite", "level": 56}]},
]
CHAMPION = {"name": "青绿", "team": [{"species": "pidgeot", "level": 58}]}
STAGES = [
    {"key": "月见山", "title": "月见山的火箭队", "kind": "boss", "desc": "击退月见山的火箭队。"},
    {"key": "华蓝市", "title": "华蓝市的阴谋", "kind": "event", "desc": "调查华蓝市附近的火箭队据点。"},
    {"key": "黄金市", "title": "黄金市总部", "kind": "boss", "desc": "潜入火箭队总部。"},
    {"key": "紫苑镇", "title": "紫苑镇的幽灵", "kind": "event", "desc": "安抚紫苑镇宝可梦塔的幽灵。"},
]


def _check(data: bytes, scale: int = SCALE) -> None:
    """通用校验:非空 PNG、尺寸正确、颜色不单一(不是纯色空白图)。"""
    assert data, "渲染返回空字节"
    assert data.startswith(b"\x89PNG"), "不是 PNG 头"
    with Image.open(BytesIO(data)) as im:
        assert im.size == (240 * scale, 160 * scale), f"尺寸错误:{im.size}"
        colors = im.convert("RGB").getcolors(maxcolors=1 << 20) or []
    assert len(colors) > 300, f"颜色过少({len(colors)}),疑似空白图"


def test_render_map():
    data = UM.render_map(
        "关都地区", NODES, current="pewter-city",
        visited=["pallet-town", "kanto-route-1", "viridian-city", "viridian-forest"],
        gyms=GYMS, next_goal="穿过3号道路,前往华蓝市挑战小霞", region_order=1, scale=SCALE,
    )
    _check(data)


def test_render_shop():
    data = UM.render_shop(SHOP, money=4520, location_zh="深灰市", discount=0.8,
                          selected=1, scale=SCALE)
    _check(data)


def test_render_gym():
    data = UM.render_gym(GYM, region_zh="关都地区", location_zh="深灰市", owned=False,
                         scale=SCALE)
    _check(data)


def test_render_league():
    data = UM.render_league("关都地区", ELITE4, CHAMPION, done=["1", "2"],
                            champion_done=False, scale=SCALE)
    _check(data)


def test_render_story():
    data = UM.render_story("关都地区", "火箭队", "坂木", STAGES, done=["月见山"],
                           current_key="华蓝市", badges=2, total_gyms=8, scale=SCALE)
    _check(data)


def test_empty_inputs_still_render():
    """缺数据/空字符串也必须返回合法 PNG,绝不抛异常。"""
    _check(UM.render_map("", [], current="", visited=[], scale=SCALE))
    _check(UM.render_shop([], money=0, scale=SCALE))
    _check(UM.render_gym({}, scale=SCALE))
    _check(UM.render_league("", [], {}, scale=SCALE))
    _check(UM.render_story("", "", "", [], scale=SCALE))


def test_shop_selected_row_is_highlighted():
    """选中行必须有高亮底色,与未选中行明显不同。"""
    data = UM.render_shop(SHOP, money=4520, selected=1, scale=SCALE)
    # 无折扣时列表从 y=19 开始,行高 (111-19-2)/6 = 15
    row_h = 15.0
    top = 19 + 1

    def mean(y0):
        with Image.open(BytesIO(data)) as im:
            crop = im.convert("RGB").crop(
                (10 * SCALE, int((y0 + 3) * SCALE), 60 * SCALE, int((y0 + 11) * SCALE))
            )
            return tuple(ImageStat.Stat(crop).mean)

    selected = mean(top + 1 * row_h)
    other = mean(top + 0 * row_h)
    diff = sum(abs(selected[i] - other[i]) for i in range(3))
    assert diff > 30, f"选中行底色差异过小:{selected} vs {other}"


def test_story_current_row_is_highlighted():
    """主线当前章节行同样应被高亮。"""
    data = UM.render_story("关都地区", "火箭队", "坂木", STAGES, done=["月见山"],
                           current_key="华蓝市", scale=SCALE)

    def mean(y0):
        with Image.open(BytesIO(data)) as im:
            crop = im.convert("RGB").crop(
                (10 * SCALE, int((y0 + 2) * SCALE), 90 * SCALE, int((y0 + 11) * SCALE))
            )
            return tuple(ImageStat.Stat(crop).mean)

    # 列表从 y=21 开始,行高 14;第 2 行是当前章节
    selected = mean(21 + 1 * 14)
    other = mean(21 + 0 * 14)
    diff = sum(abs(selected[i] - other[i]) for i in range(3))
    assert diff > 20, f"当前章节高亮差异过小:{selected} vs {other}"
