"""对战画面渲染:把一场宝可梦对战画成 GBA/DS 风格的 PNG。

只依赖 Pillow(以及标准库);字体/精灵图都用仓库里已有的本地文件,
运行时不联网。任何一步出错都吞掉并返回 ``b""``,调用方回退到纯文本。

逻辑画布 240x160,按 ``scale``(默认 3)放大成 720x480 的实际图片。
"""

from __future__ import annotations

import contextlib
import io
import os
import random
from functools import lru_cache

from astrbot.api import logger

try:  # Pillow 缺失时模块仍可导入,只是 available() 为 False
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    _PIL_OK = True
except Exception:  # pragma: no cover - 运行环境没有 Pillow
    _PIL_OK = False

# ── 资源路径 ─────────────────────────────────────────────────────
_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts")
_MAIN_FONT = os.path.join(_FONT_DIR, "OPPOSans-Regular.ttf")
_SYMBOL_FONT = os.path.join(_FONT_DIR, "Symbola_hint.ttf")

# ── 逻辑画布与布局(全部用"逻辑像素",绘制时再乘 scale)────────────
LOGICAL_W = 240
LOGICAL_H = 160
BASE_SCALE = 3
HORIZON = 0.58

FOE_BOX = (6.0, 20.0, 112.0, 32.0)
PLAYER_BOX = (112.0, 74.0, 124.0, 36.0)
MSG_BOX = (4.0, 112.0, 232.0, 44.0)

FOE_HP_BAR = (14.0, 40.0, 96.0, 6.0)
PLAYER_HP_BAR = (120.0, 90.0, 78.0, 6.0)
PLAYER_EXP_BAR = (120.0, 100.0, 106.0, 4.0)

PLAYER_SPRITE_CENTER = (62.0, 68.0)
FOE_SPRITE_CENTER = (176.0, 58.0)
PLAYER_PLATFORM = (62.0, 100.0, 90.0, 20.0)
FOE_PLATFORM = (176.0, 82.0, 74.0, 18.0)

# ── 调色板 ───────────────────────────────────────────────────────
_WEATHER_SKY = {
    "": ((150, 205, 245), (206, 236, 246)),
    "sun": ((255, 210, 138), (255, 176, 104)),
    "rain": ((112, 132, 158), (162, 178, 194)),
    "sand": ((238, 210, 152), (222, 188, 130)),
    "snow": ((198, 218, 238), (228, 238, 248)),
}
_WEATHER_GROUND = {
    "": ((124, 198, 122), (86, 158, 96)),
    "sun": ((226, 198, 126), (196, 158, 92)),
    "rain": ((108, 138, 118), (82, 112, 98)),
    "sand": ((206, 178, 118), (174, 144, 92)),
    "snow": ((232, 240, 248), (200, 214, 230)),
}
_PLATFORM_FILL = {
    "": (196, 236, 178),
    "sun": (240, 214, 158),
    "rain": (150, 176, 160),
    "sand": (216, 190, 138),
    "snow": (236, 244, 252),
}
_WEATHER_ZH = {"sun": "大晴天", "rain": "下雨", "sand": "沙暴", "snow": "下雪"}
_WEATHER_BG = {
    "sun": (255, 196, 96),
    "rain": (150, 178, 214),
    "sand": (214, 178, 108),
    "snow": (206, 230, 248),
}
_TERRAIN_ZH = {
    "electricterrain": "电气场地",
    "grassyterrain": "青草场地",
    "mistyterrain": "薄雾场地",
    "psychicterrain": "精神场地",
}
_TERRAIN_BG = {
    "electricterrain": (250, 216, 84),
    "grassyterrain": (144, 214, 124),
    "mistyterrain": (226, 186, 232),
    "psychicterrain": (204, 148, 224),
}
# (缩写, 底色, 字色)
_STATUS = {
    "brn": ("灼", (232, 120, 60), (255, 255, 255)),
    "par": ("麻", (240, 208, 64), (58, 48, 16)),
    "psn": ("毒", (168, 96, 200), (255, 255, 255)),
    "psl": ("毒", (168, 96, 200), (255, 255, 255)),
    "tox": ("毒", (140, 72, 190), (255, 255, 255)),
    "slp": ("眠", (132, 132, 142), (255, 255, 255)),
    "frz": ("冻", (120, 200, 230), (26, 58, 78)),
}


# ── 字体 ─────────────────────────────────────────────────────────
def _fs(logical: float, scale: int) -> int:
    return max(8, round(logical * scale))


@lru_cache(maxsize=64)
def _load_font(size: int):
    """按像素取字体(模块级缓存;主字体优先,符号字体兜底)。"""
    size = max(6, int(size))
    if not _PIL_OK:
        return None
    for path in (_MAIN_FONT, _SYMBOL_FONT):
        if not path or not os.path.exists(path):
            continue
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    with contextlib.suppress(Exception):
        return ImageFont.load_default(size)
    return None


@lru_cache(maxsize=64)
def _load_symbol_font(size: int):
    size = max(6, int(size))
    if not _PIL_OK:
        return None
    for path in (_SYMBOL_FONT, _MAIN_FONT):
        if not path or not os.path.exists(path):
            continue
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return _load_font(size)


@lru_cache(maxsize=1)
def _probe() -> bool:
    if not _PIL_OK:
        return False
    if not os.path.exists(_MAIN_FONT):
        return False
    f = _load_font(16)
    return f is not None


def available() -> bool:
    """Pillow + 字体可用时返回 True(否则调用方回退纯文本)。"""
    try:
        return bool(_probe())
    except Exception as e:
        logger.debug(f"battle_render.available 探测失败: {e}")
        return False


# ── 小工具 ───────────────────────────────────────────────────────
def _s(v: float, scale: int) -> int:
    return round(v * scale)


def _box_d(box, scale: int) -> tuple[int, int, int, int]:
    x, y, w, h = box
    return (_s(x, scale), _s(y, scale), _s(x + w, scale), _s(y + h, scale))


def _as_int(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def _as_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _blend(c1, c2, t: float):
    t = _clamp01(t)
    return tuple(round(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _lighten(c, t: float = 0.35):
    return tuple(round(v + (255 - v) * t) for v in c)


def _text_w(font, text: str) -> float:
    if font is None:
        return float(len(text or "") * 8)
    try:
        return float(font.getlength(text or ""))
    except Exception:
        pass
    try:
        return float(font.getbbox(text or "")[2])
    except Exception:
        return float(len(text or "") * 8)


def _ellipsize(font, text: str, max_w: float) -> str:
    text = str(text or "")
    if max_w <= 0:
        return ""
    if _text_w(font, text) <= max_w:
        return text
    ell = "…"
    ew = _text_w(font, ell)
    out = ""
    for ch in text:
        if _text_w(font, out + ch) + ew > max_w:
            break
        out += ch
    return (out + ell) if out else ell


def _wrap(font, text: str, max_w: float) -> list[str]:
    """按字符折行(CJK 没有词边界),供消息框使用。"""
    text = str(text or "")
    if max_w <= 0:
        return [text] if text else []
    lines: list[str] = []
    cur = ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if cur and _text_w(font, cur + ch) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def _hp_color(ratio: float):
    if ratio > 0.5:
        return (72, 208, 80)
    if ratio > 0.2:
        return (248, 208, 48)
    return (240, 80, 72)


# ── 背景 ─────────────────────────────────────────────────────────
def _paint_background(img, d, scale: int, weather: str) -> None:
    w, h = img.size
    sky = _WEATHER_SKY.get(weather, _WEATHER_SKY[""])
    ground = _WEATHER_GROUND.get(weather, _WEATHER_GROUND[""])
    hz = max(1, int(h * HORIZON))
    for y in range(h):
        if y < hz:
            c = _blend(sky[0], sky[1], y / max(1, hz - 1))
        else:
            c = _blend(ground[0], ground[1], (y - hz) / max(1, h - hz - 1))
        d.line([(0, y), (w, y)], fill=(*c, 255))

    rng = random.Random(20240501)
    if weather == "rain":
        lw = max(1, scale // 3)
        for _ in range(90):
            x = rng.randrange(0, w)
            y = rng.randrange(0, h)
            ln = rng.randrange(max(3, 6 * scale), max(4, 16 * scale))
            d.line(
                [(x, y), (x - int(2.5 * scale), y + ln)],
                fill=(205, 220, 238, 160),
                width=lw,
            )
    elif weather == "sand":
        for _ in range(600 * scale):
            x = rng.randrange(0, w)
            y = rng.randrange(0, h)
            d.point((x, y), fill=(158, 126, 72, 210))
    elif weather == "snow":
        rr = max(1, scale // 2)
        for _ in range(120 * scale):
            x = rng.randrange(0, w)
            y = rng.randrange(0, h)
            d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=(255, 255, 255, 215))


def _draw_platform(d, logical_box, scale: int, weather: str) -> None:
    cx, cy, pw, ph = logical_box
    fill = _PLATFORM_FILL.get(weather, _PLATFORM_FILL[""])
    box = [
        _s(cx - pw / 2, scale),
        _s(cy - ph / 2, scale),
        _s(cx + pw / 2, scale),
        _s(cy + ph / 2, scale),
    ]
    d.ellipse(box, fill=fill, outline=(70, 92, 62), width=max(1, _s(1.5, scale)))


# ── 精灵 ─────────────────────────────────────────────────────────
def _placeholder_sprite(img, center, size_px, scale: int) -> None:
    cx, cy = center
    r = size_px // 2
    box = [cx - r, cy - r, cx + r, cy + r]
    d = ImageDraw.Draw(img)
    d.ellipse(box, fill=(120, 126, 140, 255), outline=(44, 48, 58, 255),
              width=max(1, _s(1.5, scale)))
    hr = max(2, int(r * 0.45))
    d.ellipse(
        [cx - hr, cy - r + int(r * 0.18), cx + hr, cy - r + int(r * 0.18) + 2 * hr],
        fill=(150, 156, 170, 255),
        outline=(44, 48, 58, 255),
        width=max(1, _s(1.2, scale)),
    )


def _draw_sprite(base, species: str, center, factor: float, mirror: bool,
                 scale: int) -> None:
    cx = _s(center[0], scale)
    cy = _s(center[1], scale)
    size_px = max(8, round(96 * factor * scale / BASE_SCALE))
    path = ""
    with contextlib.suppress(Exception):
        from .sprites import sprite_path

        path = sprite_path(species) or ""
    if path:
        im = None
        with contextlib.suppress(OSError, ValueError):
            im = Image.open(path).convert("RGBA").resize(
                (size_px, size_px), Image.NEAREST
            )
        if im is not None:
            if mirror:
                im = ImageOps.mirror(im)
            base.alpha_composite(im, (cx - size_px // 2, cy - size_px // 2))
            return
    _placeholder_sprite(base, (cx, cy), size_px, scale)


# ── 面板 / 胶囊 / 血条 ───────────────────────────────────────────
def _panel(d, box_d, scale: int, fill, outline=(36, 40, 48),
           width_l: float = 2.0, radius_l: float = 7.0) -> None:
    d.rounded_rectangle(
        box_d,
        radius=_s(radius_l, scale),
        fill=fill,
        outline=outline,
        width=max(1, _s(width_l, scale)),
    )


def _draw_chip(d, x: int, y: int, w: int, h: int, text: str, bg, font,
               scale: int, fg=(28, 30, 36)) -> None:
    d.rounded_rectangle(
        [x, y, x + w, y + h],
        radius=_s(3, scale),
        fill=bg,
        outline=(30, 32, 40),
        width=max(1, _s(1, scale)),
    )
    d.text((x + w // 2, y + h // 2), text, font=font, fill=fg, anchor="mm")


def _status_chip(d, box, scale: int, status: str, font):
    x, y, w, _h = box
    label, bg, fg = _STATUS.get(status, ("", None, None))
    if not label:
        return 0
    tw = round(_text_w(font, label))
    ch = _s(11, scale)
    cw = tw + _s(8, scale)
    px = _s(x + w, scale) - _s(4, scale) - cw
    py = _s(y, scale) + _s(3, scale)
    _draw_chip(d, px, py, cw, ch, label, bg, font, scale, fg)
    return cw + _s(3, scale)


def _mon_fields(mon) -> dict:
    if not isinstance(mon, dict):
        mon = {}
    species = str(mon.get("species") or "").strip()
    name = str(mon.get("name") or mon.get("nickname") or "").strip() or species or "???"
    max_hp = _as_int(mon.get("max_hp"), 0)
    cur_hp = _as_int(mon.get("cur_hp"), 0)
    ratio = _clamp01(cur_hp / max_hp) if max_hp > 0 else 0.0
    return {
        "species": species,
        "name": name,
        "level": _as_int(mon.get("level"), 0),
        "cur_hp": cur_hp,
        "max_hp": max_hp,
        "ratio": ratio,
        "status": str(mon.get("status") or "").strip().lower(),
        "gender": str(mon.get("gender") or "").strip().upper(),
        "exp_pct": _clamp01(_as_float(mon.get("exp_pct"), 0.0) / 100.0),
    }


def _gender_glyph(gender: str) -> str:
    if gender in ("M", "MALE", "♂"):
        return "♂"
    if gender in ("F", "FEMALE", "♀"):
        return "♀"
    return ""


def _draw_name_row(d, box, scale: int, mon: dict, *, font, show_level: bool,
                   gender_font, chip_w: int, chip_font) -> None:
    x, y, w, _h = box
    px = _s(x, scale) + _s(6, scale)
    py = _s(y, scale) + _s(4, scale)
    avail = _s(w, scale) - _s(12, scale) - chip_w
    glyph = _gender_glyph(mon["gender"])
    g_w = (int(_text_w(gender_font, glyph)) + _s(2, scale)) if glyph else 0
    lv_txt = f"Lv{mon['level']}" if (show_level and mon["level"]) else ""
    lv_w = int(_text_w(font, " " + lv_txt)) if lv_txt else 0
    name = _ellipsize(font, mon["name"], max(0, avail - g_w - lv_w))
    d.text((px, py), name, font=font, fill=(36, 40, 48))
    cur = px + round(_text_w(font, name))
    if lv_txt:
        d.text((cur, py), " " + lv_txt, font=font, fill=(60, 66, 84))
        cur += lv_w
    if glyph:
        gcol = (58, 118, 214) if glyph == "♂" else (226, 90, 148)
        d.text((cur + _s(1, scale), py), glyph, font=gender_font, fill=gcol)


def _draw_bar(d, logical_box, ratio: float, scale: int, fill) -> None:
    x0, y0, x1, y1 = _box_d(logical_box, scale)
    ow = max(1, _s(1, scale))
    d.rectangle([x0, y0, x1, y1], fill=(70, 72, 80), outline=(30, 32, 40), width=ow)
    ix0, iy0, ix1, iy1 = x0 + ow, y0 + ow, x1 - ow, y1 - ow
    fw = round(max(0, ix1 - ix0) * _clamp01(ratio))
    if fw > 0:
        d.rectangle([ix0, iy0, ix0 + fw, iy1], fill=fill)
        d.line([(ix0, iy0), (ix0 + fw - 1, iy0)], fill=_lighten(fill), width=ow)


def _draw_hp_bar(d, logical_box, ratio: float, scale: int) -> None:
    _draw_bar(d, logical_box, ratio, scale, _hp_color(_clamp01(ratio)))


def _draw_exp_bar(d, logical_box, ratio: float, scale: int) -> None:
    _draw_bar(d, logical_box, ratio, scale, (72, 196, 236))


# ── 信息框 ───────────────────────────────────────────────────────
def _draw_foe_box(d, scale: int, foe: dict, fonts) -> None:
    _panel(d, _box_d(FOE_BOX, scale), scale, fill=(250, 250, 244), radius_l=6)
    chip_w = _status_chip(d, FOE_BOX, scale, foe["status"], fonts["chip"])
    _draw_name_row(
        d, FOE_BOX, scale, foe,
        font=fonts["name"], show_level=True,
        gender_font=fonts["symbol"], chip_w=chip_w, chip_font=fonts["chip"],
    )
    _draw_hp_bar(d, FOE_HP_BAR, foe["ratio"], scale)


def _draw_player_box(d, scale: int, my: dict, fonts) -> None:
    _panel(d, _box_d(PLAYER_BOX, scale), scale, fill=(250, 250, 244), radius_l=6)
    chip_w = _status_chip(d, PLAYER_BOX, scale, my["status"], fonts["chip"])
    _draw_name_row(
        d, PLAYER_BOX, scale, my,
        font=fonts["name"], show_level=True,
        gender_font=fonts["symbol"], chip_w=chip_w, chip_font=fonts["chip"],
    )
    _draw_hp_bar(d, PLAYER_HP_BAR, my["ratio"], scale)
    _draw_exp_bar(d, PLAYER_EXP_BAR, my["exp_pct"], scale)
    # HP 数字:血条右侧
    if my["max_hp"] > 0:
        num = f"{my['cur_hp']}/{my['max_hp']}"
        d.text(
            (_s(PLAYER_BOX[0] + PLAYER_BOX[2] - 4, scale), _s(PLAYER_HP_BAR[1] + 3, scale)),
            num, font=fonts["num"], fill=(40, 44, 54), anchor="rm",
        )


# ── 天气/场地胶囊 ────────────────────────────────────────────────
def _draw_chips(d, scale: int, width: int, weather: str, terrain: str) -> None:
    items = []
    if weather in _WEATHER_ZH:
        items.append((_WEATHER_ZH[weather], _WEATHER_BG[weather]))
    if terrain in _TERRAIN_ZH:
        items.append((_TERRAIN_ZH[terrain], _TERRAIN_BG[terrain]))
    if not items:
        return
    font = _load_font(_fs(8.0, scale))
    if font is None:
        return
    gap = _s(3, scale)
    ch = _s(12, scale)
    widths = [round(_text_w(font, t)) + _s(10, scale) for t, _ in items]
    total = sum(widths) + gap * (len(items) - 1)
    x = (width - total) // 2
    y = _s(4, scale)
    for i, (text, bg) in enumerate(items):
        _draw_chip(d, x, y, widths[i], ch, text, bg, font, scale)
        x += widths[i] + gap


def _draw_location(d, scale: int, width: int, location: str, font) -> None:
    loc = str(location or "").strip()
    if not loc or font is None:
        return
    pad = _s(5, scale)
    maxw = _s(92, scale)
    text = _ellipsize(font, loc, maxw)
    tw = round(_text_w(font, text))
    x1 = width - _s(4, scale)
    x0 = x1 - tw - pad * 2
    y0 = _s(4, scale)
    d.rounded_rectangle(
        [x0, y0, x1, y0 + _s(12, scale)],
        radius=_s(4, scale),
        fill=(250, 250, 244),
        outline=(60, 64, 74),
        width=max(1, _s(1, scale)),
    )
    d.text((x0 + pad, y0 + _s(6, scale)), text, font=font, fill=(48, 52, 62), anchor="lm")


# ── 精灵球队伍指示 ───────────────────────────────────────────────
def _draw_pokeball(d, cx: float, cy: float, r: float, alive: bool, scale: int) -> None:
    R = _s(r, scale)
    X, Y = _s(cx, scale), _s(cy, scale)
    ow = max(1, _s(1, scale))
    box = [X - R, Y - R, X + R, Y + R]
    if alive:
        d.pieslice(box, 180, 360, fill=(226, 64, 60))
        d.pieslice(box, 0, 180, fill=(246, 246, 246))
    else:
        d.ellipse(box, fill=(84, 86, 94))
    d.ellipse(box, outline=(28, 30, 36), width=ow)
    d.rectangle([X - R, Y - ow, X + R, Y + ow], fill=(28, 30, 36))
    cr = max(ow, _s(2.2, scale))
    d.ellipse(
        [X - cr, Y - cr, X + cr, Y + cr],
        fill=(246, 246, 246) if alive else (124, 126, 134),
        outline=(28, 30, 36),
        width=ow,
    )
    if not alive:
        xr = int(R * 0.72)
        d.line([(X - xr, Y - xr), (X + xr, Y + xr)], fill=(240, 80, 72), width=ow)
        d.line([(X - xr, Y + xr), (X + xr, Y - xr)], fill=(240, 80, 72), width=ow)


def _draw_party(d, scale: int, party, msg_box) -> int:
    """在消息框左侧画队伍球,返回需要给文字预留的像素宽度。"""
    if not party:
        return 0
    n = min(6, len(party))
    step = 14.0
    start_x = msg_box[0] + 9.0
    cy = msg_box[1] + msg_box[3] / 2.0
    for i in range(n):
        mon = party[i] if isinstance(party[i], dict) else {}
        alive = _as_int(mon.get("cur_hp"), 1) > 0
        _draw_pokeball(d, start_x + i * step, cy, 6.0, alive, scale)
    return _s(n * step + 2, scale)


# ── 消息框 ───────────────────────────────────────────────────────
def _draw_message(d, scale: int, title: str, log, party_w: int) -> None:
    x0, y0, x1, y1 = _box_d(MSG_BOX, scale)
    d.rounded_rectangle(
        [x0, y0, x1, y1],
        radius=_s(8, scale),
        fill=(252, 252, 248),
        outline=(28, 30, 36),
        width=max(2, _s(2.5, scale)),
    )
    font = _load_font(_fs(8.6, scale))
    lh = _s(9.5, scale)
    tx = x0 + _s(6, scale) + party_w
    ty = y0 + _s(5, scale)
    maxw = max(0, x1 - tx - _s(6, scale))
    raw: list[str] = []
    if title:
        raw.append("◆ " + str(title))
    raw.extend(str(t) for t in (log or [])[-3:])
    lines: list[str] = []
    for t in raw:
        lines.extend(_wrap(font, t, maxw))
    for i, ln in enumerate(lines[-4:]):
        d.text((tx, ty + i * lh), ln, font=font, fill=(36, 40, 48))


# ── 组装 ─────────────────────────────────────────────────────────
def _build(my: dict, foe: dict, log: list[str], *, title: str, weather: str,
           terrain: str, location: str, my_party, turn: int, scale: int):
    w = LOGICAL_W * scale
    h = LOGICAL_H * scale
    img = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)

    _paint_background(img, d, scale, weather)
    _draw_platform(d, FOE_PLATFORM, scale, weather)
    _draw_platform(d, PLAYER_PLATFORM, scale, weather)

    my_f = _mon_fields(my)
    foe_f = _mon_fields(foe)
    _draw_sprite(img, foe_f["species"], FOE_SPRITE_CENTER, 1.8, False, scale)
    _draw_sprite(img, my_f["species"], PLAYER_SPRITE_CENTER, 2.2, True, scale)

    fonts = {
        "name": _load_font(_fs(9.6, scale)),
        "num": _load_font(_fs(7.8, scale)),
        "chip": _load_font(_fs(7.8, scale)),
        "symbol": _load_symbol_font(_fs(9.6, scale)),
        "loc": _load_font(_fs(7.8, scale)),
    }

    _draw_foe_box(d, scale, foe_f, fonts)
    _draw_player_box(d, scale, my_f, fonts)
    _draw_chips(d, scale, w, weather, terrain)
    _draw_location(d, scale, w, location, fonts["loc"])

    party_w = _draw_party(d, scale, my_party, MSG_BOX)
    _draw_message(d, scale, title, log, party_w)
    # 队伍球再画一次保证盖在消息框上
    _draw_party(d, scale, my_party, MSG_BOX)
    return img


# ── 对外 API ─────────────────────────────────────────────────────
def render_battle(
    my: dict,
    foe: dict,
    log: list[str],
    *,
    title: str = "",
    weather: str = "",
    terrain: str = "",
    location: str = "",
    my_party: list[dict] | None = None,
    turn: int = 0,
    out_path: str = "",
    scale: int = 3,
) -> bytes:
    """把一场对战渲染成 PNG。返回 PNG 字节;out_path 非空时同时写文件。

    任何失败都返回 ``b""``(绝不抛异常),调用方会回退到纯文本。
    """
    try:
        if not _PIL_OK or not available():
            return b""
        scale = max(1, int(scale or 1))
        log = [str(x) for x in (log or [])]
        img = _build(
            my if isinstance(my, dict) else {},
            foe if isinstance(foe, dict) else {},
            log,
            title=str(title or ""),
            weather=str(weather or "").strip().lower(),
            terrain=str(terrain or "").strip().lower(),
            location=str(location or ""),
            my_party=my_party or [],
            turn=_as_int(turn, 0),
            scale=scale,
        )
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        data = buf.getvalue()
        if out_path:
            try:
                parent = os.path.dirname(os.path.abspath(out_path))
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(out_path, "wb") as f:
                    f.write(data)
            except OSError as e:
                logger.debug(f"battle_render: 写出 {out_path} 失败: {e}")
        return data
    except Exception as e:
        logger.debug(f"battle_render 渲染失败: {e}")
        return b""


__all__ = [
    "FOE_BOX",
    "FOE_HP_BAR",
    "LOGICAL_H",
    "LOGICAL_W",
    "MSG_BOX",
    "PLAYER_BOX",
    "PLAYER_EXP_BAR",
    "PLAYER_HP_BAR",
    "available",
    "render_battle",
]
