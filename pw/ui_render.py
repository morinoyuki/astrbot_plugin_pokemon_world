"""GBA 风格宝可梦界面工具箱 + 常用界面(队伍 / 背包 / 训练家卡 / 图鉴)。

设计沿用 `battle_render.py` 的两层做法:
  · **图形在 240×160 逻辑画布上画**,最后 `NEAREST` 放大 → 像素质感;
  · **文字在放大层画**,坐标仍用逻辑像素 → 中文清晰可读。

对外主要是 `Screen` 类(组件化),以及各 `render_*` 界面函数。
任何异常都返回 `b""`,调用方回退纯文本。
"""

from __future__ import annotations

import os
from io import BytesIO

from astrbot.api import logger

from . import fonts
from .dex import get_dex
from .items import KIND_ZH
from .sprites import back_sprite_path, sprite_path

LOGICAL_W = 240
LOGICAL_H = 160
SCALE_DEFAULT = 3

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts")
FONT_MAIN = os.path.join(FONT_DIR, "OPPOSans-Regular.ttf")
FONT_SYMBOL = os.path.join(FONT_DIR, "Symbola_hint.ttf")

# ── FRLG/RSE 调色板 ──────────────────────────────────────────────
BG = (246, 242, 214)
BOX_FILL = (250, 249, 229)
BOX_EDGE = (58, 74, 58)
BOX_HI = (152, 172, 140)
BOX_SHADOW = (188, 184, 146)
TITLE_BG = (72, 104, 88)
TITLE_HI = (128, 176, 144)
TITLE_FG = (250, 250, 245)
TEXT = (52, 52, 44)
TEXT_DIM = (120, 118, 100)
SEL_BG = (176, 224, 152)
SEL_EDGE = (96, 152, 88)

HP_TRACK = (150, 46, 38)
HP_TAG_BG = (168, 48, 40)
HP_TAG_FG = (250, 224, 96)
HP_OK = (86, 208, 88)
HP_MID = (240, 200, 48)
HP_LOW = (240, 88, 56)
EXP_TRACK = (86, 92, 110)
EXP_FILL = (86, 202, 236)
MALE = (64, 120, 244)
FEMALE = (248, 96, 160)
BADGE_ON = (232, 184, 64)
BADGE_ON_HI = (252, 228, 140)
# ── 徽章里的属性徽记 ──────────────────────────────────────────────
# 每个函数在 (x, y) 起、边长 s 的方框内用给定颜色作画。形状尽量简单但可区分,
# 让 8 枚徽章不再长得一模一样(道馆面板 / 训练家卡 / 联盟都会用到)。
def _bg_rock(d, x, y, s, c, hole):
    d.polygon([(x + s * 0.5, y + s * 0.15), (x + s * 0.92, y + s * 0.85),
               (x + s * 0.08, y + s * 0.85)], fill=c)


def _bg_water(d, x, y, s, c, hole):
    d.polygon([(x + s * 0.5, y + s * 0.08), (x + s * 0.88, y + s * 0.6),
               (x + s * 0.12, y + s * 0.6)], fill=c)
    d.ellipse([x + s * 0.12, y + s * 0.42, x + s * 0.88, y + s * 0.92], fill=c)


def _bg_electric(d, x, y, s, c, hole):
    d.polygon([(x + s * 0.58, y + s * 0.06), (x + s * 0.2, y + s * 0.55),
               (x + s * 0.48, y + s * 0.55), (x + s * 0.38, y + s * 0.94),
               (x + s * 0.8, y + s * 0.42), (x + s * 0.5, y + s * 0.42)], fill=c)


def _bg_fire(d, x, y, s, c, hole):
    d.polygon([(x + s * 0.5, y + s * 0.06), (x + s * 0.86, y + s * 0.62),
               (x + s * 0.5, y + s * 0.95), (x + s * 0.14, y + s * 0.62)], fill=c)
    d.ellipse([x + s * 0.34, y + s * 0.56, x + s * 0.66, y + s * 0.9], fill=hole)


def _bg_grass(d, x, y, s, c, hole):
    d.polygon([(x + s * 0.5, y + s * 0.08), (x + s * 0.9, y + s * 0.5),
               (x + s * 0.5, y + s * 0.92), (x + s * 0.1, y + s * 0.5)], fill=c)


def _bg_ice(d, x, y, s, c, hole):
    for ang in ((0, 1), (1, 0), (1, 1), (1, -1)):
        d.line([x + s * (0.5 - ang[0] * 0.4), y + s * (0.5 - ang[1] * 0.4),
                x + s * (0.5 + ang[0] * 0.4), y + s * (0.5 + ang[1] * 0.4)],
               fill=c, width=2)


def _bg_fighting(d, x, y, s, c, hole):
    d.rounded_rectangle([x + s * 0.16, y + s * 0.3, x + s * 0.84, y + s * 0.88],
                        radius=2, fill=c)
    for i in range(3):
        d.rectangle([x + s * (0.22 + i * 0.2), y + s * 0.14,
                     x + s * (0.36 + i * 0.2), y + s * 0.34], fill=c)


def _bg_poison(d, x, y, s, c, hole):
    d.ellipse([x + s * 0.16, y + s * 0.2, x + s * 0.84, y + s * 0.88], fill=c)
    d.ellipse([x + s * 0.4, y + s * 0.46, x + s * 0.6, y + s * 0.66], fill=hole)


def _bg_ground(d, x, y, s, c, hole):
    _bg_rock(d, x, y, s, c, hole)
    d.rectangle([x + s * 0.1, y + s * 0.86, x + s * 0.9, y + s * 0.96], fill=c)


def _bg_flying(d, x, y, s, c, hole):
    d.line([x + s * 0.08, y + s * 0.5, x + s * 0.5, y + s * 0.16,
            x + s * 0.92, y + s * 0.5], fill=c, width=2)
    d.line([x + s * 0.24, y + s * 0.78, x + s * 0.5, y + s * 0.52,
            x + s * 0.76, y + s * 0.78], fill=c, width=2)


def _bg_psychic(d, x, y, s, c, hole):
    d.ellipse([x + s * 0.08, y + s * 0.3, x + s * 0.92, y + s * 0.72], fill=c)
    d.ellipse([x + s * 0.4, y + s * 0.42, x + s * 0.6, y + s * 0.62], fill=hole)


def _bg_bug(d, x, y, s, c, hole):
    d.ellipse([x + s * 0.2, y + s * 0.16, x + s * 0.8, y + s * 0.5], fill=c)
    for i in range(3):
        d.line([x + s * 0.5, y + s * 0.44, x + s * (0.14 + i * 0.36), y + s * 0.9],
               fill=c, width=2)


def _bg_ghost(d, x, y, s, c, hole):
    d.pieslice([x + s * 0.14, y + s * 0.14, x + s * 0.86, y + s * 0.8],
               180, 360, fill=c)
    d.rectangle([x + s * 0.14, y + s * 0.46, x + s * 0.86, y + s * 0.8], fill=c)
    for i in range(2):
        d.ellipse([x + s * (0.3 + i * 0.26), y + s * 0.38,
                   x + s * (0.44 + i * 0.26), y + s * 0.52], fill=hole)


def _bg_dragon(d, x, y, s, c, hole):
    d.polygon([(x + s * 0.1, y + s * 0.2), (x + s * 0.9, y + s * 0.2),
               (x + s * 0.5, y + s * 0.92)], fill=c)


def _bg_dark(d, x, y, s, c, hole):
    # 月牙是"两圆相减":剩下的墨迹天然落在左半边(包围盒中心偏左 ~0.13s)。
    # 把两个圆整体右移,让月牙的**墨迹**在方框里居中(实测偏移 < 0.05s)。
    d.ellipse([x + s * 0.34, y + s * 0.16, x + s * 1.06, y + s * 0.88], fill=c)
    d.ellipse([x + s * 0.62, y + s * 0.06, x + s * 1.34, y + s * 0.78], fill=hole)


def _bg_steel(d, x, y, s, c, hole):
    d.rounded_rectangle([x + s * 0.12, y + s * 0.34, x + s * 0.88, y + s * 0.66],
                        radius=1, fill=c)
    for i in range(3):
        d.line([x + s * (0.28 + i * 0.22), y + s * 0.16,
                x + s * (0.28 + i * 0.22), y + s * 0.84], fill=c, width=2)


def _bg_fairy(d, x, y, s, c, hole):
    d.polygon([(x + s * 0.5, y + s * 0.08), (x + s * 0.62, y + s * 0.42),
               (x + s * 0.94, y + s * 0.5), (x + s * 0.62, y + s * 0.58),
               (x + s * 0.5, y + s * 0.92), (x + s * 0.38, y + s * 0.58),
               (x + s * 0.06, y + s * 0.5), (x + s * 0.38, y + s * 0.42)], fill=c)


def _bg_normal(d, x, y, s, c, hole):
    d.ellipse([x + s * 0.2, y + s * 0.2, x + s * 0.8, y + s * 0.8], fill=c)


_BADGE_GLYPHS = {
    "Rock": _bg_rock, "Water": _bg_water, "Electric": _bg_electric,
    "Fire": _bg_fire, "Grass": _bg_grass, "Ice": _bg_ice,
    "Fighting": _bg_fighting, "Poison": _bg_poison, "Ground": _bg_ground,
    "Flying": _bg_flying, "Psychic": _bg_psychic, "Bug": _bg_bug,
    "Ghost": _bg_ghost, "Dragon": _bg_dragon, "Dark": _bg_dark,
    "Steel": _bg_steel, "Fairy": _bg_fairy, "Normal": _bg_normal,
}

BADGE_MARK = (124, 86, 26)   # 徽章内的属性徽记(深金褐)
BADGE_OFF = (176, 172, 148)
SHADOW = (172, 168, 130)
MSG_FRAME = (162, 44, 34)
MSG_FILL = (108, 178, 162)
MSG_TEXT = (250, 250, 248)
MSG_SHADOW = (46, 92, 82)

STATUS_STYLE = {
    "brn": ("灼", (224, 96, 48)),
    "par": ("麻", (232, 192, 48)),
    "psn": ("毒", (168, 88, 200)),
    "tox": ("剧", (140, 60, 180)),
    "slp": ("眠", (120, 124, 148)),
    "frz": ("冰", (96, 196, 232)),
}
# 背包口袋(页签)
POCKETS = [
    ("items", "道具"),
    ("balls", "精灵球"),
    ("medicine", "回复"),
    ("tm", "招式机"),
    ("key", "重要"),
]
KIND_TO_POCKET = {
    "ball": "balls",
    "medicine": "medicine",
    "status": "medicine",
    "revive": "medicine",
    "pp": "medicine",
    "berry": "medicine",
    "battle": "items",
    "rare": "items",
    "stone": "items",
    "evo": "items",
    "held": "items",
    "tm": "tm",
}
POCKET_ICON = {
    "items": (198, 168, 112),
    "balls": (224, 64, 56),
    "medicine": (120, 200, 168),
    "tm": (140, 150, 220),
    "key": (232, 192, 72),
}
TYPE_COLOR = {
    "Normal": (168, 168, 144),
    "Fire": (240, 128, 48),
    "Water": (104, 144, 240),
    "Grass": (120, 200, 80),
    "Electric": (248, 208, 48),
    "Ice": (152, 216, 216),
    "Fighting": (192, 48, 40),
    "Poison": (160, 64, 160),
    "Ground": (224, 192, 104),
    "Flying": (168, 144, 240),
    "Psychic": (248, 88, 136),
    "Bug": (168, 184, 32),
    "Rock": (184, 160, 56),
    "Ghost": (112, 88, 152),
    "Dragon": (112, 96, 240),
    "Dark": (112, 88, 72),
    "Steel": (184, 184, 208),
    "Fairy": (238, 153, 172),
}


def _font(size: int):
    """主字体(带完整回退链,见 pw/fonts.py)。"""
    return fonts.load_font(max(6, int(size)))


def sanitize(text) -> str:
    """去掉**任何字体都没有**的字符(避免豆腐块)。

    注意与旧实现的区别:旧版只看主字体(OPPOSans)的 cmap,emoji 一律被丢掉;
    现在主字体缺字形时会回退到符号/emoji 字体(Symbola、系统彩色 emoji),
    只有所有字体都没有的字符才丢 —— 逻辑照搬 life_sim。
    """
    return fonts.sanitize(text)


def available() -> bool:
    return fonts.available()


def type_color(t: str) -> tuple[int, int, int]:
    return TYPE_COLOR.get(str(t or ""), (168, 168, 144))


def gender_symbol(gender: str) -> tuple[str, tuple[int, int, int]]:
    if gender == "M":
        return "♂", MALE
    if gender == "F":
        return "♀", FEMALE
    return "", TEXT


# ══════════════════════════════════════════════════════════════════
# 道具图标:按道具 key 绘制,退化到大类
#
# 约定:所有绘制函数签名统一为 ``(d, x, y, s)``,并且**严格**把墨迹限制在
# ``[x, x + s - 1] × [y, y + s - 1]`` 内(调用方靠这个方框排布,越界会串行)。
# 全部使用整数友好的图元(rectangle / ellipse / polygon / pieslice / line),
# 平涂色块 + ``BOX_EDGE`` 描边,不做渐变与抗锯齿。
# ══════════════════════════════════════════════════════════════════
_WHITE = (250, 250, 245)
_METAL = (176, 180, 196)
_CAP = (158, 160, 172)
_GOLD = (248, 216, 96)


def _rect(d, x, y, s, color, *, outline=BOX_EDGE):
    d.rectangle([x, y, x + s - 1, y + s - 1], fill=color, outline=outline)


def _panel(color=(198, 168, 112)):
    def draw(d, x, y, s):
        d.rounded_rectangle([x + 1, y + 1, x + s - 2, y + s - 2], radius=2, fill=color,
                            outline=BOX_EDGE)
    return draw


def _plus(d, cx, cy, r, color):
    if r <= 0:
        d.rectangle([cx, cy, cx, cy], fill=color)
        return
    d.rectangle([cx - r, cy - r + 1, cx + r, cy + r - 1], fill=color)
    d.rectangle([cx - r + 1, cy - r, cx + r - 1, cy + r], fill=color)


# ── 精灵球:球体 + 顶部配色 + 花纹 ──
def _ball(top, emblem=None):
    """画精灵球:底部白色 + 顶部配色 + 下半部标志符号。

    9px 下细节线条会糊,所以花纹只用一个 2~3 像素的**大色块符号**,
    并且统一画在下半部(上半部留给顶色),保证各种球一眼能分开。
    """
    def draw(d, x, y, s):
        m = s // 2
        d.ellipse([x, y, x + s - 1, y + s - 1], fill=_WHITE, outline=BOX_EDGE)
        d.pieslice([x, y, x + s - 1, y + s - 1], 180, 360, fill=top)
        d.ellipse([x, y, x + s - 1, y + s - 1], outline=BOX_EDGE)
        d.rectangle([x, y + m - 1, x + s - 1, y + m], fill=BOX_EDGE)
        if emblem:
            emblem(d, x, y, s)
        else:
            d.ellipse([x + m - 1, y + m - 1, x + m + 1, y + m + 1], fill=_WHITE,
                      outline=BOX_EDGE)
    return draw


def _em_great():
    """超级球:蓝色顶 + 两侧红色标记。"""
    def draw(d, x, y, s):
        red = (224, 64, 56)
        d.polygon([(x + 1, y + 4), (x + 3, y + 6), (x + 1, y + 8)], fill=red,
                  outline=BOX_EDGE)
        d.polygon([(x + s - 2, y + 4), (x + s - 4, y + 6), (x + s - 2, y + 8)],
                  fill=red, outline=BOX_EDGE)
    return draw


def _em_ultra():
    """高级球:黄色顶 + 黑色横竖条纹。"""
    def draw(d, x, y, s):
        dark = (44, 44, 52)
        d.rectangle([x + 1, y + 4, x + 2, y + 8], fill=dark, outline=BOX_EDGE)
        d.rectangle([x + s - 3, y + 4, x + s - 2, y + 8], fill=dark, outline=BOX_EDGE)
        d.rectangle([x + 2, y + 5, x + s - 3, y + 6], fill=dark)
    return draw


def _em_master():
    """大师球:紫色顶 + 金色 M。"""
    def draw(d, x, y, s):
        d.line([(x + 2, y + 8), (x + 2, y + 4), (x + 4, y + 6), (x + 6, y + 4),
                (x + 6, y + 8)], fill=_GOLD, width=2)
    return draw


def _em_premier():
    """纪念球:全白 + 红色顶带。"""
    def draw(d, x, y, s):
        d.rectangle([x + 1, y + 1, x + s - 2, y + 2], fill=(224, 64, 56))
    return draw


def _em_plus(color):
    """治愈球:中央十字。"""
    def draw(d, x, y, s):
        _plus(d, x + s // 2, y + s // 2 + 1, 2, color)
    return draw


def _em_net():
    """捕网球:下半部渔网纹。"""
    def draw(d, x, y, s):
        dark = (20, 80, 112)
        for i in (1, 2):
            d.line([x + 1, y + 4 + i * 2, x + s - 2, y + 4 + i * 2], fill=dark)
            d.line([x + 1 + i * 2, y + 4, x + 1 + i * 2, y + s - 1], fill=dark)
    return draw


def _em_moon():
    """黑暗球:下半部紫色月牙。"""
    def draw(d, x, y, s):
        d.polygon([(x + 5, y + 4), (x + 3, y + 5), (x + 3, y + 7), (x + 5, y + 8),
                   (x + 4, y + 6)], fill=(184, 136, 232), outline=BOX_EDGE)
    return draw


def _em_bolt():
    """先机球:下半部蓝色闪电。"""
    def draw(d, x, y, s):
        d.polygon([(x + 5, y + 3), (x + 2, y + 6), (x + 4, y + 6), (x + 3, y + 8),
                   (x + 6, y + 5), (x + 4, y + 5)], fill=(56, 88, 200),
                  outline=BOX_EDGE)
    return draw


def _em_clock():
    """计时球:下半部钟表。"""
    def draw(d, x, y, s):
        d.ellipse([x + 2, y + 3, x + 6, y + 7], fill=_WHITE, outline=BOX_EDGE)
        d.line([x + 4, y + 5, x + 4, y + 3], fill=BOX_EDGE)
        d.line([x + 4, y + 5, x + 5, y + 6], fill=BOX_EDGE)
    return draw


def _em_ring():
    """重复球:中央红圈。"""
    def draw(d, x, y, s):
        d.ellipse([x + 2, y + 3, x + 6, y + 8], fill=_WHITE, outline=(224, 48, 48))
        d.rectangle([x + 4, y + 5, x + 4, y + 5], fill=(224, 48, 48))
    return draw


def _em_spots():
    """巢穴球:下半部深绿斑点。"""
    def draw(d, x, y, s):
        dark = (48, 120, 56)
        for px, py in ((2, 5), (3, 7), (6, 5), (5, 7)):
            d.rectangle([x + px, y + py, x + px, y + py], fill=dark)
    return draw


def _em_v():
    """等级球:下半部 V 形箭头。"""
    def draw(d, x, y, s):
        d.polygon([(x + 1, y + 4), (x + 4, y + 7), (x + 7, y + 4), (x + 7, y + 6),
                   (x + 4, y + 8), (x + 1, y + 6)], fill=(248, 208, 72),
                  outline=BOX_EDGE)
    return draw


def _em_iron():
    """沉重球:铁质横条 + 竖向铆条。"""
    def draw(d, x, y, s):
        iron = (88, 88, 98)
        d.rectangle([x + 1, y + 5, x + s - 2, y + 7], fill=iron, outline=BOX_EDGE)
        d.rectangle([x + 4, y + 3, x + 4, y + 8], fill=iron)
    return draw


def _em_gem():
    """究极球:金顶 + 蓝宝石。"""
    def draw(d, x, y, s):
        d.polygon([(x + 4, y + 3), (x + 7, y + 5), (x + 4, y + 8), (x + 1, y + 5)],
                  fill=(64, 128, 224), outline=BOX_EDGE)
    return draw


# ── 瓶子:药品 / 状态药 / PP 药 ──
def _bottle(color, *, cross=None, cap=_CAP, marks=0):
    def draw(d, x, y, s):
        c = s // 2
        d.rectangle([x + c - 1, y, x + c + 1, y + s // 4], fill=cap, outline=BOX_EDGE)
        d.rounded_rectangle([x + 1, y + s // 4, x + s - 2, y + s - 1], radius=2,
                            fill=color, outline=BOX_EDGE)
        if cross:
            _plus(d, x + c, y + s // 2 + 1, max(1, s // 5), cross)
        for i in range(marks):
            d.rectangle([x + 2 + i * 2, y + s - 4, x + 2 + i * 2, y + s - 3],
                        fill=_WHITE)
    return draw


def _rainbow_bottle():
    def draw(d, x, y, s):
        c = s // 2
        d.rectangle([x + c - 1, y, x + c + 1, y + s // 4], fill=_CAP, outline=BOX_EDGE)
        bands = [(224, 96, 96), (248, 200, 88), (120, 200, 120), (120, 168, 240)]
        for i, col in enumerate(bands):
            d.rectangle([x + 1, y + s // 4 + i, x + s - 2, y + s // 4 + i], fill=col)
        d.rounded_rectangle([x + 1, y + s // 4, x + s - 2, y + s - 1], radius=2,
                            outline=BOX_EDGE)
        _plus(d, x + c, y + s // 2 + 1, 2, _WHITE)
    return draw


def _flask(color, cap=_CAP):
    def draw(d, x, y, s):
        c = s // 2
        d.rectangle([x + c - 1, y, x + c + 1, y + 3], fill=cap, outline=BOX_EDGE)
        d.ellipse([x + 1, y + 2, x + s - 2, y + s - 1], fill=color, outline=BOX_EDGE)
        d.line([x + 2, y + 4, x + 3, y + 3], fill=_WHITE)
    return draw


def _spray(color=(140, 196, 224)):
    def draw(d, x, y, s):
        c = s // 2
        d.rectangle([x + c - 1, y, x + c + 1, y + 2], fill=_CAP, outline=BOX_EDGE)
        d.rounded_rectangle([x + 1, y + 2, x + s - 2, y + s - 1], radius=2, fill=color,
                            outline=BOX_EDGE)
        for dx in (2, 4, 6):
            d.rectangle([x + dx, y + 1, x + dx, y + 1], fill=_WHITE)
    return draw


# ── 石头 / 树果 / 宝石 ──
def _stone(color, hi=_WHITE):
    def draw(d, x, y, s):
        e = s - 1
        d.polygon([(x, y + e), (x + s // 4, y + 1), (x + s // 2, y + s // 3),
                   (x + s - 3, y + 1), (x + e, y + e)], fill=color, outline=BOX_EDGE)
        d.line([x + 2, y + e - 2, x + s // 3, y + 2], fill=hi)
    return draw


def _oval_stone(color=(208, 200, 176)):
    def draw(d, x, y, s):
        d.ellipse([x + 1, y + 1, x + s - 2, y + s - 2], fill=color, outline=BOX_EDGE)
        d.line([x + 3, y + 4, x + 4, y + 2], fill=_WHITE)
    return draw


def _berry(color, leaf=(96, 152, 72)):
    def draw(d, x, y, s):
        m = s // 2
        d.polygon([(x + m, y + 2), (x + s - 2, y), (x + m + 1, y + 1)], fill=leaf,
                  outline=BOX_EDGE)
        d.ellipse([x + 1, y + 1, x + s - 2, y + s - 1], fill=color, outline=BOX_EDGE)
        d.rectangle([x + 2, y + 3, x + 3, y + 4], fill=_WHITE)
    return draw


def _gem(color):
    def draw(d, x, y, s):
        m = s // 2
        d.polygon([(x + m, y), (x + s - 1, y + m), (x + m, y + s - 1), (x, y + m)],
                  fill=color, outline=BOX_EDGE)
        d.line([x + m - 1, y + 1, x + m, y + 1], fill=_WHITE)
    return draw


def _drop(color):
    def draw(d, x, y, s):
        m = s // 2
        d.polygon([(x + m, y), (x + s - 1, y + s - 3), (x + m, y + s - 1),
                   (x, y + s - 3)], fill=color, outline=BOX_EDGE)
    return draw


def _seed(color, spot=(96, 72, 48)):
    def draw(d, x, y, s):
        d.ellipse([x + 2, y + 1, x + s - 2, y + s - 1], fill=color, outline=BOX_EDGE)
        d.ellipse([x + s // 2 - 1, y + 3, x + s // 2 + 1, y + 5], fill=spot)
    return draw


def _ice(color=(152, 216, 236)):
    def draw(d, x, y, s):
        d.rectangle([x + 1, y + 2, x + s - 2, y + s - 1], fill=color, outline=BOX_EDGE)
        d.line([x + 2, y + 3, x + s // 2, y + 3], fill=_WHITE)
        d.line([x + 2, y + 3, x + 2, y + s - 2], fill=_WHITE)
    return draw


def _sand(color=(232, 208, 152)):
    def draw(d, x, y, s):
        d.polygon([(x, y + s - 1), (x + 2, y + s // 2), (x + s // 2, y + 1),
                   (x + s - 2, y + s // 2), (x + s - 1, y + s - 1)], fill=color,
                  outline=BOX_EDGE)
        d.rectangle([x + 3, y + s - 5, x + 4, y + s - 5], fill=_WHITE)
    return draw


def _sludge():
    def draw(d, x, y, s):
        d.polygon([(x, y + s - 1), (x + 1, y + s // 2), (x + s // 2, y + 1),
                   (x + s - 2, y + s // 2), (x + s - 1, y + s - 1)],
                  fill=(120, 88, 152), outline=BOX_EDGE)
        d.rectangle([x + 3, y + 4, x + 3, y + 4], fill=(196, 168, 224))
    return draw


def _target(color):
    def draw(d, x, y, s):
        d.ellipse([x, y, x + s - 1, y + s - 1], outline=color)
        d.ellipse([x + 2, y + 2, x + s - 3, y + s - 3], outline=color)
        d.rectangle([x + s // 2, y + s // 2 - 1, x + s // 2, y + s // 2 + 1], fill=color)
    return draw


def _orb(color, core=None):
    def draw(d, x, y, s):
        d.ellipse([x + 1, y + 1, x + s - 2, y + s - 2], fill=color, outline=BOX_EDGE)
        if core:
            d.ellipse([x + 3, y + 3, x + s - 4, y + s - 4], fill=core)
    return draw


# ── 持有道具 / 装备 ──
def _band(color, *, knot=False):
    def draw(d, x, y, s):
        d.rounded_rectangle([x, y + s // 3, x + s - 1, y + s // 3 + 3], radius=1,
                            fill=color, outline=BOX_EDGE)
        if knot:
            d.polygon([(x + 1, y + 1), (x + 4, y + 2), (x + 2, y + 5)], fill=color,
                      outline=BOX_EDGE)
    return draw


def _glasses(color):
    def draw(d, x, y, s):
        d.ellipse([x + 1, y + 2, x + s // 2 - 1, y + s - 3], fill=color, outline=BOX_EDGE)
        d.ellipse([x + s // 2 + 1, y + 2, x + s - 2, y + s - 3], fill=color,
                  outline=BOX_EDGE)
        d.line([x + s // 2 - 1, y + 4, x + s // 2 + 1, y + 4], fill=BOX_EDGE)
    return draw


def _cloth(color):
    def draw(d, x, y, s):
        d.polygon([(x, y + 2), (x + s // 3, y), (x + s - 1, y + 2), (x + s - 1, y + s - 3),
                   (x + 2 * s // 3, y + s - 1), (x, y + s - 3)], fill=color,
                  outline=BOX_EDGE)
        d.line([x + 2, y + 3, x + s - 3, y + 3], fill=_WHITE)
    return draw


def _armor(color):
    def draw(d, x, y, s):
        m = s // 2
        d.polygon([(x + m, y), (x + s - 1, y + 2), (x + s - 3, y + s - 1),
                   (x + 2, y + s - 1), (x, y + 2)], fill=color, outline=BOX_EDGE)
        d.line([x + m, y + 2, x + m, y + s - 3], fill=_WHITE)
    return draw


def _vest(color=(200, 96, 72)):
    def draw(d, x, y, s):
        d.polygon([(x, y + 1), (x + 3, y), (x + s // 2, y + 2), (x + s - 4, y),
                   (x + s - 1, y + 1), (x + s - 2, y + s - 1), (x + 1, y + s - 1)],
                  fill=color, outline=BOX_EDGE)
        d.line([x + s // 2, y + 2, x + s // 2, y + s - 2], fill=_WHITE)
    return draw


def _boots(color=(148, 104, 72)):
    def draw(d, x, y, s):
        d.rectangle([x + 2, y, x + 5, y + s - 3], fill=color, outline=BOX_EDGE)
        d.rectangle([x + 2, y + s - 4, x + s - 1, y + s - 1], fill=color, outline=BOX_EDGE)
    return draw


def _helmet(color=(168, 176, 196)):
    def draw(d, x, y, s):
        d.pieslice([x, y + 1, x + s - 1, y + s - 2], 180, 360, fill=color, outline=BOX_EDGE)
        d.rectangle([x, y + s // 2, x + s - 1, y + s // 2 + 1], fill=BOX_EDGE)
        d.line([x + s // 2, y + 2, x + s // 2, y + s // 2], fill=BOX_EDGE)
    return draw


def _goggles(color=(120, 176, 224)):
    def draw(d, x, y, s):
        d.rounded_rectangle([x, y + 2, x + s - 1, y + s - 3], radius=2, fill=color,
                            outline=BOX_EDGE)
        d.ellipse([x + 1, y + 3, x + s // 2 - 1, y + s - 4], fill=(230, 240, 250),
                  outline=BOX_EDGE)
        d.ellipse([x + s // 2 + 1, y + 3, x + s - 2, y + s - 4], fill=(230, 240, 250),
                  outline=BOX_EDGE)
    return draw


def _clay(color=(200, 156, 112)):
    def draw(d, x, y, s):
        d.rounded_rectangle([x + 1, y + 2, x + s - 2, y + s - 1], radius=2, fill=color,
                            outline=BOX_EDGE)
        d.line([x + 2, y + 3, x + s - 3, y + 3], fill=(240, 216, 184))
    return draw


def _balloon(color=(240, 96, 112)):
    def draw(d, x, y, s):
        d.ellipse([x + 1, y, x + s - 2, y + s - 3], fill=color, outline=BOX_EDGE)
        d.line([x + s // 2, y + s - 3, x + s // 2, y + s - 1], fill=BOX_EDGE)
    return draw


def _dice():
    def draw(d, x, y, s):
        d.rounded_rectangle([x + 1, y + 1, x + s - 2, y + s - 2], radius=2, fill=_WHITE,
                            outline=BOX_EDGE)
        for px, py in ((2, 2), (5, 2), (2, 5), (5, 5), (3, 3)):
            d.rectangle([x + px, y + py, x + px, y + py], fill=BOX_EDGE)
    return draw


def _food():
    def draw(d, x, y, s):
        d.ellipse([x + 1, y + 1, x + s - 2, y + s - 4], fill=(240, 208, 128),
                  outline=BOX_EDGE)
        d.rectangle([x + 1, y + s - 4, x + s - 2, y + s - 1], fill=(224, 168, 96),
                    outline=BOX_EDGE)
    return draw


def _tag(color):
    def draw(d, x, y, s):
        d.polygon([(x, y), (x + s - 3, y), (x + s - 1, y + s // 2), (x + s - 3, y + s - 1),
                   (x, y + s - 1)], fill=color, outline=BOX_EDGE)
        d.rectangle([x + 2, y + 3, x + 3, y + 4], fill=_WHITE)
    return draw


def _sheet(color=_METAL):
    def draw(d, x, y, s):
        d.polygon([(x + 1, y + 3), (x + s - 3, y), (x + s - 1, y + s - 3),
                   (x + 3, y + s - 1)], fill=color, outline=BOX_EDGE)
        d.line([x + 3, y + 3, x + s - 4, y + 2], fill=_WHITE)
    return draw


def _ingot(color=(168, 176, 196)):
    def draw(d, x, y, s):
        d.polygon([(x + 2, y + 1), (x + s - 3, y + 1), (x + s - 1, y + s - 2),
                   (x, y + s - 2)], fill=color, outline=BOX_EDGE)
        d.line([x + 2, y + 2, x + s - 4, y + 2], fill=_WHITE)
    return draw


def _tooth(color=_WHITE):
    def draw(d, x, y, s):
        d.polygon([(x + 1, y + 1), (x + s - 2, y + 1), (x + s // 2, y + s - 1)],
                  fill=color, outline=BOX_EDGE)
    return draw


def _claw(color=_METAL):
    def draw(d, x, y, s):
        for i, ox in enumerate((0, 3, 6)):
            d.polygon([(x + ox + 1, y + 1 + i), (x + ox + 2, y + s - 1),
                       (x + ox, y + s - 2)], fill=color, outline=BOX_EDGE)
    return draw


def _scale(color):
    def draw(d, x, y, s):
        d.polygon([(x, y + s // 3), (x + s // 2, y), (x + s - 1, y + s // 3),
                   (x + s // 2, y + s - 1)], fill=color, outline=BOX_EDGE)
        d.line([x + s // 2 - 2, y + 2, x + s // 2 + 2, y + 2], fill=_WHITE)
    return draw


def _prism():
    def draw(d, x, y, s):
        bands = [(232, 96, 96), (248, 200, 80), (120, 200, 120), (120, 168, 240)]
        for i, col in enumerate(bands):
            d.polygon([(x + i * 2, y + s - 2), (x + 1 + i * 2, y + 1),
                       (x + 2 + i * 2, y + s - 2)], fill=col, outline=BOX_EDGE)
    return draw


def _crown_rock(color=(168, 160, 148)):
    def draw(d, x, y, s):
        d.polygon([(x, y + 3), (x + s // 4, y + 1), (x + s // 2, y + 4),
                   (x + s - 4, y + 1), (x + s - 1, y + 3), (x + s - 2, y + s - 1),
                   (x + 1, y + s - 1)], fill=color, outline=BOX_EDGE)
        d.polygon([(x + 2, y + s - 3), (x + s // 2, y + s - 6), (x + s - 3, y + s - 3)],
                  fill=(240, 200, 72), outline=BOX_EDGE)
    return draw


def _plug(color=(240, 208, 72)):
    def draw(d, x, y, s):
        d.rounded_rectangle([x + 2, y + 1, x + s - 3, y + s - 3], radius=2, fill=color,
                            outline=BOX_EDGE)
        d.rectangle([x + s // 2 - 1, y + s - 3, x + s // 2 + 1, y + s - 1], fill=_CAP,
                    outline=BOX_EDGE)
        d.line([(x + s // 2 - 2, y + 2), (x + s // 2, y + 4), (x + s // 2 + 2, y + 2)],
               fill=BOX_EDGE)
    return draw


def _pot(color=(176, 120, 88)):
    def draw(d, x, y, s):
        d.rounded_rectangle([x + 1, y + 3, x + s - 3, y + s - 1], radius=2, fill=color,
                            outline=BOX_EDGE)
        d.rectangle([x + s - 3, y + 4, x + s - 1, y + 6], fill=color, outline=BOX_EDGE)
        d.line([x + 3, y + 5, x + s - 5, y + 5], fill=(232, 200, 168))
    return draw


def _cup(color=(244, 244, 240)):
    def draw(d, x, y, s):
        d.rounded_rectangle([x + 1, y + 3, x + s - 3, y + s - 1], radius=1, fill=color,
                            outline=BOX_EDGE)
        d.arc([x + s - 4, y + 4, x + s - 1, y + s - 3], 270, 90, fill=BOX_EDGE)
        d.line([x + 2, y + 4, x + s - 4, y + 4], fill=(200, 168, 120))
    return draw


def _cream():
    def draw(d, x, y, s):
        d.rounded_rectangle([x + 1, y + s // 2, x + s - 2, y + s - 1], radius=2,
                            fill=(248, 208, 168), outline=BOX_EDGE)
        d.ellipse([x + 2, y + 2, x + s - 3, y + s // 2 + 2], fill=_WHITE, outline=BOX_EDGE)
        d.ellipse([x + s // 3, y + 1, x + s - 3, y + s // 3 + 2], fill=(248, 176, 196),
                  outline=BOX_EDGE)
    return draw


def _chip(color=(96, 168, 200)):
    def draw(d, x, y, s):
        d.rounded_rectangle([x + 1, y + 1, x + s - 2, y + s - 2], radius=1, fill=color,
                            outline=BOX_EDGE)
        for i in range(3):
            d.line([x, y + 2 + i * 2, x + 1, y + 2 + i * 2], fill=BOX_EDGE)
            d.line([x + s - 2, y + 2 + i * 2, x + s - 1, y + 2 + i * 2], fill=BOX_EDGE)
    return draw


def _feather(color):
    def draw(d, x, y, s):
        d.polygon([(x + 1, y + s - 1), (x + 3, y + 2), (x + s - 2, y),
                   (x + s // 2, y + s - 2)], fill=color, outline=BOX_EDGE)
        d.line([x + 2, y + s - 2, x + s - 3, y + 2], fill=_WHITE)
    return draw


def _magnet():
    def draw(d, x, y, s):
        red = (216, 72, 64)
        d.pieslice([x + 1, y + 1, x + s - 2, y + s - 1], 180, 360, fill=red,
                   outline=BOX_EDGE)
        d.rectangle([x + 1, y + s // 2, x + s // 2 - 2, y + s - 1], fill=red,
                    outline=BOX_EDGE)
        d.rectangle([x + s // 2 + 1, y + s // 2, x + s - 2, y + s - 1], fill=red,
                    outline=BOX_EDGE)
        d.rectangle([x + 1, y + s - 4, x + s // 2 - 2, y + s - 1], fill=_WHITE,
                    outline=BOX_EDGE)
        d.rectangle([x + s // 2 + 1, y + s - 4, x + s - 2, y + s - 1], fill=_WHITE,
                    outline=BOX_EDGE)
    return draw


def _spoon(color=_METAL):
    def draw(d, x, y, s):
        m = s // 2
        d.ellipse([x + 2, y + 1, x + s - 3, y + 4], fill=color, outline=BOX_EDGE)
        d.rectangle([x + m - 1, y + 4, x + m, y + s - 1], fill=color, outline=BOX_EDGE)
    return draw


def _pouch(color):
    def draw(d, x, y, s):
        d.polygon([(x + 2, y), (x + s - 3, y), (x + s - 1, y + s - 1), (x, y + s - 1)],
                  fill=color, outline=BOX_EDGE)
        d.line([x + 1, y + 2, x + s - 2, y + 2], fill=_WHITE)
    return draw


def _apple(color, leaf=(96, 168, 72)):
    def draw(d, x, y, s):
        d.polygon([(x + s // 2, y + 2), (x + s // 2 + 2, y)], fill=leaf, outline=BOX_EDGE)
        d.ellipse([x + 1, y + 1, x + s - 2, y + s - 1], fill=color, outline=BOX_EDGE)
        d.rectangle([x + 2, y + 3, x + 2, y + 4], fill=_WHITE)
    return draw


def _heart(color):
    def draw(d, x, y, s):
        r = max(1, s // 4)
        d.ellipse([x + 1, y + 1, x + 2 * r - 1, y + 2 * r], fill=color, outline=BOX_EDGE)
        d.ellipse([x + s - 2 * r, y + 1, x + s - 2, y + 2 * r], fill=color,
                  outline=BOX_EDGE)
        d.polygon([(x, y + 2 * r - 2), (x + s - 1, y + 2 * r - 2), (x + s // 2, y + s - 1)],
                  fill=color, outline=BOX_EDGE)
    return draw


# ── 战斗强化 / 稀有道具 ──
def _stat_up(color, icon=None):
    def draw(d, x, y, s):
        d.rounded_rectangle([x, y, x + s - 1, y + s - 1], radius=2, fill=color,
                            outline=BOX_EDGE)
        c = s // 2
        if icon == "shield":
            d.polygon([(x + c, y + 1), (x + s - 2, y + 3), (x + c, y + s - 2),
                       (x + 1, y + 3)], fill=_WHITE)
        elif icon == "spark":
            d.line([(x + c, y + 3), (x + s // 3, y + c), (x + c, y + c)], fill=_WHITE)
            d.line([(x + c, y + 3), (x + 2 * s // 3, y + c)], fill=_WHITE)
        elif icon == "double":
            d.line([(x + 2, y + c), (x + c, y + 2), (x + s - 2, y + c)], fill=_WHITE)
            d.line([(x + 2, y + s - 3), (x + c, y + c - 1), (x + s - 2, y + s - 3)],
                   fill=_WHITE)
        else:
            d.polygon([(x + c, y + 1), (x + s - 2, y + c), (x + 2, y + c)], fill=_WHITE)
            d.rectangle([x + c - 1, y + c, x + c + 1, y + s - 2], fill=_WHITE)
    return draw


def _arrow_up(color):
    def draw(d, x, y, s):
        d.polygon([(x + s // 2, y), (x + s - 2, y + s // 2), (x + 2, y + s // 2)],
                  fill=color, outline=BOX_EDGE)
        d.rectangle([x + s // 2 - 1, y + s // 2, x + s // 2 + 1, y + s - 1], fill=color,
                    outline=BOX_EDGE)
    return draw


def _capsule(c1, c2):
    def draw(d, x, y, s):
        radius = max(1, s // 3)
        d.rounded_rectangle([x + 1, y, x + s - 2, y + s - 1], radius=radius, fill=c1,
                            outline=BOX_EDGE)
        d.pieslice([x + 1, y, x + s - 2, y + s - 1], 0, 180, fill=c2)
        d.rounded_rectangle([x + 1, y, x + s - 2, y + s - 1], radius=radius,
                            outline=BOX_EDGE)
    return draw


def _patch(color):
    def draw(d, x, y, s):
        d.rounded_rectangle([x, y + 1, x + s - 1, y + s - 2], radius=2, fill=color,
                            outline=BOX_EDGE)
        _plus(d, x + s // 2, y + s // 2, 2, _WHITE)
    return draw


def _candy(color, wrap=(250, 232, 244)):
    def draw(d, x, y, s):
        m = s // 2
        d.polygon([(x, y + m - 2), (x + 2, y + m), (x, y + m + 2)], fill=wrap,
                  outline=BOX_EDGE)
        d.polygon([(x + s - 1, y + m - 2), (x + s - 3, y + m), (x + s - 1, y + m + 2)],
                  fill=wrap, outline=BOX_EDGE)
        d.ellipse([x + 2, y + 1, x + s - 3, y + s - 2], fill=color, outline=BOX_EDGE)
    return draw


# 五角星:外顶点 / 内顶点按 36° 交错,半径按 s 缩放。
_STAR_OUTER = [(0.0, -1.0), (0.951, -0.309), (0.588, 0.809), (-0.588, 0.809),
               (-0.951, -0.309)]
_STAR_INNER = [(0.588, -0.809), (0.951, 0.309), (0.0, 1.0), (-0.951, 0.309),
               (-0.588, -0.809)]


def _star(color, *, small=False, crack=False):
    def draw(d, x, y, s):
        c = s / 2.0
        ro = (c - 1.6) if small else (c - 0.5)
        ri = ro * 0.42
        pts = []
        for i in range(5):
            ox, oy = _STAR_OUTER[i]
            ix, iy = _STAR_INNER[i]
            pts.append((x + c + ox * ro, y + c + oy * ro))
            pts.append((x + c + ix * ri, y + c + iy * ri))
        d.polygon(pts, fill=color, outline=BOX_EDGE)
        if crack:
            d.line([x + c, y + c - ro * 0.5, x + c - 1, y + c + ro * 0.3], fill=BOX_EDGE)
    return draw


# ── 道具 key → 图标(覆盖全部 BAG_ITEMS 与持有道具 ITEMS)──
_ITEM_ICONS = {
    # 精灵球家族:靠顶部配色 + 花纹区分
    "poke-ball": _ball((224, 64, 56)),
    "great-ball": _ball((72, 112, 224), _em_great()),
    "ultra-ball": _ball((240, 208, 48), _em_ultra()),
    "master-ball": _ball((144, 72, 176), _em_master()),
    "premier-ball": _ball(_WHITE, _em_premier()),
    "heal-ball": _ball((240, 144, 176), _em_plus((248, 216, 72))),
    "net-ball": _ball((72, 176, 208), _em_net()),
    "dusk-ball": _ball((56, 48, 72), _em_moon()),
    "quick-ball": _ball((248, 216, 64), _em_bolt()),
    "timer-ball": _ball((168, 112, 64), _em_clock()),
    "repeat-ball": _ball((224, 64, 56), _em_ring()),
    "nest-ball": _ball((104, 184, 88), _em_spots()),
    "level-ball": _ball((224, 72, 56), _em_v()),
    "heavy-ball": _ball((152, 152, 160), _em_iron()),
    "beast-ball": _ball((216, 176, 64), _em_gem()),
    # 伤药:瓶子颜色由浅到深 + 十字由小到大
    "potion": _bottle((160, 220, 170)),
    "super-potion": _bottle((120, 196, 140)),
    "hyper-potion": _bottle((88, 170, 116)),
    "max-potion": _bottle((56, 140, 96), cross=_WHITE),
    "full-restore": _bottle((64, 168, 200), cross=_GOLD),
    "fresh-water": _bottle((128, 192, 240)),
    "soda-pop": _bottle((240, 144, 128)),
    "lemonade": _bottle((240, 208, 96)),
    "moomoo-milk": _bottle((248, 248, 244), cap=(248, 160, 180)),
    "berry-juice": _flask((208, 88, 144)),
    "sweet-heart": _heart((248, 136, 168)),
    # 状态回复:颜色对应异常状态
    "antidote": _bottle((168, 88, 200), cross=_WHITE),
    "paralyze-heal": _bottle((232, 192, 48), cross=_WHITE),
    "burn-heal": _bottle((224, 96, 48), cross=_WHITE),
    "ice-heal": _bottle((96, 196, 232), cross=_WHITE),
    "awakening": _bottle((240, 240, 236), cross=(120, 124, 148)),
    "full-heal": _rainbow_bottle(),
    # PP 回复:蓝色药瓶 + 白点数量表示回复量
    "ether": _bottle((120, 150, 230), marks=1),
    "max-ether": _bottle((96, 128, 220), marks=2),
    "elixir": _bottle((80, 110, 210), marks=3),
    "max-elixir": _bottle((64, 92, 200), marks=4),
    # 复活:半星(有裂痕)/ 满星
    "revive": _star(_GOLD, small=True, crack=True),
    "max-revive": _star((252, 216, 72)),
    # 树果:颜色 + 叶片
    "oran-berry": _berry((248, 152, 64)),
    "cheri-berry": _berry((232, 72, 72)),
    "chesto-berry": _berry((136, 88, 192)),
    "pecha-berry": _berry((240, 144, 176)),
    "rawst-berry": _berry((96, 176, 200)),
    "aspear-berry": _berry((152, 200, 72)),
    "lum-berry": _berry((104, 176, 88), (200, 232, 120)),
    "sitrus-berry": _berry((232, 200, 72)),
    "leppa-berry": _berry((224, 104, 88), (144, 208, 96)),
    # 战斗强化(仅战斗中使用)
    "x-attack": _stat_up((224, 80, 72)),
    "x-defense": _stat_up((72, 120, 224), "shield"),
    "x-special": _stat_up((168, 96, 216), "spark"),
    "x-sp-defense": _stat_up((72, 176, 160), "shield"),
    "x-speed": _stat_up((240, 200, 64), "double"),
    "dire-hit": _target((200, 72, 72)),
    "guard-spec": _stat_up((96, 176, 112), "shield"),
    # 稀有用具
    "rare-candy": _candy((240, 152, 192)),
    "pp-up": _arrow_up((96, 196, 120)),
    "pp-max": _arrow_up((80, 140, 224)),
    "ability-capsule": _capsule((160, 96, 216), (248, 160, 208)),
    "ability-patch": _patch((248, 160, 208)),
    # 进化石:按原作石头的印象配色
    "fire-stone": _stone((240, 112, 48)),
    "water-stone": _stone((96, 160, 240)),
    "thunder-stone": _stone((248, 208, 48)),
    "leaf-stone": _stone((112, 200, 88)),
    "moon-stone": _stone((72, 56, 104)),
    "sun-stone": _stone((248, 152, 56)),
    "shiny-stone": _stone((240, 244, 248)),
    "dusk-stone": _stone((64, 60, 72)),
    "dawn-stone": _stone((96, 208, 184)),
    "ice-stone": _stone((168, 224, 240)),
    # 进化道具
    "sweet-apple": _apple((224, 72, 72)),
    "tart-apple": _apple((120, 192, 88)),
    "syrupy-apple": _apple((232, 184, 72)),
    "cracked-pot": _pot(),
    "unremarkable-teacup": _cup(),
    "auspicious-armor": _armor((232, 192, 88)),
    "malicious-armor": _armor((128, 88, 160)),
    "metal-alloy": _ingot(),
    "oval-stone": _oval_stone(),
    "razor-claw": _claw(),
    "razor-fang": _tooth(),
    "dragon-scale": _scale((112, 144, 232)),
    "deep-sea-scale": _scale((248, 152, 184)),
    "deep-sea-tooth": _tooth((168, 212, 240)),
    "kings-rock": _crown_rock(),
    "electirizer": _plug(),
    "magmarizer": _flask((232, 96, 64)),
    "protector": _armor((152, 168, 200)),
    "reaper-cloth": _cloth((120, 88, 152)),
    "sachet": _pouch((248, 176, 200)),
    "whipped-dream": _cream(),
    "prism-scale": _prism(),
    "up-grade": _chip((96, 168, 200)),
    # 持有道具
    "leftovers": _food(),
    "black-sludge": _sludge(),
    "life-orb": _orb((232, 80, 80), (240, 200, 96)),
    "toxic-orb": _orb((152, 88, 200), (200, 160, 232)),
    "flame-orb": _orb((232, 112, 64), (248, 208, 96)),
    "choice-band": _band((224, 96, 72)),
    "choice-specs": _glasses((232, 96, 112)),
    "choice-scarf": _cloth((96, 144, 232)),
    "assault-vest": _vest((200, 96, 72)),
    "eviolite": _gem((176, 180, 196)),
    "focus-sash": _band(_WHITE, knot=True),
    "rocky-helmet": _helmet((148, 148, 160)),
    "heavy-duty-boots": _boots((120, 88, 64)),
    "light-clay": _clay((232, 200, 120)),
    "terrain-extender": _clay((120, 176, 152)),
    "heat-rock": _stone((232, 120, 64)),
    "damp-rock": _stone((96, 152, 224)),
    "smooth-rock": _stone((224, 200, 152)),
    "icy-rock": _stone((152, 216, 232)),
    "booster-energy": _capsule((248, 160, 64), (96, 144, 240)),
    "loaded-dice": _dice(),
    "covert-cloak": _cloth((112, 88, 72)),
    "clear-amulet": _gem((120, 200, 220)),
    "weakness-policy": _gem((224, 96, 96)),
    "throat-spray": _spray(),
    "air-balloon": _balloon(),
    "safety-goggles": _goggles(),
    "expert-belt": _band((96, 72, 56)),
    "muscle-band": _band((200, 80, 72)),
    "wise-glasses": _glasses((96, 160, 232)),
    "silk-scarf": _cloth((248, 244, 236)),
    # 属性增强道具:统一宝石造型,靠属性配色区分
    "charcoal": _stone((72, 64, 60)),
    "mystic-water": _drop((96, 160, 240)),
    "miracle-seed": _seed((112, 192, 112)),
    "magnet": _magnet(),
    "never-melt-ice": _ice(),
    "black-belt": _band((64, 56, 52)),
    "poison-barb": _tooth((168, 96, 200)),
    "soft-sand": _sand(),
    "sharp-beak": _tooth((240, 168, 64)),
    "twisted-spoon": _spoon((192, 196, 208)),
    "silver-powder": _sand((232, 232, 240)),
    "hard-stone": _stone((180, 176, 168)),
    "spell-tag": _tag((152, 112, 200)),
    "dragon-fang": _tooth((240, 224, 200)),
    "black-glasses": _glasses((56, 56, 64)),
    "metal-coat": _sheet(_METAL),
    "fairy-feather": _feather((248, 176, 200)),
}

# 大类型兜底图标(旧界面只传 kind 时的退路)
def _tm_disc(color=(96, 176, 232)):
    """招式机图标:一张小光盘(圆盘 + 中心孔 + 高光)。"""

    def draw(d, x, y, size):
        s = float(size)
        cx, cy = x + s / 2, y + s / 2
        d.ellipse([cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2],
                  fill=color, outline=(40, 56, 72))
        d.ellipse([cx - s / 6, cy - s / 6, cx + s / 6, cy + s / 6],
                  fill=(240, 246, 250), outline=(40, 56, 72))
        d.line([cx - s / 3, cy - s / 3, cx - s / 8, cy - s / 8],
               fill=(220, 238, 250), width=max(1, int(max(1, s / 9))))

    return draw


_KIND_ICONS = {
    "tm": _tm_disc(),
    "ball": _ball((224, 64, 56)),
    "medicine": _bottle((120, 200, 168)),
    "pp": _bottle((120, 150, 230), marks=1),
    "status": _bottle((232, 200, 120)),
    "revive": _star(_GOLD),
    "battle": _stat_up((232, 128, 96)),
    "berry": _berry((232, 96, 112)),
    "stone": _stone((168, 176, 196)),
    "evo": _stone((176, 168, 200)),
    "rare": _gem((180, 140, 232)),
    "_default": _panel(),
}


# ══════════════════════════════════════════════════════════════════
# Screen:逻辑图形层 + 放大文字层
# ══════════════════════════════════════════════════════════════════
class Screen:
    """240×160 逻辑画布;`text()` 在放大后的画布上绘制。"""

    def __init__(self, *, scale: int = SCALE_DEFAULT, bg=BG, w: int = LOGICAL_W,
                 h: int = LOGICAL_H):
        from PIL import Image, ImageDraw

        self.scale = max(1, int(scale or SCALE_DEFAULT))
        self.w, self.h = w, h
        self.small = Image.new("RGB", (w, h), bg)
        self.d = ImageDraw.Draw(self.small)
        self.big = None
        self.d2 = None
        self._ops: list[tuple] = []  # 延迟的文字绘制指令

    # ── 生命周期 ──
    def upscale(self):
        """把逻辑层放大并**回放**所有文字指令。

        关键:文字必须最后画。若边画图形边写文字,先写下的文字会把"当时的"
        逻辑层定格成放大图,之后再画的方框/精灵/血条就都丢了。
        """
        from PIL import Image, ImageDraw

        self.big = self.small.resize(
            (self.w * self.scale, self.h * self.scale), Image.NEAREST
        )
        self.d2 = ImageDraw.Draw(self.big)
        for x, y, text, kw in self._ops:
            size = round(kw.get("size", 9) * self.scale)
            if not text or not self.f(kw.get("size", 9)):
                continue
            px, py, text = self._clamp_text(size, text, x, y, kw.get("stroke", 0))
            if not text:
                continue
            # 走 fonts.draw_text:主字体 + emoji/符号回退 + 位图字体缩放
            fonts.draw_text(
                self.big,
                (px, py),
                text,
                size,
                kw.get("fill", TEXT),
                stroke_width=round(kw.get("stroke", 0) * self.scale),
                stroke_fill=kw.get("sfill") or MSG_SHADOW,
            )
        return self.big

    def _clamp_text(self, size: int, text: str, x: float, y: float,
                    stroke: float = 0.0) -> tuple[int, int, str]:
        """把文字拉回画布内。

        底部那一行最容易出问题:字号 8 的逻辑文字从 y 起画,墨迹会到 y+9 左右,
        若框底贴着画布下边(160)就会溢出、看起来"越过背景边界"。
        这里按真实墨迹范围统一钳制,任何界面都不会画出背景之外。
        """
        # 描边会额外向外涂 stroke_width 像素,getbbox 并不包含它,必须一起算进来,
        # 否则带描边的文字仍会溢出 1~2 像素(实测能顶到画布最外一列)。
        pad = 2 + round(max(0.0, float(stroke)) * self.scale)
        s = str(sanitize(text))
        px, py = round(x * self.scale), round(y * self.scale)
        if not s:
            return px, py, s
        bx0, by0, bx1, by1 = fonts.bbox(s, size)
        limit_x = self.w * self.scale - pad
        limit_y = self.h * self.scale - pad
        # 比画布还宽的文字无论怎么摆都会溢出 → 先截断保头留省略号
        avail = limit_x - pad
        if bx1 - bx0 > avail > 0:
            cur = ""
            for ch in s:
                if fonts.measure(cur + ch + "…", size) > avail:
                    break
                cur += ch
            s = (cur or s[:1]) + "…"
            bx0, by0, bx1, by1 = fonts.bbox(s, size)
        if px + bx0 < pad:
            px = pad - bx0
        if px + bx1 > limit_x:
            px = max(0, limit_x - bx1)
        if py + by0 < pad:
            py = pad - by0
        if py + by1 > limit_y:
            py = max(0, limit_y - by1)
        return px, py, s

    def finish(self) -> bytes:
        try:
            self.upscale()
            buf = BytesIO()
            self.big.save(buf, format="PNG")
            return buf.getvalue()
        except Exception as e:  # 渲染失败一律回退文本
            logger.debug("宝可梦世界: 界面渲染失败: %s", e)
            return b""

    # ── 文字(逻辑坐标)──
    def f(self, size: float):
        return _font(round(size * self.scale))

    def fsize(self, size: float) -> int:
        """逻辑字号 → 放大层像素字号。"""
        return round(size * self.scale)

    def text(self, x: float, y: float, s: str, *, size: float = 9, fill=TEXT,
             stroke: float = 0, sfill=None, anchor_left: bool = True) -> float:
        """记录一条文字指令(在 `finish()` 时统一画到放大层)。返回逻辑宽度。"""
        _ = anchor_left
        s = sanitize(s)
        self._ops.append((x, y, s, {"size": size, "fill": fill,
                                    "stroke": stroke, "sfill": sfill}))
        return self.tw(s, size)

    def text_right(self, x: float, y: float, s: str, **kw) -> None:
        kw.pop("anchor_left", None)
        size = kw.get("size", 9)
        if self.f(size) is None:
            return
        w = self.tw(s, size)
        self.text(x - w, y, s, **kw)

    def text_center(self, cx: float, y: float, s: str, **kw) -> None:
        kw.pop("anchor_left", None)
        size = kw.get("size", 9)
        if self.f(size) is None:
            return
        w = self.tw(s, size)
        self.text(cx - w / 2, y, s, **kw)

    def tw(self, s: str, size: float = 9) -> float:
        """逻辑宽度(逻辑坐标)。含 emoji 时按分段测量。"""
        if self.f(size) is None:
            return 0.0
        return fonts.measure(sanitize(s), round(size * self.scale)) / self.scale

    def wrap(self, s: str, max_w: float, *, size: float = 9, limit: int = 6) -> list[str]:
        """按宽度折行;**显式换行符 `\n` 是强制换行**。

        以前 `\n` 被当普通字符(还会被 sanitize 丢掉),于是"多条信息用 \n 拼"
        再交给 wrap 就等于把内容连成一整段,换行位置落在条目中间
        (战斗结算的成长条目就这样把"学会了「X」"错挂到下一只宝可梦头上)。
        """
        out: list[str] = []
        for hard in str(s or "").split("\n"):
            cur = ""
            for ch in sanitize(hard):
                if self.tw(cur + ch, size) > max_w and cur:
                    out.append(cur)
                    cur = ch
                    if len(out) >= limit:
                        return out[:limit]
                else:
                    cur += ch
            if cur:
                out.append(cur)
                if len(out) >= limit:
                    return out[:limit]
        return out[:limit]

    # ── 组件(逻辑坐标)──
    def window(self, box, *, fill=BOX_FILL, edge=BOX_EDGE, radius: int = 3,
               shadow: bool = True, hi: bool = True) -> None:
        x0, y0, x1, y1 = box
        if shadow:
            self.d.rounded_rectangle([x0 + 2, y0 + 2, x1 + 2, y1 + 2], radius=radius,
                                     fill=BOX_SHADOW)
        self.d.rounded_rectangle(box, radius=radius, fill=fill, outline=edge, width=2)
        if hi and x1 - x0 > 8 and y1 - y0 > 8:
            self.d.rounded_rectangle([x0 + 2, y0 + 2, x1 - 2, y1 - 2],
                                     radius=max(1, radius - 1), outline=BOX_HI, width=1)

    def title_bar(self, text: str, *, box=(4, 3, 236, 16), size: float = 9.5,
                  right: str = "") -> None:
        x0, y0, x1, _y1 = box
        self.d.rounded_rectangle(box, radius=3, fill=TITLE_BG, outline=BOX_EDGE)
        self.d.rectangle([x0 + 2, y0 + 1, x1 - 2, y0 + 2], fill=TITLE_HI)
        # 文字基线要留出描边:条高 13(y0..y0+13),字号 9.5 从 y0+1.4 起
        # 墨迹约到 y0+13.5 —— 原来的 y0+3.2 会穿出条底 1.7px
        self.text(x0 + 5, y0 + 1.4, text, size=size, fill=TITLE_FG, stroke=0.6,
                  sfill=BOX_EDGE)
        if right:
            self.text_right(x1 - 5, y0 + 1.4, right, size=size, fill=TITLE_FG,
                            stroke=0.6, sfill=BOX_EDGE)

    @property
    def content_bottom(self) -> int:
        """内容区的下边界(底部提示条上方留出间隙)。

        `footer()` 占 y=h-16..h-5,再留 4px 间隙 ⇒ 面板底边最多到 h-20。
        **所有界面最下面那块 window 都应该用它**,别再各写各的 143/144/150 ——
        之前各写各的,`/宝可梦` 写到 152 直接和提示条叠在一起(用户反馈),
        队伍界面第 6 行写到 174 也压住了提示条。
        """
        return int(self.h) - 20

    def footer(self, text: str, *, box=None, size: float = 7.8) -> None:
        if box is None:
            # 跟着画布高度走:默认 160 高时算出来仍是 (4,144,236,155),与旧版一致;
            # 加高画布的界面(队伍列表)自动把提示条放到最底部。
            box = (4, self.h - 16, 236, self.h - 5)
        x0, y0, _x1, _y1 = box
        self.d.rounded_rectangle(box, radius=3, fill=BOX_FILL, outline=BOX_EDGE)
        self.text(x0 + 5, y0 + 1.8, text, size=size, fill=TEXT_DIM)

    def highlight(self, box, *, fill=SEL_BG, edge=SEL_EDGE) -> None:
        self.d.rounded_rectangle(box, radius=2, fill=fill, outline=edge)


    def ball(self, x: float, y: float, r: float = 3.5) -> None:
        d = self.d
        d.ellipse([x, y, x + r * 2, y + r * 2], fill=(250, 250, 245), outline=BOX_EDGE)
        d.pieslice([x, y, x + r * 2, y + r * 2], 180, 360, fill=(224, 64, 56))
        d.rectangle([x, y + r - 1, x + r * 2, y + r + 1], fill=BOX_EDGE)
        d.ellipse([x + r - 1.5, y + r - 1.5, x + r + 1.5, y + r + 1.5],
                  fill=(250, 250, 245), outline=BOX_EDGE)

    def hp_bar(self, box, ratio: float, *, tag: bool = False, ticks: bool = True) -> None:
        x, y, w, h = box
        if tag:
            self.d.rectangle([x - 13, y - 1, x - 1, y + h + 1], fill=HP_TAG_BG,
                             outline=BOX_EDGE)
            self.text_center(x - 7, y - 1.5, "HP", size=5.6, fill=HP_TAG_FG)
        self.d.rounded_rectangle([x, y, x + w, y + h], radius=2, fill=HP_TRACK,
                                 outline=BOX_EDGE)
        fill_w = int((w - 2) * max(0.0, min(1.0, ratio)))
        if fill_w <= 0:
            return
        color = HP_OK if ratio > 0.5 else (HP_MID if ratio > 0.2 else HP_LOW)
        self.d.rectangle([x + 1, y + 1, x + fill_w, y + h - 1], fill=color)
        self.d.rectangle([x + 1, y + 1, x + fill_w, y + 1],
                         fill=tuple(min(255, c + 40) for c in color))
        if ticks:
            for t in range(x + 6, x + w - 1, 6):
                self.d.line([t, y + 1, t, y + h - 1], fill=(196, 96, 84))

    def exp_bar(self, box, pct: float) -> None:
        x, y, w, h = box
        self.d.rectangle([x, y, x + w, y + h], fill=EXP_TRACK)
        fw = int(w * max(0.0, min(100.0, pct)) / 100.0)
        if fw > 0:
            self.d.rectangle([x, y, x + fw, y + h], fill=EXP_FILL)

    def status_chip(self, x: float, y: float, status: str, *, size: float = 5.4) -> None:
        style = STATUS_STYLE.get(str(status or ""))
        if not style:
            return
        self.d.rounded_rectangle([x, y, x + 11, y + 8], radius=2, fill=style[1],
                                 outline=BOX_EDGE)
        self.text_center(x + 5.5, y + 0.6, style[0], size=size, fill=(255, 255, 250))

    def badge(self, x: float, y: float, size: float = 11, *, on: bool = True,
              kind: str = "") -> None:
        """徽章:已获得=金色盾牌,未获得=灰色槽。

        `kind` 传道馆属性(如 ``"Rock"``/``"Water"``)时,盾牌里会画出对应属性的
        徽记 —— 正作里每枚徽章都有各自造型,这里用属性做区分,8 枚不再长得一样。
        未获得的徽章也画一个淡色徽记(让玩家知道后面还有什么)。
        """
        d = self.d
        # 徽记方框:以盾牌**主体**中心为基准居中。旧实现把徽记画在 (x, y+0.08s)
        # 且边长 0.82s → 水平方向左偏 0.09s(因为盾牌是 x..x+size 居中,
        # 而方框从 x 起);未获得状态又用了整格 1.0s,两种状态大小与位置都不一致。
        gs = size * 0.74
        gx = x + (size - gs) / 2
        gy = y + size * 0.57 - gs / 2
        if not on:
            d.rounded_rectangle([x, y, x + size, y + size], radius=2,
                                fill=BADGE_OFF, outline=BOX_EDGE)
            glyph = _BADGE_GLYPHS.get(str(kind))
            if glyph:
                glyph(d, gx, gy, gs, (196, 192, 176), BADGE_OFF)
            return
        d.polygon(
            [
                (x + size / 2, y),
                (x + size, y + size * 0.28),
                (x + size * 0.82, y + size * 0.86),
                (x + size / 2, y + size * 1.12),
                (x + size * 0.18, y + size * 0.86),
                (x, y + size * 0.28),
            ],
            fill=BADGE_ON, outline=BOX_EDGE,
        )
        d.line([x + size * 0.3, y + size * 0.2, x + size * 0.7, y + size * 0.2],
               fill=BADGE_ON_HI)
        glyph = _BADGE_GLYPHS.get(str(kind))
        if glyph:
            glyph(d, gx, gy, gs, BADGE_MARK, BADGE_ON)

    def item_icon(self, kind: str, x: float, y: float, size: float = 9) -> None:
        """画道具图标:优先按道具 key,退化到大类,最后退化到默认。

        调用方既可传道具 key(``"great-ball"``),也可传大类(``"ball"``);
        两者共用同一个签名,旧界面无需改动。图标严格画在
        ``[x, x + size - 1] × [y, y + size - 1]`` 内。
        """
        draw = _ITEM_ICONS.get(kind) or _KIND_ICONS.get(kind) or _KIND_ICONS["_default"]
        draw(self.d, x, y, size)

    # ── 精灵图 ──
    def sprite(self, species: str, *, ground: tuple[float, float], factor: float = 1.0,
               bounds: tuple[int, int] = (64, 64), back: bool = False,
               dim: bool = False, silhouette: bool = False) -> None:
        """按"脚底对齐"贴图:水平居中于 ground[0],底边压在 ground[1]。"""
        from PIL import Image, ImageEnhance, ImageOps

        cx, base_y = ground
        path = back_sprite_path(species) if back else sprite_path(species)
        img = _trimmed(path, factor, bounds, mirror=back)
        if img is None:
            img = _blob(bounds)
        if silhouette:
            img = ImageOps.grayscale(img).point(lambda v: 0 if v < 200 else 255)
            img = img.convert("RGBA")
            img = ImageOps.colorize(img.convert("L"), black=(0, 0, 0),
                                    white=(40, 40, 48)).convert("RGBA")
            alpha = Image.open(path).convert("RGBA").getchannel("A") if path and os.path.exists(path) else None
            if alpha is not None:
                alpha = alpha.crop(alpha.getbbox()) if alpha.getbbox() else alpha
                alpha = alpha.resize(img.size, Image.NEAREST)
                img.putalpha(alpha)
        if dim:
            img = ImageEnhance.Brightness(img).enhance(0.45)
        self.small.paste(img, (round(cx - img.width / 2), round(base_y - img.height)), img)


def _fit(sc: Screen, text: str, width: float, size: float) -> str:
    """把文字截断到给定宽度(超出时补省略号)。"""
    s = str(text or "")
    if sc.tw(s, size) <= width:
        return s
    while s and sc.tw(s + "…", size) > width:
        s = s[:-1]
    return s + "…"


def _trimmed(path: str, factor: float, bounds: tuple[int, int], *, mirror: bool = False):
    from PIL import Image, ImageOps

    if not path or not os.path.exists(path):
        return None
    try:
        with Image.open(path) as im:
            img = im.convert("RGBA")
    except (OSError, ValueError):
        return None
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    scale = factor
    if img.width * scale > bounds[0] or img.height * scale > bounds[1]:
        scale = min(bounds[0] / img.width, bounds[1] / img.height)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                     Image.NEAREST)
    if mirror:
        img = ImageOps.mirror(img)
    return img


def _blob(bounds: tuple[int, int]):
    from PIL import Image, ImageDraw

    w, h = bounds
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, w - 2, h - 4], fill=(176, 172, 148, 255), outline=(96, 94, 80, 255))
    return img


def _ratio(cur, mx) -> float:
    try:
        c, m = float(cur or 0), float(mx or 1)
    except (TypeError, ValueError):
        return 1.0
    return 0.0 if m <= 0 else max(0.0, min(1.0, c / m))


# ══════════════════════════════════════════════════════════════════
# 界面:队伍(FRLG 队伍菜单)
# ══════════════════════════════════════════════════════════════════
PARTY_H = 196   # 队伍界面专用画布高度(比默认 160 高:6 行都要够宽裕)


def render_party(
    mons: list[dict],
    *,
    title: str = "队伍",
    money: int = 0,
    box_count: int = 0,
    badges: int = 0,
    sprites: bool = True,
    scale: int = SCALE_DEFAULT,
) -> bytes:
    """队伍界面:6 行,每行 [精灵图][名字 Lv 性别][血条 + 数值][异常]。

    sprites=False 时不画缩略图(配置 sprite_enable 关闭),并把文字左移补位。

    mons: [{species, name, level, cur_hp, max_hp, status, gender, exp_pct}]
    """
    try:
        # 行高从 21 加到 26:原版 6×21 正好把最后一行的框压到底部提示条上,
        # 名字行与 HP 条之间只剩 0.5px(用户反馈"HP 图标与 Lv 贴太近")。
        # 画布高度必须跟着长:6 行 ×26 从 y=19 排到 174,而 footer 在 h-16 起 ——
        # 186 高时提示条落在 170,和第 6 行重叠(测试抓到的),196 高才留出间隙。
        sc = Screen(scale=scale, h=PARTY_H)
        sc.title_bar(title, right=f"{money:,}₽")
        top = 19
        row_h = 26
        for i in range(6):
            y0 = top + i * row_h
            box = (5, y0, 235, y0 + row_h - 1)
            sc.window(box, radius=2, shadow=(i == 0))
            if i >= len(mons):
                sc.text(14, y0 + 5, f"{i + 1}. —", size=8.5, fill=TEXT_DIM)
                continue
            mon = mons[i]
            x_name = 37.0
            if sprites:
                sc.sprite(str(mon.get("species") or ""), ground=(22, y0 + row_h - 4),
                          factor=0.62, bounds=(20, row_h - 8), back=False,
                          dim=_ratio(mon.get("cur_hp"), mon.get("max_hp")) <= 0)
            else:
                x_name = 14.0
            name = str(mon.get("name") or "?")
            # 第一行:名字 / 性别 / Lv(名字行下移 1px,把下方空间让给血条)
            sc.text(x_name, y0 + 3.5, name, size=8.6, fill=TEXT)
            w = sc.tw(name, 8.6) + x_name + 2
            gender, gcolor = gender_symbol(str(mon.get("gender") or ""))
            if gender:
                sc.text(w, y0 + 3.5, gender, size=8.6, fill=gcolor)
                w += 6
            sc.text(w + 2, y0 + 3.5, f"Lv{int(mon.get('level') or 0)}", size=8.6,
                    fill=TEXT)
            # 第二行:HP 标签 + 血条(与名字行留出 ~3px 间隙,不再贴着 Lv)
            sc.hp_bar((70, y0 + 16, 118, 5),
                      _ratio(mon.get("cur_hp"), mon.get("max_hp")), tag=True)
            sc.text_right(232, y0 + 13.5,
                          f"{int(mon.get('cur_hp') or 0)}/{int(mon.get('max_hp') or 0)}",
                          size=7.6, fill=TEXT)
            if mon.get("status"):
                sc.status_chip(192, y0 + 3, str(mon["status"]))
            if mon.get("item"):
                # 持有道具:名字前的小星标(别压到右侧 HP 数值上)
                sc.text(32, y0 + 3.5, "★", size=7.4, fill=(214, 160, 56))
        sc.footer(f"电脑 {box_count} 只 · 徽章 {badges} 枚 · /对战 出招时用序号换人")
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 队伍界面渲染失败: %s", e)
        return b""


# ══════════════════════════════════════════════════════════════════
# 界面:背包(FRLG 背包 + 口袋页签)
# ══════════════════════════════════════════════════════════════════
# 背包每页条目数(与 main.py 的翻页换算共用,别各写各的)
BAG_PER_PAGE = 4


def render_bag(
    items: list[dict],
    *,
    money: int = 0,
    active_pocket: str = "items",
    selected: int = 0,
    per_page: int = BAG_PER_PAGE,
    pockets: list[tuple[str, str]] | None = None,
    scale: int = SCALE_DEFAULT,
) -> bytes:
    """背包界面:页签 + 条目列表(分页) + 底部说明框。

    items: [{key, zh, count, desc, effect, kind}] —— 只显示属于 active_pocket 的条目。
    `selected` 是**整个口袋里的下标**(0 起),会自动翻到它所在的那一页并高亮;
    说明框显示的是**选中那一件**的效果与说明(以前写死第 0 件,口袋东西一多
    就永远只看得到第一件的说明 —— 用户反馈)。
    """
    try:
        pockets = pockets or POCKETS
        sc = Screen(scale=scale)
        sc.title_bar("背包", right=f"{money:,}₽")
        # 页签
        tx = 5
        for key, label in pockets:
            kw = sc.tw(label, 7.6) + 8
            on = key == active_pocket
            box = (tx, 18, tx + kw, 29)
            sc.d.rounded_rectangle(box, radius=2,
                                   fill=POCKET_ICON.get(key, BOX_FILL) if on else BOX_FILL,
                                   outline=SEL_EDGE if on else BOX_EDGE)
            sc.text(tx + 4, 20, label, size=7.6,
                    fill=(30, 40, 30) if on else TEXT_DIM)
            tx += kw + 2
        # 列表(分页)
        total = len(items)
        per = max(1, int(per_page))
        pages = max(1, (total + per - 1) // per)
        sel = max(0, min(int(selected or 0), total - 1)) if total else 0
        page = max(0, min(sel // per, pages - 1)) if total else 0
        shown = items[page * per:(page + 1) * per]
        top, bottom = 31.0, 104.0
        sc.window((5, top, 235, bottom), radius=2)
        if not shown:
            sc.text(14, top + 3, "这个口袋是空的。", size=8.5, fill=TEXT_DIM)
        # 行高按"每页槽位数"算,不按实际条目数 —— 否则最后半页会被拉得很高
        row_h = max(12.0, (bottom - top - 2) / per)
        for i, it in enumerate(shown):
            idx = page * per + i
            y0 = top + 1 + i * row_h
            if idx == sel:
                sc.highlight((7, y0, 233, y0 + row_h - 1))
            sc.item_icon(str(it.get("key") or it.get("kind") or ""), 10, y0 + 1.5, 9)
            # 行首显示**序号**:不然玩家根本没法指定"看第几件"
            sc.text(22, y0 + 2.2, f"{idx + 1}.", size=6.6, fill=TEXT_DIM)
            sc.text(34, y0 + 1.5,
                    _fit(sc, str(it.get("zh") or it.get("key") or ""), 112, 8.4),
                    size=8.4, fill=TEXT)
            sc.text_right(228, y0 + 1.5, f"×{int(it.get('count') or 0)}",
                          size=8.4, fill=TEXT)
            # 每行都写自己的效果:以前只有"被选中那一件"有说明,
            # 默认选中第 0 件 ⇒ 玩家看到的永远只有第一件的说明
            eff = _fit(sc, str(it.get("effect") or it.get("desc") or ""), 198, 6.4)
            if eff:
                sc.text(34, y0 + row_h - 7.6, eff, size=6.4, fill=TEXT_DIM)
        # 说明框:选中那一件的效果 + 说明(标题行右侧放页码,避免压住列表)
        cur = items[sel] if total else {}
        sc.window((5, 106, 235, sc.content_bottom), radius=2)
        head = f"第 {sel + 1}/{max(1, total)} 件 · {cur.get('zh') or cur.get('key') or '—'}"
        if cur.get("kind"):
            head += f" · {KIND_ZH.get(str(cur['kind']), cur['kind'])}"
        sc.text(10, 108.5, _fit(sc, head, 150, 7.6), size=7.6, fill=TEXT_DIM)
        if pages > 1:
            sc.text_right(231, 108.5,
                          f"{page + 1}/{pages} 页 · {page * per + 1}-"
                          f"{min(total, (page + 1) * per)}/共 {total}",
                          size=6.6, fill=TEXT_DIM)
        # 效果与说明**分块**绘制:`Screen.wrap()` 不认 `\n`(逐字符折行),
        # 拼在一起会被连成一行。
        y = 117.5
        room = max(1, int((sc.content_bottom - y - 0.5) // 8.6))
        eff = str(cur.get("effect") or "")
        if eff:
            for ln in sc.wrap(f"⚙️ {eff}", 220, size=7.8, limit=1):
                sc.text(10, y, ln, size=7.8, fill=(40, 96, 56))
                y += 8.6
                room -= 1
        for ln in sc.wrap(str(cur.get("desc") or "—"), 220, size=7.8, limit=max(1, room)):
            sc.text(10, y, ln, size=7.8, fill=TEXT)
            y += 8.6
        # 提示里写**中文分类名**,别把内部 key(items/balls)抖给玩家
        pk_label = next((lb for pk, lb in pockets if pk == active_pocket), "道具")
        # 底部只放最常用的两条(长了会被截断);完整用法在消息文本的提示行里
        tail = f'◆ 详情:"/背包 {pk_label} 5"'
        if pages > 1:
            tail += f' · 翻页:"/背包 {pk_label} 页 2"'
        sc.footer(_fit(sc, tail, 226, 7.0), size=7.0)
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 背包界面渲染失败: %s", e)
        return b""


# ══════════════════════════════════════════════════════════════════
# 界面:训练家卡
# ══════════════════════════════════════════════════════════════════
def render_trainer_card(
    info: dict,
    *,
    scale: int = SCALE_DEFAULT,
) -> bytes:
    """训练家卡:左半边资料 + 右半边徽章盒。

    info: {name, id_no, money, region, location, play_day, steps, party, box,
           seen, caught, badges:[(name, bool)], story_progress, best}
    """
    try:
        sc = Screen(scale=scale)
        sc.title_bar("训练家卡")
        sc.window((5, 19, 128, sc.content_bottom), radius=3)
        sc.window((132, 19, 235, sc.content_bottom), radius=3)
        sc.ball(14, 26, 7)
        # 长名字必须截断:左框只到 x=128,否则会横穿到右侧徽章盒上
        sc.text(38, 26, _fit(sc, str(info.get("name") or "训练家"), 84, 11),
                size=11, fill=TEXT)
        sc.text(38, 40, f"ID No.{info.get('id_no') or '00000'}", size=8, fill=TEXT_DIM)
        rows = [
            ("金钱", f"{int(info.get('money') or 0):,}₽"),
            ("地区", str(info.get("region") or "?")),
            ("当前", str(info.get("location") or "?")),
            ("旅程", f"{info.get('play_day') or 0} 天"),   # 玩家自己的旅程天数

            ("步数", f"{int(info.get('steps') or 0):,}"),
        ]
        for i, (k, v) in enumerate(rows):
            sc.text(14, 55 + i * 13, k, size=8, fill=TEXT_DIM)
            sc.text_right(122, 55 + i * 13, v, size=8.4, fill=TEXT)
        sc.text(14, 122, "图鉴", size=8, fill=TEXT_DIM)
        sc.text_right(122, 122, f"捕获 {info.get('caught') or 0} / 见到 {info.get('seen') or 0}",
                      size=8, fill=TEXT)
        # 徽章盒
        sc.text(140, 24, "徽章", size=8.5, fill=TEXT_DIM)
        badges = list(info.get("badges") or [])
        for i in range(8):
            gx, gy = 140 + (i % 4) * 23, 34 + (i // 4) * 22
            # 容错:徽章项约定为 (名称, 是否获得);数据异常时降级为"未获得",
            # 而不是整个界面渲染失败返回空图。
            b = badges[i] if i < len(badges) else None
            ok_item = isinstance(b, (list, tuple)) and len(b) >= 2
            on = bool(b[1]) if ok_item else False
            name = str(b[0]) if ok_item and b[0] else ""
            kind = str(b[2]) if ok_item and len(b) >= 3 and b[2] else ""
            sc.badge(gx, gy, 13, on=on, kind=kind)
            if name and on:
                sc.text_center(gx + 6.5, gy + 14, name[:4], size=6, fill=TEXT_DIM)
        sc.text(140, 82, "队伍", size=8.5, fill=TEXT_DIM)
        sc.text(196, 82, f"{info.get('party') or 0}/6", size=8.5, fill=TEXT)
        sc.text(140, 94, "电脑", size=8.5, fill=TEXT_DIM)
        sc.text(196, 94, f"{info.get('box') or 0} 只", size=8.5, fill=TEXT)
        prog = str(info.get("story_progress") or "")
        for i, ln in enumerate(sc.wrap(prog, 90, size=7.6, limit=4)):
            sc.text(140, 108 + i * 9, ln, size=7.6, fill=TEXT)
        sc.footer(str(info.get("best") or "◆ /帮助 查看全部指令"))
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 训练家卡渲染失败: %s", e)
        return b""


# ══════════════════════════════════════════════════════════════════
# 界面:图鉴(RSE/FRLG 图鉴条目)
# ══════════════════════════════════════════════════════════════════
def render_dex(
    entry: dict,
    *,
    caught: bool = False,
    seen: bool = False,
    locations: list[dict] | None = None,
    scale: int = SCALE_DEFAULT,
) -> bytes:
    """图鉴条目:No./名字/分类/属性/种族值/捕捉率 + 精灵图 + 说明文。"""
    try:
        sc = Screen(scale=scale)
        num = int(entry.get("num") or 0)
        known = caught or seen
        mark = "●" if caught else ("◎" if seen else "○")
        name = entry.get("zh") or entry.get("name") or "?"
        sc.title_bar(f"No.{num:04d} " + (name if known else "???"), right=mark)
        sc.window((5, 19, 112, 112), radius=3)
        sc.sprite(str(entry.get("_key") or ""), ground=(58, 108), factor=1.0,
                  bounds=(84, 82), silhouette=not known)
        sc.window((116, 19, 235, 112), radius=3)
        y = 23
        sc.text(121, y, "分类", size=7.6, fill=TEXT_DIM)
        sc.text(150, y, str(entry.get("genus") or "?") if known else "???",
                size=7.8, fill=TEXT)
        y += 11
        for t in (entry.get("types") or [])[:2]:
            label = str(t) if known else "???"
            sc.d.rounded_rectangle([121, y, 121 + sc.tw(label, 7.4) + 8, y + 10],
                                   radius=2,
                                   fill=type_color(str(t)) if known else (150, 150, 150),
                                   outline=BOX_EDGE)
            sc.text(125, y + 1.2, label, size=7.4, fill=(255, 255, 250))
            y += 11
        bs = entry.get("baseStats") or {}
        total = sum(int(bs.get(k, 0) or 0) for k in ("hp", "atk", "def", "spa", "spd", "spe"))
        stats = [("HP", "hp"), ("攻击", "atk"), ("防御", "def"),
                 ("特攻", "spa"), ("特防", "spd"), ("速度", "spe")]
        y = 58
        for label, key in stats:
            val = int(bs.get(key, 0) or 0)
            sc.text(121, y, label, size=7, fill=TEXT_DIM)
            sc.d.rectangle([142, y + 1.5, 142 + 40, y + 6], fill=(216, 212, 186))
            if known:
                sc.d.rectangle([142, y + 1.5, 142 + min(40, val * 40 // 160), y + 6],
                               fill=HP_OK if val >= 80 else (HP_MID if val >= 50 else HP_LOW))
            sc.text_right(232, y, str(val) if known else "?", size=7, fill=TEXT)
            y += 8.5
        sc.window((5, 115, 235, sc.content_bottom), radius=3)
        flavor = str(entry.get("flavor") or entry.get("flavorEn") or "—")
        if not known:
            flavor = "还没有见过这只宝可梦。先去野外找到它,或者把它收服吧。"
        for i, ln in enumerate(sc.wrap(flavor, 222, size=8, limit=3)):
            sc.text(10, 118 + i * 9.5, ln, size=8, fill=TEXT)
        tail = ""
        if locations:
            loc = locations[0]
            tail = f"野外:{loc.get('zh')} Lv{loc.get('min')}-{loc.get('max')}"
            if len(locations) > 1:
                tail += f" 等 {len(locations)} 处"
        else:
            tail = "野外分布:未知" if not caught else "野外分布:已收录"
        dims = ""
        if known and (entry.get("heightm") or entry.get("weightkg")):
            dims = f"{entry.get('heightm') or '?'}m · {entry.get('weightkg') or '?'}kg · "
        if known:
            tail = (f"{dims}种族值 {total} · 捕捉率 {entry.get('captureRate') or '?'} · "
                    + tail)
        sc.footer(tail)
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 图鉴渲染失败: %s", e)
        return b""


# ═════════════════════════════════════════════════════════════════
# 单只宝可梦资料(仿 GBA 的"摘要"画面)
# ═════════════════════════════════════════════════════════════════
def _chip(sc: Screen, x: float, y: float, label: str, *, size: float = 6.6) -> float:
    """属性/标签色块,返回宽度(传入英文属性 key 或中文都行)。"""
    label = get_dex().type_label(label) if label else "?"
    w = sc.tw(label, size) + 6
    sc.d.rounded_rectangle([x, y, x + w, y + 9], radius=2,
                           fill=type_color(label), outline=BOX_EDGE)
    sc.text(x + 3, y + 1, label, size=size, fill=(255, 255, 252))
    return w


def render_mon_summary(mon: dict, *, index: int = 1, party_size: int = 1,
                       scale: int = SCALE_DEFAULT) -> bytes:
    """单只宝可梦资料页:`/宝可梦 <序号>`(仿 GBA 的"摘要"画面)。

    mon 字段(都可缺省,缺省显示 "?"):species/name/level/gender/types/cur_hp/max_hp/
    status/exp_pct/exp_now/exp_next/stats/base/nature_zh/ability_zh/ability_desc/
    item_zh/friendship/moves/evo_hint/dex_no/genus

    注意 `hp_bar`/`exp_bar` 的参数是 **(x, y, 宽, 高)** 而不是 (x0,y0,x1,y1),
    `exp_bar` 的百分比是 **0~100**(踩过一次:传了 0~1,条子直接画满整个面板)。
    """
    try:
        sc = Screen(scale=scale)
        name = str(mon.get("name") or "?")
        lv = int(mon.get("level") or 1)
        sc.title_bar("宝可梦资料", right=f"{int(index)}/{max(1, int(party_size))}")

        # ── 左上:名字 / 属性 / 立绘 ──
        sc.window((5, 19, 96, 92), radius=2)
        sc.text_center(50.5, 21.5, _fit(sc, name, 86, 8.2), size=8.2, fill=TEXT)
        types = [str(t) for t in (mon.get("types") or [])][:2]
        if types:
            widths = [sc.tw(get_dex().type_label(t), 6.6) + 6 for t in types]
            cx = 50.5 - (sum(widths) + 2 * (len(types) - 1)) / 2
            for t, w in zip(types, widths, strict=False):
                _chip(sc, cx, 30.5, t, size=6.6)
                cx += w + 2
        # 立绘贴底,上边缘在属性标签之下(bounds 会按比例钳制,不会溢出)
        sc.sprite(str(mon.get("species") or ""), ground=(50, 89), factor=1.0,
                  bounds=(62, 46))

        # ── 右上:等级 / HP / 经验 / 能力值 ──
        sc.window((100, 19, 235, 92), radius=2)
        head = f"Lv{lv}"
        g = str(mon.get("gender") or "")
        if g in ("M", "F"):
            head += " " + ("♂" if g == "M" else "♀")
        sc.text(105, 21, head, size=7.8, fill=TEXT)
        cur, mx = int(mon.get("cur_hp") or 0), max(1, int(mon.get("max_hp") or 1))
        sc.text_right(230, 21, f"HP {cur}/{mx}", size=7.4, fill=TEXT)
        sc.hp_bar((105, 30, 125, 6), cur / mx)
        sc.text(105, 39, "EXP", size=6.6, fill=TEXT_DIM)
        sc.text_right(230, 39, f"{int(mon.get('exp_now') or 0)}/{int(mon.get('exp_next') or 0)}",
                      size=6.6, fill=TEXT_DIM)
        # exp_bar 的百分比是 0~100
        sc.exp_bar((105, 47, 125, 4), float(mon.get("exp_pct") or 0.0))
        stats = mon.get("stats") or {}
        base = mon.get("base") or {}
        rows = (("HP", "hp"), ("攻击", "atk"), ("防御", "def"),
                ("特攻", "spa"), ("特防", "spd"), ("速度", "spe"))
        for i, (label, key) in enumerate(rows):
            col, row = divmod(i, 3)
            x = 106 + col * 66
            xr = 160 + col * 70
            y = 56 + row * 11
            sc.text(x, y, label, size=7.0, fill=TEXT_DIM)
            b = base.get(key)
            if isinstance(b, (int, float)):
                sc.text(x + 26, y + 0.4, f"({int(b)})", size=6.2, fill=TEXT_DIM)
            sc.text_right(xr, y, str(int(stats.get(key) or 0)), size=7.8, fill=TEXT)

        # 底部提示条(footer)在 160 高的画布上占 y=144..155 —— 下面这两个窗口
        # **不能超过 140**,否则会被提示条压住(用户反馈的"重叠")。
        # ── 左下:特性 / 性格 / 亲密 / 持有物 ──
        sc.window((5, 96, 118, 140), radius=2)
        y = 99.0
        for label, value in (
            ("特性", str(mon.get("ability_zh") or "?")),
            ("性格", str(mon.get("nature_zh") or "?")),
            ("亲密", str(int(mon.get("friendship") or 0))),
            ("持有", str(mon.get("item_zh") or "无")),
        ):
            sc.text(10, y, label, size=6.8, fill=TEXT_DIM)
            sc.text(34, y, _fit(sc, value, 84, 7.2), size=7.2, fill=TEXT)
            y += 9.0
        # ── 右下:招式 + PP ──
        sc.window((122, 96, 235, 140), radius=2)
        sc.text(127, 98, "招式", size=7.0, fill=TEXT_DIM)
        moves = list(mon.get("moves") or [])
        if not moves:
            sc.text(127, 112, "还没有招式", size=7.6, fill=TEXT_DIM)
        for i, mv in enumerate(moves[:4]):
            y = 104.5 + i * 8.8
            # 属性用左侧小色块表示(省下横向空间给招式名与 PP)
            if mv.get("type"):
                sc.d.rectangle([127, y + 1.5, 130.5, y + 7],
                               fill=type_color(get_dex().type_label(str(mv["type"]))),
                               outline=BOX_EDGE)
            sc.text(134, y, _fit(sc, str(mv.get("zh") or "?"), 62, 7.6), size=7.6, fill=TEXT)
            pp, ppx = int(mv.get("pp") or 0), int(mv.get("pp_max") or 0)
            sc.text_right(231, y + 0.6, f"PP {pp}/{ppx}", size=6.4,
                          fill=TEXT_DIM if pp else (198, 96, 96))

        tail = f"编号 #{int(mon.get('dex_no') or 0):04d}"
        if mon.get("genus"):
            tail += f" · {mon['genus']}"
        if mon.get("evo_hint"):
            tail += f" · {mon['evo_hint']}"
        sc.footer(_fit(sc, tail, 228, 7.2), size=7.2)
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 宝可梦资料渲染失败: %s", e)
        return b""


def render_box(mons: list[dict], *, capacity: int = 0, money: int = 0,
               scale: int = SCALE_DEFAULT) -> bytes:
    """电脑箱子:`/电脑`。显示仓库里的宝可梦(每页 16 只)。"""
    try:
        sc = Screen(scale=scale)
        sc.title_bar("电脑 · 宝可梦仓库",
                     right=f"{len(mons)}/{capacity}只" if capacity else f"{len(mons)}只")
        if not mons:
            sc.window((5, 19, 235, sc.content_bottom), radius=2)
            sc.text_center(120, 70, "仓库是空的。", size=9, fill=TEXT_DIM)
            sc.text_center(120, 84, "队伍满 6 只后收服的宝可梦会存到这里。",
                           size=7.2, fill=TEXT_DIM)
            sc.footer("取出:`/队伍 取出 <序号>`", size=7.4)
            return sc.finish()
        # ch×rows 必须让最后一格的下边缘 ≤ 140(footer 在 144..155)
        cols, rows, cw, ch = 2, 7, 116, 17
        for i, mon in enumerate(mons[:cols * rows]):
            cx = 4 + (i % cols) * (cw + 1)
            cy = 19 + (i // cols) * ch
            sc.window((cx, cy, cx + cw - 1, cy + ch - 1), radius=2)
            sc.sprite(str(mon.get("species") or ""), ground=(cx + 14, cy + ch - 3),
                      factor=0.5, bounds=(24, ch - 6),
                      dim=_ratio(mon.get("cur_hp"), mon.get("max_hp")) <= 0)
            sc.text(cx + 27, cy + 2, _fit(sc, str(mon.get("name") or "?"), 58, 7.6),
                    size=7.6, fill=TEXT)
            lv = f"Lv{int(mon.get('level') or 1)}"
            g = str(mon.get("gender") or "")
            if g in ("M", "F"):
                lv += "♂" if g == "M" else "♀"
            sc.text_right(cx + cw - 4, cy + 2, lv, size=6.8, fill=TEXT_DIM)
            # hp_bar 参数是 (x, y, 宽, 高)
            sc.hp_bar((cx + 27, cy + 10.5, cw - 34, 4),
                      _ratio(mon.get("cur_hp"), mon.get("max_hp")))
        more = len(mons) - cols * rows
        tail = f"共 {len(mons)} 只"
        if more > 0:
            tail += f"(另有 {more} 只未显示)"
        sc.footer(f"{tail} · 取出:`/队伍 取出 <序号>`", size=7.2)
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 仓库渲染失败: %s", e)
        return b""
