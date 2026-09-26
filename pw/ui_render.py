"""GBA 风格宝可梦界面工具箱 + 常用界面(队伍 / 背包 / 训练家卡 / 图鉴)。

设计沿用 `battle_render.py` 的两层做法:
  · **图形在 240×160 逻辑画布上画**,最后 `NEAREST` 放大 → 像素质感;
  · **文字在放大层画**,坐标仍用逻辑像素 → 中文清晰可读。

对外主要是 `Screen` 类(组件化),以及各 `render_*` 界面函数。
任何异常都返回 `b""`,调用方回退纯文本。
"""

from __future__ import annotations

import os
from functools import lru_cache
from io import BytesIO

from astrbot.api import logger

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


@lru_cache(maxsize=24)
def _font(size: int):
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    for path in (FONT_MAIN, FONT_SYMBOL):
        if not os.path.exists(path):
            continue
        try:
            return ImageFont.truetype(path, max(6, int(size)))
        except OSError:
            continue
    return None


@lru_cache(maxsize=4)
def _cmap(path: str) -> frozenset[int]:
    """字体覆盖的码点集合(用于剔除会渲染成豆腐块的字符)。"""
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return frozenset()
    if not os.path.exists(path):
        return frozenset()
    try:
        with TTFont(path, fontNumber=0) as f:
            return frozenset(f.getBestCmap())
    except Exception:
        return frozenset()


@lru_cache(maxsize=1)
def _glyph_set() -> frozenset[int]:
    """按**实际用来渲染的主字体**判定覆盖范围。

    注意:Symbola 覆盖了大量 emoji(📨💰),但 `_font()` 优先返回 OPPOSans,
    真正画字的是 OPPOSans —— 用两个字体的并集来判定会把 emoji 放行,
    结果还是豆腐块。所以这里只看主字体(它缺失时才退化为 Symbola)。
    """
    main = _cmap(FONT_MAIN)
    return main if main else _cmap(FONT_SYMBOL)


def sanitize(text: str) -> str:
    """去掉字体没有的字符(主要是 emoji),避免界面上出现豆腐块。

    游戏内文本里大量出现 📨🎁💰🏅 这类 emoji,而 OPPOSans/Symbola 都没有;
    直接画会变成空方框。这里统一过滤 —— 保留 ASCII 与字体覆盖的码点。
    """
    cm = _glyph_set()
    if not cm:
        return str(text)
    out = []
    for ch in str(text):
        if ch in "\n\t" or ord(ch) < 128 or ord(ch) in cm:
            out.append(ch)
        elif ch == "\u3000":
            out.append(" ")
    return "".join(out)


def available() -> bool:
    try:
        from PIL import Image, ImageDraw  # noqa: F401
    except ImportError:
        return False
    return _font(20) is not None


def type_color(t: str) -> tuple[int, int, int]:
    return TYPE_COLOR.get(str(t or ""), (168, 168, 144))


def gender_symbol(gender: str) -> tuple[str, tuple[int, int, int]]:
    if gender == "M":
        return "♂", MALE
    if gender == "F":
        return "♀", FEMALE
    return "", TEXT


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
            font = self.f(kw.get("size", 9))
            if font is None or not text:
                continue
            px, py, text = self._clamp_text(font, text, x, y,
                                            kw.get("stroke", 0))
            self.d2.text(
                (px, py),
                str(text),
                font=font,
                fill=kw.get("fill", TEXT),
                stroke_width=round(kw.get("stroke", 0) * self.scale),
                stroke_fill=kw.get("sfill") or MSG_SHADOW,
            )
        return self.big

    def _clamp_text(self, font, text: str, x: float, y: float,
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
        try:
            bx0, by0, bx1, by1 = font.getbbox(s)
        except (ValueError, OSError):
            return px, py, s
        limit_x = self.w * self.scale - pad
        limit_y = self.h * self.scale - pad
        # 比画布还宽的文字无论怎么摆都会溢出 → 先截断保头留省略号
        avail = limit_x - pad
        if bx1 - bx0 > avail > 0:
            cur = ""
            for ch in s:
                if font.getlength(cur + ch + "…") > avail:
                    break
                cur += ch
            s = (cur or s[:1]) + "…"
            try:
                bx0, by0, bx1, by1 = font.getbbox(s)
            except (ValueError, OSError):
                return px, py, s
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

    def text(self, x: float, y: float, s: str, *, size: float = 9, fill=TEXT,
             stroke: float = 0, sfill=None, anchor_left: bool = True) -> float:
        """记录一条文字指令(在 `finish()` 时统一画到放大层)。返回逻辑宽度。"""
        _ = anchor_left
        s = sanitize(s)
        self._ops.append((x, y, s, {"size": size, "fill": fill,
                                    "stroke": stroke, "sfill": sfill}))
        font = self.f(size)
        return font.getlength(str(s)) / self.scale if font else 0.0

    def text_right(self, x: float, y: float, s: str, **kw) -> None:
        kw.pop("anchor_left", None)
        size = kw.get("size", 9)
        font = self.f(size)
        if font is None:
            return
        w = font.getlength(str(s)) / self.scale
        self.text(x - w, y, s, **kw)

    def text_center(self, cx: float, y: float, s: str, **kw) -> None:
        kw.pop("anchor_left", None)
        size = kw.get("size", 9)
        font = self.f(size)
        if font is None:
            return
        w = font.getlength(str(s)) / self.scale
        self.text(cx - w / 2, y, s, **kw)

    def tw(self, s: str, size: float = 9) -> float:
        font = self.f(size)
        return font.getlength(sanitize(s)) / self.scale if font else 0.0

    def wrap(self, s: str, max_w: float, *, size: float = 9, limit: int = 6) -> list[str]:
        out, cur = [], ""
        for ch in sanitize(s):
            if self.tw(cur + ch, size) > max_w and cur:
                out.append(cur)
                cur = ch
                if len(out) >= limit:
                    return out
            else:
                cur += ch
        if cur:
            out.append(cur)
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
        self.text(x0 + 5, y0 + 3.2, text, size=size, fill=TITLE_FG, stroke=0.6,
                  sfill=BOX_EDGE)
        if right:
            self.text_right(x1 - 5, y0 + 3.2, right, size=size, fill=TITLE_FG,
                            stroke=0.6, sfill=BOX_EDGE)

    def footer(self, text: str, *, box=(4, 144, 236, 155), size: float = 7.8) -> None:
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

    def badge(self, x: float, y: float, size: float = 11, *, on: bool = True) -> None:
        """徽章:未获得=灰槽,已获得=金色盾牌。"""
        if not on:
            self.d.rounded_rectangle([x, y, x + size, y + size], radius=2,
                                     fill=BADGE_OFF, outline=BOX_EDGE)
            return
        self.d.polygon(
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
        self.d.line([x + size * 0.3, y + size * 0.2, x + size * 0.7, y + size * 0.2],
                    fill=BADGE_ON_HI)

    def item_icon(self, kind: str, x: float, y: float, size: float = 9) -> None:
        """按道具类别画一个小图标。"""
        d = self.d
        s = size
        if kind == "ball":
            d.ellipse([x, y, x + s, y + s], fill=(250, 250, 245), outline=BOX_EDGE)
            d.pieslice([x, y, x + s, y + s], 180, 360, fill=(224, 64, 56))
            d.rectangle([x, y + s / 2 - 1, x + s, y + s / 2 + 1], fill=BOX_EDGE)
        elif kind in ("medicine", "pp"):
            body = (120, 200, 168) if kind == "medicine" else (120, 150, 230)
            d.rounded_rectangle([x + 1, y + 1, x + s - 1, y + s], radius=2, fill=body,
                                outline=BOX_EDGE)
            d.rectangle([x + s * 0.4, y - 1, x + s * 0.6, y + 2], fill=BOX_EDGE)
        elif kind == "status":
            d.rounded_rectangle([x + 2, y + 2, x + s - 1, y + s], radius=2,
                                fill=(232, 200, 120), outline=BOX_EDGE)
            d.rectangle([x + s * 0.42, y, x + s * 0.58, y + 3], fill=BOX_EDGE)
        elif kind == "revive":
            d.polygon([(x + s / 2, y), (x + s, y + s / 2), (x + s / 2, y + s),
                       (x, y + s / 2)], fill=(250, 216, 96), outline=BOX_EDGE)
        elif kind == "battle":
            d.polygon([(x + 1, y + s), (x + s * 0.35, y + 1), (x + s * 0.65, y + 1),
                       (x + s - 1, y + s)], fill=(232, 128, 96), outline=BOX_EDGE)
        elif kind == "berry":
            d.ellipse([x + 1, y + 1, x + s - 1, y + s - 1], fill=(232, 96, 112),
                      outline=BOX_EDGE)
            d.line([x + s / 2, y + 1, x + s * 0.8, y], fill=(96, 152, 72))
        elif kind in ("stone", "evo"):
            d.polygon([(x, y + s), (x + s * 0.3, y), (x + s * 0.7, y + s * 0.35),
                       (x + s, y + s)], fill=(168, 176, 196), outline=BOX_EDGE)
        elif kind == "rare":
            d.polygon([(x + s / 2, y), (x + s, y + s * 0.6), (x + s / 2, y + s),
                       (x, y + s * 0.6)], fill=(180, 140, 232), outline=BOX_EDGE)
        else:
            d.rounded_rectangle([x + 1, y + 1, x + s - 1, y + s - 1], radius=2,
                                fill=(198, 168, 112), outline=BOX_EDGE)

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
def render_party(
    mons: list[dict],
    *,
    title: str = "队伍",
    money: int = 0,
    box_count: int = 0,
    badges: int = 0,
    scale: int = SCALE_DEFAULT,
) -> bytes:
    """队伍界面:6 行,每行 [精灵图][名字 Lv 性别][血条 + 数值][异常]。

    mons: [{species, name, level, cur_hp, max_hp, status, gender, exp_pct}]
    """
    try:
        sc = Screen(scale=scale)
        sc.title_bar(title, right=f"{money:,}₽")
        top = 19
        row_h = 21
        for i in range(6):
            y0 = top + i * row_h
            box = (5, y0, 235, y0 + row_h - 1)
            sc.window(box, radius=2, shadow=(i == 0))
            if i >= len(mons):
                sc.text(14, y0 + 5, f"{i + 1}. —", size=8.5, fill=TEXT_DIM)
                continue
            mon = mons[i]
            sc.sprite(str(mon.get("species") or ""), ground=(22, y0 + row_h - 2),
                      factor=0.62, bounds=(20, 18), back=False,
                      dim=_ratio(mon.get("cur_hp"), mon.get("max_hp")) <= 0)
            name = str(mon.get("name") or "?")
            sc.text(37, y0 + 2.5, name, size=8.6, fill=TEXT)
            w = sc.tw(name, 8.6) + 39
            gender, gcolor = gender_symbol(str(mon.get("gender") or ""))
            if gender:
                sc.text(w, y0 + 2.5, gender, size=8.6, fill=gcolor)
                w += 6
            sc.text(w + 1, y0 + 2.5, f"Lv{int(mon.get('level') or 0)}", size=8.6,
                    fill=TEXT)
            sc.hp_bar((70, y0 + 12.5, 118, 4.5),
                      _ratio(mon.get("cur_hp"), mon.get("max_hp")), tag=True)
            sc.text_right(232, y0 + 10.5,
                          f"{int(mon.get('cur_hp') or 0)}/{int(mon.get('max_hp') or 0)}",
                          size=7.6, fill=TEXT)
            if mon.get("status"):
                sc.status_chip(192, y0 + 2, str(mon["status"]))
            if mon.get("item"):
                # 持有道具:名字前的小星标(别压到右侧 HP 数值上)
                sc.text(32, y0 + 2.5, "★", size=7.4, fill=(214, 160, 56))
        sc.footer(f"电脑 {box_count} 只 · 徽章 {badges} 枚 · /对战 出招时用序号换人")
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 队伍界面渲染失败: %s", e)
        return b""


# ══════════════════════════════════════════════════════════════════
# 界面:背包(FRLG 背包 + 口袋页签)
# ══════════════════════════════════════════════════════════════════
def render_bag(
    items: list[dict],
    *,
    money: int = 0,
    active_pocket: str = "items",
    selected: int = 0,
    pockets: list[tuple[str, str]] | None = None,
    scale: int = SCALE_DEFAULT,
) -> bytes:
    """背包界面:页签 + 条目列表 + 底部说明框。

    items: [{key, zh, count, desc, kind}] —— 只显示属于 active_pocket 的条目。
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
        # 列表
        shown = items[:6]
        sc.window((5, 31, 235, 112), radius=2)
        if not shown:
            sc.text(14, 34, "这个口袋是空的。", size=8.5, fill=TEXT_DIM)
        for i, it in enumerate(shown):
            y0 = 33 + i * 13
            if i == selected:
                sc.highlight((7, y0, 233, y0 + 12))
            sc.item_icon(str(it.get("kind") or ""), 10, y0 + 1.5, 9)
            sc.text(23, y0 + 1.5, str(it.get("zh") or it.get("key") or ""), size=8.4,
                    fill=TEXT)
            sc.text_right(228, y0 + 1.5, f"×{int(it.get('count') or 0)}", size=8.4,
                          fill=TEXT)
        # 说明框
        sc.window((5, 113, 235, 144), radius=2)
        cur = shown[selected] if 0 <= selected < len(shown) else (shown[0] if shown else {})
        lines = sc.wrap(str(cur.get("desc") or "—"), 220, size=8, limit=3)
        for i, ln in enumerate(lines):
            sc.text(10, 116 + i * 9, ln, size=8, fill=TEXT)
        sc.footer("◆ 用 /商店 买 卖 · 对战时 /对战 item <道具>")
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
        sc.window((5, 19, 128, 143), radius=3)
        sc.window((132, 19, 235, 143), radius=3)
        sc.ball(14, 26, 7)
        sc.text(38, 26, str(info.get("name") or "训练家"), size=11, fill=TEXT)
        sc.text(38, 40, f"ID No.{info.get('id_no') or '00000'}", size=8, fill=TEXT_DIM)
        rows = [
            ("金钱", f"{int(info.get('money') or 0):,}₽"),
            ("地区", str(info.get("region") or "?")),
            ("当前", str(info.get("location") or "?")),
            ("第几天", f"{info.get('play_day') or 0} 天"),
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
            on = bool(b[1]) if isinstance(b, (list, tuple)) and len(b) >= 2 else False
            name = str(b[0]) if isinstance(b, (list, tuple)) and len(b) >= 2 and b[0] else ""
            sc.badge(gx, gy, 13, on=on)
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
        sc.window((5, 115, 235, 145), radius=3)
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
