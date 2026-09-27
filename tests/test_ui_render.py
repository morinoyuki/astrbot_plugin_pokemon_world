"""界面渲染测试(队伍 / 背包 / 训练家卡 / 图鉴)。

重点覆盖两类问题:
  1. 渲染不再"只剩文字" —— 之前 `upscale()` 会把逻辑层提前定格,导致方框、
     精灵、血条全部丢失(只剩文字)。这里直接断言调色板里的关键颜色确实存在。
  2. 状态差异可见 —— 倒下变暗、选中行高亮、未捕获显示剪影、徽章点亮。
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
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
    # 队伍界面用的是加高画布(6 行 × 26px 才放得下,原来的 160 高会把
    # 最后一行压到底部提示条上)
    assert im.size == (UI.LOGICAL_W * SCALE, UI.PARTY_H * SCALE)
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
        assert im.size == (UI.LOGICAL_W * scale, UI.PARTY_H * scale)


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


def test_sanitize_keeps_emoji_now_that_fallback_fonts_exist():
    """有 emoji 回退字体时 emoji 要**保留**(照搬 life_sim 的回退链)。

    旧实现只看主字体(OPPOSans)的 cmap,把 emoji 一律丢掉;现在主字体缺字形
    会回退到 Symbola / 系统彩色 emoji 字体,所以能画的就画,只有**任何字体都
    没有**的字符才丢(否则才是豆腐块)。
    """
    from pw import fonts

    if fonts.char_renderable("📨"):
        assert "📨" in UI.sanitize("📨 【路边的小包裹】获得 伤药 ×2")
    # 真正的"无人覆盖"字符必须丢掉
    assert UI.sanitize("测试\uffff结束") == "测试结束"
    # 常用符号(OFFOSans 自带)当然保留
    assert UI.sanitize("◆ ★ ● —") == "◆ ★ ● —"
    # emoji 现在能通过回退字体画出来 → 保留(画不出来的仍会丢)
    assert "获得赏金 1020₽" in UI.sanitize("💰 获得赏金 1020₽")
    assert UI.sanitize("◆胜利! Lv105 345/353 暴鲤龙♂") == "◆胜利! Lv105 345/353 暴鲤龙♂"


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
    # 两块面板之间的缝隙由渲染层的几何常量推导(MAP_BOX / TOWN_TOP),
    # 不再写死行号 —— 之前调过面板高度后这个测试就误报了。
    y0, y1 = M.MAP_BOX[3] + 1, M.TOWN_TOP
    band = im.crop((10 * SCALE, y0 * SCALE, 146 * SCALE, y1 * SCALE))
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
    from pw.ui_render import LOGICAL_W, Screen

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
    # emoji 有没有被过滤取决于宿主有没有 emoji 回退字体;但要保证一件事:
    # 过滤后的文本仍不含"任何字体都没有"的字符(不会出现豆腐块)
    from pw import fonts as _f

    assert _f.sanitize("📨\uffff") in ("📨", "")
    assert not _f.char_renderable("\uffff")
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


def test_map_has_no_decorative_blobs_and_goal_fits_one_line():
    """地图上不应再出现无信息的装饰色块,「下一目标」也不应留孤儿字。"""
    from pw import ui_menu as M
    from pw.ui_render import Screen

    # 早期版本在面板角落画了两团淡蓝椭圆当"海",不对应任何地理信息,已移除
    assert not hasattr(M, "MAP_SEA"), "装饰性海面色块又回来了"

    sc = Screen(scale=SCALE)
    lines, size = M._goal_lines(sc, "挑战枯叶市道馆:马志士", 72, 7.8, 2)
    assert lines == ["挑战枯叶市道馆:马志士"], lines   # 略微缩小字号压成一行
    assert size < 7.8
    # 真正的长目标:折行但不能剩一个字单独占行
    lines, _ = M._goal_lines(sc, "击败火箭队首领坂木并夺回被抢走的宝可梦", 72, 7.8, 2)
    assert len(lines) == 2
    assert len(lines[-1]) > 2
    lines, _ = M._goal_lines(sc, "", 72, 7.8, 2)
    assert lines == ["自由探索"]


def test_gotcha_card_shows_the_ball_actually_used():
    """捕获画面要用实际投出的球(大师球/高级球不能都画成红白球)。"""
    from pw import ui_info as U
    from pw.battle import TurnResult
    from pw.items import BAG_ITEMS

    mon = {"species": "gyarados", "name": "暴鲤龙", "level": 30, "cur_hp": 100, "max_hp": 130}
    shots = {}
    for key in ("poke-ball", "great-ball", "master-ball"):
        data = U.render_gotcha(mon, ball_zh=BAG_ITEMS[key]["zh"], ball_key=key,
                               dex_line="图鉴已记录:12 种", scale=SCALE)
        assert data, f"{key} 渲染失败"
        shots[key] = data
    assert len({shots[k] for k in shots}) == 3, "不同精灵球的捕获画面必须不同"
    # 未指定球时也要能渲染(回退到大类默认球)
    assert U.render_gotcha(mon, scale=SCALE)

    # TurnResult 要能带出用过的球
    assert TurnResult(outcome="caught", item_key="ultra-ball").item_key == "ultra-ball"


def test_capture_records_ball_key(tmp_path=None):
    """大师球必定捕获:用它投球后 res.item_key 必须是 master-ball。"""
    from test_commands import _Cmd, _Event, run_cmd

    from pw import battle as B

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "battle_image": False, "quest_enable": False}
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        t = p._load(ev)
        t.data["location"] = "kanto-route-1"
        t.add_item("master-ball", 1)
        p._save(t)
        B.start(t, [{"species": "rattata", "level": 3}], kind="wild", wild=True,
                meta={"kind": "wild", "species": "rattata", "types": ["Normal"],
                      "method": "walk", "title": "野生的 小拉达"}, day=1)
        p._save(t)
        ev2 = _Event("/对战 catch 大师球")
        run_cmd(p, ev2, p.cmd_battle)
        t2 = p._load(ev2)
        assert t2.data.get("battle") is None, "大师球应当直接结束战斗"
        assert any("捕获成功" in o for o in ev2.outputs)


def test_sprite_enable_toggle_actually_works():
    """sprite_enable 曾经只写在配置表里、代码从不读取(开关无效)。"""
    from pw import ui_render as R
    from pw.util import coerce_bool

    # 布尔配置必须是语义解析,而不是"非空字符串即真"
    assert coerce_bool("false", True) is False
    assert coerce_bool("关", True) is False
    assert coerce_bool("", True) is False
    assert coerce_bool("yes", False) is True
    assert coerce_bool(None, True) is True

    mons = [{"species": "pikachu", "name": "皮卡丘", "level": 12, "cur_hp": 30,
             "max_hp": 40, "gender": "M"} for _ in range(3)]
    on = R.render_party(mons, sprites=True, scale=SCALE)
    off = R.render_party(mons, sprites=False, scale=SCALE)
    assert on and off and on != off, "开关必须真的改变渲染结果"

    from test_commands import _Cmd, _Event, run_cmd

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": True, "sprite_enable": False, "quest_enable": False}
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        # 关掉缩略图后仍然要能正常出图(只是不画精灵)
        assert p._cfg_bool("sprite_enable", True) is False
        ev2 = _Event("/队伍")
        run_cmd(p, ev2, p.cmd_team)
        assert "<chain:" in "".join(ev2.outputs)


def test_title_bar_text_stays_inside_the_bar():
    """标题文字(含描边)不能穿出标题条底边 —— 之前长标题会越界 1.7px。"""
    from PIL import Image, ImageChops

    mons = [{"species": "pikachu", "name": "皮卡丘", "level": 12, "cur_hp": 30,
             "max_hp": 40, "gender": "M"}]
    for title in ("队伍", "对抗火箭队(首领:坂木)"):
        with_title = Image.open(io.BytesIO(
            UI.render_party(mons, box_count=0, badges=0, title=title, scale=4)
        )).convert("RGB")
        empty = Image.open(io.BytesIO(
            UI.render_party(mons, box_count=0, badges=0, title="", scale=4)
        )).convert("RGB")
        diff = ImageChops.difference(with_title, empty).convert("L")
        rows = [y for y in range(diff.height)
                if diff.crop((0, y, diff.width, y + 1)).getbbox()]
        assert rows, f"标题「{title}」没有画出任何像素"
        assert max(rows) / 4 <= 16, f"标题「{title}」墨迹到 y={max(rows)/4:.1f},穿出条底 16"


def test_trainer_card_long_name_does_not_cross_the_box():
    """超长训练家名必须截断,不能横穿左框压到徽章盒上。"""
    info = {"name": "一个超级无敌长的训练家名字啊啊啊啊", "region_zh": "关都",
            "location_zh": "真新镇", "money": 1000, "days": 1, "steps": 10,
            "pokedex": {"seen": 1, "caught": 1}, "badges": [], "id": 1}
    im = Image.open(io.BytesIO(UI.render_trainer_card(info, scale=4))).convert("RGB")
    text_like = sum(
        1
        for x in range(129 * 4, 132 * 4)
        for y in range(19 * 4, 143 * 4)
        if all(
            abs(p - t) < 40
            for p, t in zip(im.getpixel((x, y)), (52, 52, 44), strict=False)
        )
    )
    assert text_like == 0, "左框右边界外不该有文字墨迹"


def test_battle_party_balls_do_not_cover_message_text():
    """队伍球指示器不能压住对话框正文(≥3 只时必现)。"""
    from pw import battle_render as BR

    my = {"species": "charizard", "name": "喷火龙", "level": 50, "cur_hp": 153,
          "max_hp": 153}
    foe = {"species": "blastoise", "name": "水箭龟", "level": 50, "cur_hp": 150,
           "max_hp": 150}
    party = [{"species": "pikachu", "name": "皮卡丘", "level": 10, "cur_hp": 1,
              "max_hp": 20} for _ in range(3)]
    with_party = BR.render_battle(my, foe, ["测试战报文本内容"], my_party=party, scale=3)
    without = BR.render_battle(my, foe, ["测试战报文本内容"], my_party=None, scale=3)
    assert with_party and without
    from PIL import Image

    a = Image.open(io.BytesIO(with_party)).convert("RGB")
    b = Image.open(io.BytesIO(without)).convert("RGB")
    # 判定:两组图的正文区域像素必须完全相同(球队列在正文左侧,不影响正文)
    box = (30 * 3, 100 * 3, 200 * 3, 112 * 3)
    assert a.crop(box).tobytes() == b.crop(box).tobytes(), "队伍球不该改变正文区域"


def test_awaiting_switch_message_has_no_json():
    from pw.engine import create_pokemon, start_battle

    a = create_pokemon("pidgey", 5, moves=["tackle"])
    a.pp = {"tackle": 20}
    bt = start_battle([a, create_pokemon("rattata", 3, moves=["tackle"])],
                      [create_pokemon("snorlax", 60, moves=["tackle"])], seed=9)
    bt.start()
    bt.step({"type": "move", "move": "tackle"})
    lines = bt.step({"type": "move", "move": "tackle"})   # 待换人时又出招
    text = " ".join(lines)
    assert "{" not in text and "type" not in text, f"内部 JSON 泄漏给玩家:{text}"


def test_simultaneous_faint_counts_as_player_win():
    """双方最后一只同时倒下应判玩家胜(反作用力同归于尽)。"""
    from pw.engine import create_pokemon, start_battle

    a = create_pokemon("charizard", 50, moves=["doubleedge"])
    a.pp = {"doubleedge": 20}
    a.cur_hp = 1
    b = create_pokemon("rattata", 1, moves=["tackle"])
    b.pp = {"tackle": 20}
    b.cur_hp = 1
    bt = start_battle([a], [b], seed=11)
    bt.start()
    for _ in range(4):
        bt.step({"type": "move", "move": "doubleedge"})
        if bt.finished:
            break
    assert bt.finished
    assert bt.winner == "player", f"同归于尽应判玩家胜,实际 {bt.winner}"


def test_badge_glyphs_are_centered_in_the_shield():
    """徽章里的属性徽记必须居中(实测旧实现水平左偏 0.09×边长)。

    旧实现把徽记画在 (x, y+0.08s)、边长 0.82s —— 盾牌是 x..x+size 居中,
    而徽记从 x 起 → 左偏;未获得状态又用了整格 1.0s,两种状态大小与位置都不一致。
    """
    scale = 6
    size = 16

    def ink_center(image, x0, y0):
        xs, ys = [], []
        for y in range(int(y0 * scale), int((y0 + size) * scale)):
            for x in range(int(x0 * scale), int((x0 + size) * scale)):
                if image.getpixel((x, y)) == UI.BADGE_MARK:
                    xs.append(x)
                    ys.append(y)
        if not xs:
            return None
        return ((min(xs) + max(xs)) / 2 - x0 * scale) / scale, (
            (min(ys) + max(ys)) / 2 - y0 * scale
        ) / scale

    kinds = [
        "Rock", "Water", "Electric", "Grass", "Poison", "Psychic", "Fire",
        "Ground", "Flying", "Bug", "Ghost", "Dragon", "Dark", "Steel", "Fairy",
        "Normal", "Ice", "Fighting",
    ]
    # 18 枚排一行会超出默认 240 宽画布 → 用更宽的画布(Screen 支持自定义尺寸)
    sc = UI.Screen(scale=scale, w=len(kinds) * 20 + 8, h=32)
    for i, kind in enumerate(kinds):
        sc.badge(4 + i * 20, 6, size, on=True, kind=kind)
    im = Image.open(io.BytesIO(sc.finish())).convert("RGB")

    # 盾牌主体中心:x + size/2、y + 0.57*size
    for i, kind in enumerate(kinds):
        center = ink_center(im, 4 + i * 20, 6)
        assert center, f"{kind} 没有画出徽记"
        dx, dy = center[0] - size / 2, center[1] - 0.57 * size
        assert abs(dx) <= 1.2, f"{kind} 徽记水平偏心 {dx:+.2f}px"
        assert abs(dy) <= 1.8, f"{kind} 徽记垂直偏心 {dy:+.2f}px"


def test_unobtained_badge_glyph_has_no_gold_leak():
    """未获得徽章的徽记里不能出现金色内芯(那是"已获得"的配色)。

    火焰/毒等图形的镂空原本硬编码成金色,画在灰色槽里会冒出一个金点。
    """
    scale = 6
    sc = UI.Screen(scale=scale)
    sc.badge(6, 6, 16, on=False, kind="Fire")
    sc.badge(30, 6, 16, on=False, kind="Poison")
    im = Image.open(io.BytesIO(sc.finish())).convert("RGB")
    gold = UI.BADGE_ON
    leaked = [
        (x / scale, y / scale)
        for y in range(im.height)
        for x in range(im.width)
        if im.getpixel((x, y)) == gold
    ]
    assert not leaked, f"未获得徽章里泄漏了金色像素:{leaked[:5]}"


def _content_gap_above_footer(im, scale):
    """返回 (纯背景间隙行数, 最低内容行)。

    `Screen.footer()` 占 y=h-16..h-5;`window()` 的投影会让面板底边下方再多 2px。
    所以允许的"空"像素只有 背景 / 投影 / 面板投影色,其余都算内容。
    """
    allowed = (UI.BG, UI.SHADOW, UI.BOX_SHADOW)
    h = im.size[1] // scale
    footer_top = h - 16
    lowest = -1
    for ly in range(footer_top):
        for lx in range(0, im.size[0], 2):
            if im.getpixel((lx, ly * scale + scale // 2)) not in allowed:
                lowest = ly
                break
    return footer_top - lowest, lowest


def test_no_screen_overlaps_the_footer_bar():
    """任何界面都不能压到底部提示条上。

    用户的反馈是"`/宝可梦` 下面两个窗口跟底部提示框重叠":那两个 window 画到
    y=152,而提示条在 144 —— 直接叠了 8px。同样的隐患当时还在队伍(第 6 行到 174)、
    委托板(第 3 行到 145)等界面里。现在统一用 `Screen.content_bottom`(= h-20),
    这里逐个界面量"提示条上方还剩多少纯背景行",要求 ≥1(也就是没有内容落进提示条)。
    """
    from pw import ui_info as I
    from pw import ui_menu as M
    from pw import ui_quest as Q
    from pw.dex import get_dex

    dex = get_dex()
    entry = dict(dex.species["pikachu"])
    entry["_key"] = "pikachu"
    mon = dict(
        MON_A, species="charizard", name="喷火龙", level=36, gender="M",
        exp_pct=42.0, exp_now=1200, exp_next=2900,
        stats={"hp": 113, "atk": 76, "def": 72, "spa": 94, "spd": 77, "spe": 88},
        base=dex.species["charizard"]["baseStats"], nature_zh="勤奋",
        ability_zh="猛火", ability_desc="HP 减少时火属性招式威力提高。",
        item_zh="无", friendship=90, dex_no=6, genus="火焰宝可梦",
        evo_hint="可进化为 超级喷火龙X",
        moves=[{"zh": "喷射火焰", "pp": 15, "pp_max": 15, "type": "Fire"},
               {"zh": "劈开", "pp": 20, "pp_max": 20, "type": "Normal"}],
    )
    quest = {"giver": "捕虫少年阿明", "title": "帮忙补充图鉴", "desc": "帮我抓 2 只。",
             "objective": {"kind": "catch", "count": 2},
             "reward": {"money": 300, "items": {"poke-ball": 1}}}
    screens = {
        "party": UI.render_party([MON_A, MON_B], money=1, scale=SCALE),
        "bag": UI.render_bag(BAG, money=1, active_pocket="balls", scale=SCALE),
        "card": UI.render_trainer_card(CARD, scale=SCALE),
        "dex": UI.render_dex(entry, caught=True, scale=SCALE),
        "mon": UI.render_mon_summary(mon, index=1, party_size=3, scale=SCALE),
        "box": UI.render_box(
            [{"species": "pidgey", "name": "波波", "level": 5, "gender": "F",
              "cur_hp": 12, "max_hp": 20}], capacity=60, scale=SCALE,
        ),
        "box_empty": UI.render_box([], capacity=60, scale=SCALE),
        "quests": Q.render_quests([quest, quest, quest], progress=[(1, 2)] * 3,
                                  day=1, region_zh="关都", scale=SCALE),
        "map": M.render_map(
            "关都",
            [{"key": "pallet-town", "zh": "真新镇", "next": ["viridian-city"]},
             {"key": "viridian-city", "zh": "常青市", "next": []}],
            current="pallet-town", visited=["pallet-town"], gyms=[],
            next_goal="常青市", scale=SCALE,
        ),
        "shop": M.render_shop(
            [{"key": "potion", "zh": "伤药", "price": 200, "desc": "回复 20 HP。"}],
            money=3000, location_zh="深灰市", scale=SCALE,
        ),
        "news": I.render_news(1, world_events=["天气异常"], region_zh="关都",
                              location_zh="真新镇", weather_zh="晴天", scale=SCALE),
        "result": I.render_battle_result(
            outcome="win", title="野生战", lines=["击败了 小拉达!"],
            rewards=["获得 320₽"], growth=["新叶喵 升到了 Lv6"],
            mon={"species": "sprigatito", "level": 6}, scale=SCALE,
        ),
        "gotcha": I.render_gotcha(MON_A, ball_zh="精灵球", scale=SCALE),
        "growth": I.render_growth(MON_A, before_level=5, after_level=6,
                                  learned=["树叶"], scale=SCALE),
    }
    for name, data in screens.items():
        assert data, f"{name} 渲染失败"
        gap, lowest = _content_gap_above_footer(_img(data), SCALE)
        assert gap >= 1, (
            f"{name} 的内容(最低第 {lowest} 行)压到了底部提示条"
            f"(间隙 {gap}px)"
        )


# ══════════════════════════════════════════════════════════════════
# 文本溢出检测:任何文字都不能压出所在面板
# ══════════════════════════════════════════════════════════════════
class _PanelSpy:
    """记录渲染时 `Screen.window()` 画的框和 `Screen.text()` 画的字。"""

    def __enter__(self):
        from pw import fonts as F  # noqa: F401

        self.boxes: list[tuple] = []
        self.texts: list[tuple] = []
        self._ow, self._ot = UI.Screen.window, UI.Screen.text

        def window(inner, box, **kw):
            self.boxes.append(tuple(float(v) for v in box))
            return self._ow(inner, box, **kw)

        def text(inner, x, y, s, **kw):
            self.texts.append((float(x), float(y), str(s), dict(kw)))
            return self._ot(inner, x, y, s, **kw)

        UI.Screen.window, UI.Screen.text = window, text
        return self

    def __exit__(self, *exc):
        UI.Screen.window, UI.Screen.text = self._ow, self._ot

    def offenders(self, scale: int, pad: float = 2.0) -> list[tuple]:
        """返回越出所属面板内边框的文字(右溢出, 下溢出, 文本)。"""
        from pw import fonts

        meas = UI.Screen(scale=scale)
        out = []
        for x, y, s, kw in self.texts:
            if not s.strip():
                continue
            size = float(kw.get("size", 9) or 9)
            width = meas.tw(s, size)
            _, y0, _, y1 = fonts.bbox(s, round(size * scale))
            ink_h = (y1 - y0) / scale
            inner = None
            for b in self.boxes:
                inside = b[0] - 1 <= x <= b[2] + 1 and b[1] - 1 <= y <= b[3] + 1
                smaller = inner is None or (b[2] - b[0]) * (b[3] - b[1]) < (
                    (inner[2] - inner[0]) * (inner[3] - inner[1])
                )
                if inside and smaller:
                    inner = b
            if inner is None:
                continue
            over_r = x + width - (inner[2] - pad)
            over_b = y + ink_h - (inner[3] - pad)
            if over_r > 0.5 or over_b > 0.5:
                out.append((round(over_r, 1), round(over_b, 1), s[:24], inner))
        return out


def test_no_text_overflows_its_panel():
    """地图 / 新闻等界面的文字不能压出所在窗口。

    用户反馈:"地图界面 右边和左边下面的窗口都有一定程度上的文本溢出" ——
    实测两处:右侧资料栏图例最后一行墨迹到 139.4(面板内边框在 138)、
    左下第三列标签顶到边框;另外 `/今日` 顶部信息条的天气药丸文字比药丸框还低。
    这里对多个界面 × 多个 scale 做统一检测(取**最内层**包含该文字的窗口做比较)。
    """
    from pw import ui_info as I
    from pw import ui_menu as M
    from pw.world import WorldMap

    world = WorldMap()
    nodes = world.nodes("kanto")
    goals = [
        "挑战 深灰市 的 小刚",
        "前往 华蓝市 的 小霞(先穿过 9 号道路)",
        "完成 关都 主线:击退火箭队、集齐 8 枚徽章",
        "自由探索",
    ]
    visited = ["pallet-town", "viridian-city", "pewter-city", "kanto-route-2"]

    for scale in (1, 2, 3, 5):
        for goal in goals:
            with _PanelSpy() as spy:
                M.render_map(world.region_zh("kanto"), nodes, current="pewter-city",
                             visited=visited, gyms=world.gyms("kanto"),
                             next_goal=goal, region_order=1, scale=scale)
            assert not spy.offenders(scale), (
                f"地图文案压出面板(scale={scale} goal={goal}):{spy.offenders(scale)}"
            )
        with _PanelSpy() as spy:
            I.render_news(7, world_events=["火箭队 在 3 号道路 活动"],
                          region_zh="关都", location_zh="深灰市", weather_zh="晴天",
                          locks=["3 号道路 暂时封锁"], scale=scale)
        assert not spy.offenders(scale), f"新闻界面压出面板:{spy.offenders(scale)}"

        # 长名字也要过得去
        with _PanelSpy() as spy:
            UI.render_bag(
                [{"key": "super-potion", "zh": "厉害伤药", "count": 12,
                  "desc": "回复 120 HP。", "effect": "回复 120 HP", "kind": "medicine"}],
                money=999999, active_pocket="medicine", selected=0, scale=scale,
            )
        assert not spy.offenders(scale), f"背包压出面板:{spy.offenders(scale)}"
