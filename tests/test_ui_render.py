"""界面渲染测试(队伍 / 背包 / 训练家卡 / 图鉴)。

重点覆盖两类问题:
  1. 渲染不再"只剩文字" —— 之前 `upscale()` 会把逻辑层提前定格,导致方框、
     精灵、血条全部丢失(只剩文字)。这里直接断言调色板里的关键颜色确实存在。
  2. 状态差异可见 —— 倒下变暗、选中行高亮、未捕获显示剪影、徽章点亮。
"""

from __future__ import annotations

import os
import sys
from io import BytesIO

import pytest
from PIL import Image, ImageStat

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pw import ui_render as UI  # noqa: E402

SCALE = 2


def _img(data: bytes) -> Image.Image:
    assert data, "渲染返回了空字节"
    assert data.startswith(b"\x89PNG"), "不是 PNG"
    return Image.open(BytesIO(data)).convert("RGB")


def _colors(im: Image.Image) -> set:
    return set(im.getcolors(maxcolors=1 << 20) or [])


def _has_near(im: Image.Image, target, tol: int = 26) -> bool:
    tr, tg, tb = target
    for _count, rgb in _colors(im):
        r, g, b = rgb[:3]
        if abs(r - tr) <= tol and abs(g - tg) <= tol and abs(b - tb) <= tol:
            return True
    return False


def _mean(im: Image.Image) -> float:
    return sum(ImageStat.Stat(im).mean) / 3


MON_A = {"species": "gyarados", "name": "暴鲤龙", "level": 105, "cur_hp": 345,
         "max_hp": 353, "gender": "F", "exp_pct": 38.0, "item": "leftovers"}
MON_B = {"species": "pikachu", "name": "皮卡丘", "level": 32, "cur_hp": 64,
         "max_hp": 88, "gender": "M", "status": "par", "exp_pct": 10.0}
MON_FAINT = {"species": "snorlax", "name": "卡比兽", "level": 40, "cur_hp": 0,
             "max_hp": 200, "gender": "M", "status": "slp"}

BAG = [
    {"key": "poke-ball", "zh": "精灵球", "count": 5, "desc": "捕获宝可梦的基本球。",
     "kind": "ball"},
    {"key": "great-ball", "zh": "超级球", "count": 2, "desc": "比精灵球更容易捕获。",
     "kind": "ball"},
    {"key": "potion", "zh": "伤药", "count": 3, "desc": "回复 20 点 HP。",
     "kind": "medicine"},
]

CARD = {
    "name": "小智", "id_no": "12345", "money": 12400, "region": "关都",
    "location": "华蓝市", "play_day": 12, "steps": 8600, "party": 5, "box": 7,
    "seen": 42, "caught": 31,
    "badges": [("灰色", True), ("蓝色", True), ("橙色", True)] + [("", False)] * 5,
    "story_progress": "月见山的火箭队:火箭队在月见山抢夺化石。",
    "best": "◆ /帮助",
}


def test_party_screen_draws_shapes_not_only_text():
    """回归:方框/血条/精灵必须真的画出来(丢失图形的老 bug)。"""
    data = UI.render_party([MON_A, MON_B, MON_FAINT], money=12400, box_count=7,
                           badges=3, scale=SCALE)
    im = _img(data)
    assert im.size == (UI.LOGICAL_W * SCALE, UI.LOGICAL_H * SCALE)
    assert len(_colors(im)) > 300, f"颜色过少,疑似空白:{len(_colors(im))}"
    assert _has_near(im, UI.BOX_EDGE), "窗口描边没画出来"
    assert _has_near(im, UI.HP_TRACK), "血条没画出来"
    assert _has_near(im, UI.TITLE_BG), "标题条没画出来"


def test_party_fainted_mon_is_dimmed_and_status_chip_shown():
    alive = _img(UI.render_party([MON_A], scale=SCALE))
    fainted = _img(UI.render_party([MON_FAINT], scale=SCALE))
    assert _mean(fainted) < _mean(alive), "倒下的宝可梦应当变暗"
    # 异常状态徽章颜色(brn/par/slp)应出现在图上
    with_par = _img(UI.render_party([MON_B], scale=SCALE))
    assert _has_near(with_par, UI.STATUS_STYLE["par"][1], tol=40), "异常徽章没画出来"


def test_party_empty_slots_render():
    data = UI.render_party([], scale=SCALE)
    im = _img(data)
    assert len(_colors(im)) > 60


def test_bag_screen_tabs_and_selection():
    data = UI.render_bag(BAG, money=12400, active_pocket="balls", selected=1,
                         scale=SCALE)
    im = _img(data)
    assert im.size == (UI.LOGICAL_W * SCALE, UI.LOGICAL_H * SCALE)
    assert _has_near(im, UI.SEL_BG, tol=12), "选中行高亮没画出来"
    assert _has_near(im, UI.POCKET_ICON["balls"], tol=20), "当前口袋页签没高亮"
    assert _has_near(im, UI.HP_TAG_BG, tol=40), "道具图标/标签没画出来"


def test_bag_empty_and_unknown_pocket():
    assert _img(UI.render_bag([], money=0, active_pocket="items", scale=SCALE))
    assert _img(UI.render_bag(BAG, active_pocket="不存在的口袋", scale=SCALE))


def test_trainer_card_badge_states_differ():
    on = _img(UI.render_trainer_card(CARD, scale=SCALE))
    card_off = dict(CARD)
    card_off["badges"] = [("", False)] * 8
    off = _img(UI.render_trainer_card(card_off, scale=SCALE))
    assert _has_near(on, UI.BADGE_ON, tol=30), "点亮的徽章没画出来"
    assert _mean(on) > _mean(off), "空徽章盒应当更暗"
    assert len(_colors(on)) > 300


def test_dex_hides_unseen_and_shows_silhouette():
    from pw.dex import get_dex

    dex = get_dex()
    entry = dict(dex.species["gyarados"])
    entry["_key"] = "gyarados"
    caught = _img(UI.render_dex(entry, caught=True,
                                locations=[{"zh": "19号水路", "min": 37, "max": 42}],
                                scale=SCALE))
    unseen = _img(UI.render_dex(entry, caught=False, seen=False, scale=SCALE))
    seen = _img(UI.render_dex(entry, caught=False, seen=True, scale=SCALE))
    assert len(_colors(caught)) > 300
    # 未见过 → 剪影:精灵区域明显更暗
    box = (5 * SCALE, 19 * SCALE, 112 * SCALE, 112 * SCALE)
    assert _mean(unseen.crop(box)) < _mean(caught.crop(box)) - 8, "未捕获应显示黑色剪影"
    assert caught != seen, "已捕获与仅见到的图标应不同"


def test_all_screens_render_with_minimal_data():
    """极端数据也不应该崩:空字典/空列表。"""
    for data in (
        UI.render_party([{}], scale=SCALE),
        UI.render_bag([{}], scale=SCALE),
        UI.render_trainer_card({}, scale=SCALE),
        UI.render_dex({}, scale=SCALE),
    ):
        assert _img(data)


def test_available_and_scale_variants():
    assert UI.available() is True
    for scale in (1, 2, 4):
        im = _img(UI.render_party([MON_A], scale=scale))
        assert im.size == (UI.LOGICAL_W * scale, UI.LOGICAL_H * scale)


def test_glyphs_used_are_present_in_font():
    """界面里用到的特殊字符必须在字体里,否则会渲染成豆腐块。"""
    from fontTools.ttLib import TTFont

    font = os.path.join(_ROOT, "pw", "static", "fonts", "OPPOSans-Regular.ttf")
    if not os.path.exists(font):
        pytest.skip("字体缺失")
    cmap = TTFont(font).getBestCmap()
    used = "◆★●○◎×↑↓—·"
    missing = [c for c in used if ord(c) not in cmap]
    assert not missing, f"字体缺少这些字形(会显示豆腐块):{missing}"
    for bad in "▶◈◐≡⚔":
        assert ord(bad) not in cmap, f"{bad} 其实字体里有,可更新白名单"


def test_no_ink_touches_canvas_edge():
    """所有界面都不能画出背景之外。

    曾经的 bug:底部提示文字从 y 起画,墨迹会到 y+9,框底又贴着画布下边,
    于是文字越过背景边界被裁掉。现在 `Screen` 会按真实墨迹范围钳制,
    这里断言最外一圈(2px)必须全是背景色。
    """
    from pw.dex import get_dex

    dex = get_dex()
    entry = dict(dex.species["pikachu"])
    entry["_key"] = "pikachu"
    screens = {
        "party": UI.render_party([MON_A, MON_B], money=1, scale=SCALE),
        "bag": UI.render_bag(BAG, money=1, active_pocket="balls", scale=SCALE),
        "card": UI.render_trainer_card(CARD, scale=SCALE),
        "dex": UI.render_dex(entry, caught=True, scale=SCALE),
    }
    for name, data in screens.items():
        im = _img(data)
        w, h = im.size
        edge = set()
        for x in range(w):
            for y in (0, 1, h - 2, h - 1):
                edge.add(im.getpixel((x, y)))
        for y in range(h):
            for x in (0, 1, w - 2, w - 1):
                edge.add(im.getpixel((x, y)))
        assert edge == {UI.BG}, f"{name} 的边缘有非背景像素:{list(edge)[:4]}"


def test_sanitize_drops_emoji_to_avoid_tofu():
    """游戏文本里的 emoji 必须被剔除,否则界面上是豆腐块。"""
    assert UI.sanitize("📨 【路边的小包裹】获得 伤药 ×2") == " 【路边的小包裹】获得 伤药 ×2"
    assert UI.sanitize("💰 获得赏金 1020₽") == " 获得赏金 1020₽"
    assert UI.sanitize("◆胜利! Lv105 345/353 暴鲤龙♂") == "◆胜利! Lv105 345/353 暴鲤龙♂"
    assert "🏅" not in UI.sanitize("🏅 获得「灰色徽章」!")


def test_battle_message_box_grows_with_lines():
    """对战对话框高度必须自适应:长文本不能顶破边框。

    曾经的 bug:框固定 112~158(内高 38px),却按 4 行 × 10px = 40px 画,
    第 4 行会溢出到底部边框之外。
    """
    from pw import battle_render as BR

    long_log = [
        "暴鲤龙 使用了 龙之舞!暴鲤龙的攻击大幅提升了!",
        "对手 喵喵 使用了 抓!效果不太好……",
        "暴鲤龙 使用了 水流喷射!效果绝佳!喵喵 倒下了!",
        "暴鲤龙 获得了 384 点经验值!",
    ]
    my = {"species": "gyarados", "name": "暴鲤龙", "level": 105, "cur_hp": 300,
          "max_hp": 353, "exp_pct": 40}
    foe = {"species": "meowth", "name": "喵喵", "level": 20, "cur_hp": 30, "max_hp": 52}
    data = BR.render_battle(my, foe, long_log,
                            title="世界大赛 · 决赛 —— 地区冠军 科拿 的冰之军团",
                            scale=SCALE)
    im = _img(data)
    w, h = im.size
    # 对话框底边之上、之下各留的边距里不能出现文字色(白字/描边)
    assert BR.MSG_BOTTOM <= BR.LOGICAL_H - 1
    bottom_band = im.crop((0, (BR.MSG_BOTTOM + 1) * SCALE, w, h))
    ink = {
        px
        for _c, px in (bottom_band.getcolors(maxcolors=1 << 20) or [])
        if abs(px[0] - 250) < 20 and abs(px[1] - 250) < 20 and abs(px[2] - 248) < 20
    }
    assert not ink, "对话框下方出现了文字像素(说明文字溢出了边框)"
    # 行数上限必须与框高自洽
    assert BR.MSG_MAX_LINES >= 3
    assert (BR.MSG_BOTTOM - 4 - BR.MSG_TOP_MAX) // BR.MSG_LINE_H >= 3


def test_map_last_row_does_not_overflow_panel():
    """地图最后一行的节点名不能越过下面的"地点"框。"""
    from pw import ui_menu as M
    from pw.world import WorldMap

    world = WorldMap()
    nodes = [{**v, "key": k} for k, v in world.nodes("kanto").items()]
    data = M.render_map("关都", nodes, current="vermilion-city",
                        visited=["pallet-town"], gyms=world.gyms("kanto"),
                        next_goal="挑战枯叶市道馆:马志士", scale=SCALE)
    im = _img(data)
    # 地图面板 MAP=(5,19,150,108),地点框 TOWN=(5,111,150,144)。
    # 检查整段 [106,111]:节点标签(含最低一行)绝不能碰到/越过面板底边 108。
    band = im.crop((10 * SCALE, 106 * SCALE, 146 * SCALE, 112 * SCALE))
    text_like = 0
    for _c, px in (band.getcolors(maxcolors=1 << 20) or []):
        r, g, b = px[:3]
        if (abs(r - 52) < 13 and abs(g - 52) < 13 and abs(b - 44) < 13) or (
            abs(r - 120) < 13 and abs(g - 118) < 13 and abs(b - 100) < 13
        ):
            text_like += 1
    assert text_like == 0, (
        "地图与「地点」框之间的缝隙里出现了文字像素(地图最后一行溢出)"
    )


def test_clamp_text_handles_stroke_and_overwide_text():
    """文字钳制必须算上描边宽度,并且超宽文字要截断而不是画到画布外。"""
    from pw.ui_render import LOGICAL_W, Screen, sanitize

    sc = Screen(scale=SCALE)
    # 1) 超宽右对齐文字 + 描边:曾在最右一列留下墨迹
    long_zh = "这是一段远远超过画布宽度的超长文本" * 3
    sc.title_bar("标题", right=long_zh)
    sc.text(0, 150, long_zh, size=9, stroke=1.0, sfill=(0, 0, 0))
    # 2) 超宽的左对齐文字
    sc.text(-50, 60, long_zh, size=12)
    im = _img(sc.finish())
    edges = set()
    for x in range(im.width):
        edges.add(im.getpixel((x, 0)))
        edges.add(im.getpixel((x, im.height - 1)))
    for y in range(im.height):
        edges.add(im.getpixel((0, y)))
        edges.add(im.getpixel((im.width - 1, y)))
    bg = {(246, 242, 214)}
    assert edges <= bg, f"钳制后仍有墨迹贴到画布边缘:{sorted(edges - bg)[:5]}"
    assert sanitize("📨") == ""  # emoji 仍被过滤
    assert LOGICAL_W == 240


def test_battle_result_reward_box_is_adaptive():
    """奖励/成长框要随文本行数自适应,长奖励不能压进下一块。"""
    from pw import ui_info as U

    data = U.render_battle_result(
        outcome="win",
        title="世界大赛 · 决赛",
        lines=["你击败了 世界冠军 丹帝!全场观众起立鼓掌,这是属于你的时代!"],
        rewards=["获得 18000₽", "获得 大师球 ×1", "获得 神奇糖果 ×3",
                 "获得 「世界冠军」称号与冠军披风"],
        growth=["暴鲤龙 升到了 Lv100!", "皮卡丘 想学习 「伏特攻击」,但已经学会 4 个招式了。"],
        mon={"species": "gyarados", "name": "暴鲤龙", "level": 100, "cur_hp": 310,
             "max_hp": 353, "gender": "F"},
        scale=SCALE,
    )
    im = _img(data)
    # 底部提示条上沿 = 144:其上方不能出现正文墨迹
    band = im.crop((0, 142 * SCALE, im.width, 144 * SCALE))
    text_like = 0
    for _c, px in (band.getcolors(maxcolors=1 << 20) or []):
        r, g, b = px[:3]
        if abs(r - 52) < 13 and abs(g - 52) < 13 and abs(b - 44) < 13:
            text_like += 1
    assert text_like == 0, "奖励/成长文本越过了底部提示条上沿"
    # 被截断的文本要带省略号,而不是留半个词(直接针对折行工具验证)
    from pw.ui_info import _wrap_fit
    from pw.ui_render import Screen

    wsc = Screen(scale=SCALE)
    wrapped = _wrap_fit(wsc, "很长很长的奖励文本" * 4, 40, 7.6, 2)
    assert len(wrapped) == 2
    assert wrapped[-1].endswith("…"), wrapped
    assert "很长很长" not in wrapped[-1].replace(wrapped[-1][:2], "")[:0]
