"""宝可梦对战界面渲染(仿 GBA《火红/叶绿》)。

设计
====
1. **图形走像素**:所有方块/血条/场地都在 240×160 的逻辑画布上用整数坐标绘制,
   最后 `Image.NEAREST` 放大(默认 3 倍 → 720×480),得到真正的像素质感。
2. **文字走清晰**:中文小字号在 10px 下会糊,所以文字在放大后的画布上绘制,
   保证聊天里可读。
3. **我方用背面图**(`sprites_back/`,和正作一致),敌方用正面图。
4. 布局、配色、控件都对着 FRLG 实战截图还原:
   象牙色信息框 + 深绿描边、血条带精灵球图标与「HP」标签及刻度、
   我方额外显示 HP 数值与「EXP」经验条、底部暗红框青绿底的对话框、
   背景是带地平线的场地 + 两个椭圆站台。

任何异常都返回 `b""`,调用方会回退成纯文本 —— 渲染永远不该中断游戏。
"""

from __future__ import annotations

import os
from functools import lru_cache
from io import BytesIO

from astrbot.api import logger

from .sprites import back_sprite_path, sprite_path

try:
    from PIL import ImageFont
except ImportError:  # 没装 Pillow 时 available() 返回 False
    ImageFont = None  # type: ignore[assignment]

LOGICAL_W = 240
LOGICAL_H = 160
SCALE_DEFAULT = 3

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts")
FONT_MAIN = os.path.join(FONT_DIR, "OPPOSans-Regular.ttf")
FONT_SYMBOL = os.path.join(FONT_DIR, "Symbola_hint.ttf")

# ── 调色板(对着 FRLG 截图取样)──────────────────────────────────
BG_TOP = (242, 232, 180)
BG_GROUND = (250, 243, 205)
ARENA_FILL = (250, 246, 218)
ARENA_LINE = (222, 206, 156)
PLATFORM_FILL = (250, 248, 228)
PLATFORM_LINE = (212, 194, 144)

BOX_FILL = (250, 249, 227)
BOX_EDGE = (58, 74, 58)
BOX_EDGE_2 = (150, 168, 138)
BOX_SHADOW = (196, 190, 150)
TEXT = (52, 52, 44)
TEXT_DIM = (110, 108, 92)

MSG_FRAME = (162, 44, 34)
MSG_FRAME_HI = (214, 100, 82)
MSG_FILL = (108, 178, 162)
MSG_TEXT = (250, 250, 248)
MSG_SHADOW = (46, 92, 82)

HP_TRACK = (150, 46, 38)
HP_TRACK_HI = (196, 96, 84)
HP_TAG_BG = (168, 48, 40)
HP_TAG_FG = (250, 224, 96)
EXP_TAG_BG = (246, 208, 56)
EXP_TAG_FG = (72, 56, 16)
EXP_TRACK = (86, 92, 110)
EXP_FILL = (86, 202, 236)
MALE = (64, 120, 244)
FEMALE = (248, 96, 160)
SHADOW = (176, 172, 132)

STATUS_STYLE = {
    "brn": ("灼", (224, 96, 48)),
    "par": ("麻", (232, 192, 48)),
    "psn": ("毒", (168, 88, 200)),
    "tox": ("剧", (140, 60, 180)),
    "slp": ("眠", (120, 124, 148)),
    "frz": ("冰", (96, 196, 232)),
}
# 天气会改变场地配色(和正作一样:下雨变阴、晴天偏暖、沙暴发黄、下雪发蓝)
WEATHER_PALETTE = {
    "rain": ((176, 188, 206), (194, 202, 212), (204, 210, 220), (176, 190, 208)),
    "sun": ((252, 226, 148), (252, 240, 192), (252, 240, 196), (222, 202, 150)),
    "sand": ((228, 196, 124), (238, 212, 150), (240, 218, 162), (214, 184, 118)),
    "snow": ((214, 226, 240), (232, 240, 248), (238, 244, 250), (208, 218, 232)),
}
WEATHER_STYLE = {
    "sun": "大晴天",
    "rain": "下雨",
    "sand": "沙暴",
    "snow": "下雪",
}
TERRAIN_STYLE = {
    "electricterrain": "电气场地",
    "grassyterrain": "青草场地",
    "mistyterrain": "薄雾场地",
    "psychicterrain": "精神场地",
}

# ── 逻辑布局 ─────────────────────────────────────────────────────
FOE_BOX = (10, 8, 108, 40)
MY_BOX = (132, 68, 236, 110)
# 血条一行 = [精灵球][HP 标签][条];三者依次排开,互不重叠
ENEMY_BALL = (14, 26, 3)          # (x, y, r)
ENEMY_HP_TAG = (20, 25, 33, 34)   # (x0, y0, x1, y1)
ENEMY_HP_BAR = (35, 26, 67, 6)    # (x, y, w, h)
PLAYER_BALL = (136, 86, 3)
PLAYER_HP_TAG = (142, 85, 155, 94)
PLAYER_HP_BAR = (157, 86, 68, 6)  # ← 测试取样点,须为"填充从左侧开始"的条
EXP_TAG = (136, 99, 151, 105)
EXP_BAR = (153, 100, 77, 3)

FOE_PLATFORM = (114, 48, 240, 76)
MY_PLATFORM = (0, 86, 112, 124)
# 精灵"落地线":(中心 x, 脚底 y)。取站台椭圆中部 —— 正作里宝可梦站在椭圆上。
FOE_GROUND = (177, 64)
MY_GROUND = (56, 120)
# 两侧用**各自固定**的缩放(不是"撑满框"),这样同一侧相对体型得以保留:
# 皮卡丘仍然比大岩蛇小,只是脚底都踩在同一条线上。
FOE_SCALE = 0.62
MY_SCALE = 1.05

# 对话框:高度**自适应**行数(从底部向上长),不再写死 112~158。
# 之前固定 4 行 + 每行 10px = 40px,而框内只有 38px,长文本会顶破边框。
MSG_EDGE_X = (2, 238)
MSG_BOTTOM = 158
# 上限必须在我方信息框(MY_BOX)底边**之下** —— 旧值 88 在 68~110 的框里,
# 于是长战报把对话框一路顶到我方血条/经验条上面(实测 6 行时完全盖住)。
# 取 MY_BOX 底边 +2 作为硬上限:放得下 4 行(审计代理实测真实对局最多 4 行)。
MSG_TOP_MAX = MY_BOX[3] + 2       # = 112
MSG_LINE_H = 9.6          # 逻辑行高
MSG_MAX_LINES = max(1, int((MSG_BOTTOM - 4 - MSG_TOP_MAX) // MSG_LINE_H))


@lru_cache(maxsize=16)
def _font(size: int):
    if ImageFont is None:
        return None
    for path in (FONT_MAIN, FONT_SYMBOL):
        if not os.path.exists(path):
            continue
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return None


def available() -> bool:
    """Pillow 与内置字体是否可用。"""
    try:
        import PIL  # noqa: F401
        from PIL import Image, ImageDraw  # noqa: F401
    except ImportError:
        return False
    return _font(20) is not None


def sprite_for(species: str, *, back: bool = False, base: str = "") -> str:
    """取本地精灵图;back=True 优先背面图。"""
    return back_sprite_path(species, base) if back else sprite_path(species)


# ── 绘制辅助 ─────────────────────────────────────────────────────
def _load_sprite(path: str, factor: float, bounds: tuple[int, int]):
    """读图 → 裁掉透明边距 → 按固定倍率缩放。

    裁边距是关键:官方 96×96 图的底部透明边距从 10px 到 32px 不等
    (皮卡丘 26px、地鼠 32px、暴鲤龙 10px),若按画布底对齐,脚底会差出 20 多像素。
    """
    from PIL import Image

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
    max_w, max_h = bounds
    if img.width * scale > max_w or img.height * scale > max_h:
        scale = min(max_w / img.width, max_h / img.height)
    new = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    return img.resize(new, Image.NEAREST)


def _silhouette(size: tuple[int, int]):
    """缺图占位:灰色剪影 + 问号。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w, h = size
    d.ellipse([int(w * 0.16), int(h * 0.16), int(w * 0.84), int(h * 0.84)],
              fill=(168, 164, 140, 255), outline=(96, 94, 80, 255))
    d.rectangle([int(w * 0.36), int(h * 0.7), int(w * 0.64), int(h * 0.86)],
                fill=(168, 164, 140, 255))
    return img


def _wrap(font, text: str, max_w: float, *, limit: int = 3) -> list[str]:
    """按像素宽度折行(中文逐字折,英文按空格)。"""
    out: list[str] = []
    if font is None:
        return [str(text)[:24]]
    cur = ""
    for ch in str(text):
        probe = cur + ch
        if font.getlength(probe) > max_w and cur:
            out.append(cur)
            cur = ch
            if len(out) >= limit:
                return out
        else:
            cur = probe
    if cur:
        out.append(cur)
    return out[:limit]


def _chip(big, d, x: int, y: int, text: str, bg, fg, font, scale: int,
          align_right: bool = False) -> int:
    """画一个小标签,返回它的宽度。"""
    pad = 4 * scale
    w = int(font.getlength(text)) + pad * 2
    h = int(font.size * 1.5)
    if align_right:
        x = x - w
    d.rectangle([x, y, x + w, y + h], fill=bg, outline=BOX_EDGE, width=scale)
    d.text((x + pad, y + int(h * 0.16)), text, font=font, fill=fg,
            stroke_width=1, stroke_fill=MSG_SHADOW)
    return w


def _draw_box(d_small, box, *, fill=BOX_FILL, edge=BOX_EDGE, radius=3, shadow=True):
    x0, y0, x1, y1 = box
    if shadow:
        d_small.rounded_rectangle([x0 + 2, y0 + 2, x1 + 2, y1 + 2], radius=radius,
                                  fill=BOX_SHADOW)
    d_small.rounded_rectangle(box, radius=radius, fill=fill, outline=edge, width=2)
    d_small.rounded_rectangle([x0 + 2, y0 + 2, x1 - 2, y1 - 2], radius=max(1, radius - 1),
                              outline=BOX_EDGE_2, width=1)


def _draw_ball(d, x: int, y: int, r: int) -> None:
    """精灵球小图标。"""
    d.ellipse([x, y, x + r * 2, y + r * 2], fill=(250, 250, 245), outline=(48, 48, 44))
    d.pieslice([x, y, x + r * 2, y + r * 2], 180, 360, fill=(224, 64, 56))
    d.rectangle([x, y + r - 1, x + r * 2, y + r + 1], fill=(48, 48, 44))
    d.ellipse([x + r - 2, y + r - 2, x + r + 2, y + r + 2],
              fill=(250, 250, 245), outline=(48, 48, 44))


def _draw_hp_row(d, ball_xy, tag_box, bar_box, ratio: float) -> None:
    """一行血条:精灵球图标 + 「HP」标签 + 带刻度的血条。"""
    d.rectangle(tag_box, fill=HP_TAG_BG, outline=BOX_EDGE)
    _draw_ball(d, ball_xy[0], ball_xy[1], ball_xy[2])
    _draw_hp_bar(d, bar_box, ratio)


def _draw_hp_bar(d, box, ratio: float, *, tag: bool = False) -> None:
    """血条本体(带刻度)。"""
    x, y, w, h = box
    d.rounded_rectangle([x, y, x + w, y + h], radius=2, fill=HP_TRACK, outline=BOX_EDGE)
    fill_w = int((w - 2) * max(0.0, min(1.0, ratio)))
    if fill_w <= 0:
        return
    color = (86, 208, 88) if ratio > 0.5 else ((240, 200, 48) if ratio > 0.2 else (240, 88, 56))
    d.rectangle([x + 1, y + 1, x + fill_w, y + h - 1], fill=color)
    d.rectangle([x + 1, y + 1, x + fill_w, y + 1], fill=tuple(min(255, c + 40) for c in color))
    # 刻度(每 6px 一道暗线,模拟原作的格状血条)
    for tick in range(x + 6, x + w - 1, 6):
        d.line([tick, y + 1, tick, y + h - 1], fill=HP_TRACK_HI)


def _draw_exp_bar(d, box, pct: float) -> None:
    x, y, w, h = box
    d.rectangle([x, y, x + w, y + h], fill=EXP_TRACK)
    fill_w = int(w * max(0.0, min(100.0, pct)) / 100.0)
    if fill_w > 0:
        d.rectangle([x, y, x + fill_w, y + h], fill=EXP_FILL)


def _status_chip(d, x: int, y: int, status: str) -> None:
    style = STATUS_STYLE.get(str(status or ""))
    if not style:
        return
    d.rounded_rectangle([x, y, x + 13, y + 11], radius=2, fill=style[1], outline=BOX_EDGE)


# ── 主入口 ───────────────────────────────────────────────────────
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
    scale: int = SCALE_DEFAULT,
) -> bytes:
    """把一场对战渲染成 PNG 字节;失败返回 b""。"""
    try:
        from PIL import Image, ImageDraw

        scale = max(1, int(scale or SCALE_DEFAULT))
        my = dict(my or {})
        foe = dict(foe or {})
        log = [str(x) for x in (log or [])]
        party = list(my_party or [])
        W, H = LOGICAL_W * scale, LOGICAL_H * scale
        S = scale

        small = Image.new("RGB", (LOGICAL_W, LOGICAL_H), BG_TOP)
        d = ImageDraw.Draw(small)

        # ── 背景:天气配色 + 地平线 + 竞技场椭圆 + 两个站台 ──
        _draw_scene(d, weather)

        # ── 精灵(我方背面图):脚底统一踩在各自站台的落地线上 ──
        _paste_small(small, my, MY_GROUND, factor=MY_SCALE, bounds=(92, 96),
                     back=True, dim=not _alive(my))
        _paste_small(small, foe, FOE_GROUND, factor=FOE_SCALE, bounds=(64, 58),
                     back=False, dim=not _alive(foe))

        # ── 信息框与血条(逻辑层)──
        _draw_box(d, FOE_BOX)
        _draw_box(d, MY_BOX)
        _draw_hp_row(d, ENEMY_BALL, ENEMY_HP_TAG, ENEMY_HP_BAR, _ratio(foe))
        _draw_hp_row(d, PLAYER_BALL, PLAYER_HP_TAG, PLAYER_HP_BAR, _ratio(my))
        d.rectangle(EXP_TAG, fill=EXP_TAG_BG, outline=BOX_EDGE)
        _draw_exp_bar(d, EXP_BAR, float(my.get("exp_pct") or 0.0))
        _status_chip(d, FOE_BOX[2] - 18, FOE_BOX[1] + 4, foe.get("status") or "")
        _status_chip(d, MY_BOX[2] - 18, MY_BOX[1] + 4, my.get("status") or "")

        # ── 先算对话框内容(高度自适应)──
        f_name = _font(int(9.5 * S))
        f_small = _font(int(8.5 * S))
        f_ball = _font(int(6.5 * S))
        f_msg = _font(int(9 * S))

        mx0, mx1 = MSG_EDGE_X
        # 队伍球从 x=8 开始每只 +11,3 只就会压到正文(正文从 x=30 起)。
        # 按队伍数量把正文起始位置右移,保证球与文字不重叠。
        n_balls = min(6, max(1, len(my_party or [])))
        tx_pad = max(28, int(8 + n_balls * 11 + 4))
        tx_right = 6
        maxw = (mx1 - mx0 - tx_pad - tx_right) * S
        src_lines: list[str] = []
        if title:
            src_lines.append("◆ " + str(title))
        src_lines.extend(log[-3:])
        wrapped: list[str] = []
        for line in src_lines:
            wrapped.extend(_wrap(f_msg, line, maxw, limit=MSG_MAX_LINES))
        shown_lines = wrapped[-MSG_MAX_LINES:] or [""]
        box_h = 7 + len(shown_lines) * MSG_LINE_H + 2
        my1 = MSG_BOTTOM
        # 双保险:即使行数算多了也不许长到我方信息框上面
        my0 = max(int(my1 - box_h), MSG_TOP_MAX)

        # ── 对话框(暗红框 + 青绿底)──
        d.rounded_rectangle([mx0, my0, mx1, my1], radius=4, fill=MSG_FRAME)
        d.rounded_rectangle([mx0 + 2, my0 + 2, mx1 - 2, my1 - 2], radius=3,
                            outline=MSG_FRAME_HI)
        d.rounded_rectangle([mx0 + 4, my0 + 4, mx1 - 4, my1 - 4], radius=3, fill=MSG_FILL)

        # ── 队伍球(跟随对话框左端垂直居中)──
        bx = 8
        ball_y = my0 + max(2, (box_h - 8) / 2 - 1)
        for mon in party[:6]:
            alive = int(mon.get("cur_hp", 0) or 0) > 0
            _draw_ball(d, bx, ball_y, 4)
            if not alive:
                d.ellipse([bx, ball_y, bx + 8, ball_y + 8], fill=(120, 118, 108),
                          outline=BOX_EDGE)
                d.line([bx + 1, ball_y + 1, bx + 7, ball_y + 7], fill=(250, 250, 245),
                       width=1)
                d.line([bx + 7, ball_y + 1, bx + 1, ball_y + 7], fill=(250, 250, 245),
                       width=1)
            bx += 11

        # ── 放大(像素风)──
        big = small.resize((W, H), Image.NEAREST)
        d2 = ImageDraw.Draw(big)

        # 敌方信息
        _box_text(d2, foe, FOE_BOX, ENEMY_HP_BAR, f_name, f_small, f_ball, S, mine=False)
        # 我方信息
        _box_text(d2, my, MY_BOX, PLAYER_HP_BAR, f_name, f_small, f_ball, S, mine=True)

        # 地名 / 天气 / 场地标签:右上角纵向排布(左上角被敌方信息框占用)
        chip_x = (LOGICAL_W - 5) * S
        chip_y = 4
        if location:
            _chip(big, d2, chip_x, chip_y * S, str(location)[:6],
                  (238, 238, 222), (60, 60, 50), f_small, S, align_right=True)
            chip_y += 17
        if weather and weather in WEATHER_STYLE:
            _chip(big, d2, chip_x, chip_y * S, WEATHER_STYLE[weather],
                  (250, 214, 96), (72, 48, 8), f_small, S, align_right=True)
            chip_y += 17
        if terrain and terrain in TERRAIN_STYLE:
            _chip(big, d2, chip_x, chip_y * S, TERRAIN_STYLE[terrain],
                  (186, 240, 178), (32, 72, 32), f_small, S, align_right=True)

        # 对话框文本(行数与框高已在上面按实际折行结果算好)
        tx = (mx0 + tx_pad) * S
        ty = (my0 + 4) * S
        for i, line in enumerate(shown_lines):
            d2.text((tx, ty + i * int(MSG_LINE_H * S)), line, font=f_msg, fill=MSG_TEXT,
                    stroke_width=max(1, S // 2), stroke_fill=MSG_SHADOW)

        # 「HP」「EXP」标签文字(放大后画才清晰),在标签框内居中
        for tag, label, fg in (
            (ENEMY_HP_TAG, "HP", HP_TAG_FG),
            (PLAYER_HP_TAG, "HP", HP_TAG_FG),
            (EXP_TAG, "EXP", EXP_TAG_FG),
        ):
            tw = f_ball.getlength(label)
            th = f_ball.size
            d2.text(
                (
                    (tag[0] + tag[2]) / 2 * S - tw / 2,
                    (tag[1] + tag[3]) / 2 * S - th * 0.62,
                ),
                label,
                font=f_ball,
                fill=fg,
            )

        _ = turn
        buf = BytesIO()
        big.save(buf, format="PNG")
        data = buf.getvalue()
        if out_path:
            with open(out_path, "wb") as f:
                f.write(data)
        return data
    except Exception as e:  # 渲染失败必须回退文本
        logger.debug("宝可梦世界: 对战画面渲染失败: %s", e)
        return b""


def _draw_scene(d, weather: str) -> None:
    """场地背景:天气配色 + 地平线 + 竞技场椭圆 + 站台 + 天气粒子。"""
    key = str(weather or "")
    sky, ground, arena, plat = WEATHER_PALETTE.get(
        key, (BG_TOP, BG_GROUND, ARENA_FILL, PLATFORM_FILL)
    )
    d.rectangle([0, 0, LOGICAL_W, 78], fill=sky)
    d.rectangle([0, 78, LOGICAL_W, LOGICAL_H], fill=ground)
    d.line([0, 78, LOGICAL_W, 78], fill=tuple(max(0, c - 26) for c in ground))
    d.ellipse([-26, 30, LOGICAL_W + 26, 132], fill=arena,
              outline=tuple(max(0, c - 34) for c in arena))
    for box in (FOE_PLATFORM, MY_PLATFORM):
        d.ellipse(box, fill=plat, outline=tuple(max(0, c - 40) for c in plat))
    if key == "rain":
        for i in range(52):
            x = (i * 37) % LOGICAL_W
            y = (i * 23) % 118
            d.line([x, y, x - 3, y + 7], fill=(232, 240, 250))
    elif key == "sand":
        for i in range(70):
            x = (i * 53) % LOGICAL_W
            y = 24 + (i * 31) % 96
            d.point((x, y), fill=(206, 176, 116))
            d.point((x + 1, y + 1), fill=(216, 188, 130))
    elif key == "snow":
        for i in range(44):
            x = (i * 41) % LOGICAL_W
            y = (i * 29) % 112
            d.ellipse([x, y, x + 1, y + 1], fill=(250, 252, 255))


def _alive(mon: dict) -> bool:
    try:
        return int(mon.get("cur_hp", 1) or 0) > 0
    except (TypeError, ValueError):
        return True


def _ratio(mon: dict) -> float:
    try:
        cur = float(mon.get("cur_hp", 1) or 0)
        mx = float(mon.get("max_hp", 1) or 1)
    except (TypeError, ValueError):
        return 1.0
    return 0.0 if mx <= 0 else max(0.0, min(1.0, cur / mx))


def _paste_small(small, mon: dict, ground, *, factor: float, bounds, back: bool,
                 dim: bool) -> None:
    """按"脚底对齐"把精灵贴到逻辑画布:水平居中于站台,底边压在落地线上。"""
    from PIL import ImageEnhance, ImageOps

    cx, base_y = ground
    species = str(mon.get("species") or "")
    path = sprite_for(species, back=back)
    img = _load_sprite(path, factor, bounds)
    if img is None:
        img = _silhouette((bounds[0], min(bounds[1], 48)))
    elif back and path and os.path.basename(os.path.dirname(path)) != "sprites_back":
        # 退回正面图时镜像,近似"从背后看"的观感
        img = ImageOps.mirror(img)
    if dim:
        img = ImageEnhance.Brightness(img).enhance(0.45)
    px = cx - img.width // 2
    py = base_y - img.height
    small.paste(img, (px, py), img)


def _box_text(d2, mon, box, hp_bar, f_name, f_small, f_ball, S, *, mine: bool) -> None:
    """信息框里的文字(放大层绘制)。"""
    x0, y0, x1, _y1 = box
    name = str(mon.get("name") or mon.get("species") or "?")
    lv = mon.get("level")
    px = (x0 + 5) * S
    py = (y0 + 4) * S
    d2.text((px, py), name, font=f_name, fill=TEXT)
    w = int(f_name.getlength(name))
    gender = str(mon.get("gender") or "")
    if gender in ("M", "F"):
        d2.text((px + w + 2 * S, py), "♂" if gender == "M" else "♀", font=f_name,
                fill=MALE if gender == "M" else FEMALE)
        w += int(f_name.getlength("♂")) + 2 * S
    if lv not in (None, ""):
        lvtext = f"No.{int(lv)}"
        d2.text(((x1 - 5) * S - f_name.getlength(lvtext), py), lvtext, font=f_name, fill=TEXT)
    if mine:
        cur = mon.get("cur_hp", 0)
        mx = mon.get("max_hp", 0)
        hptext = f"{int(cur or 0)}/{int(mx or 0)}"
        d2.text(((x1 - 5) * S - f_small.getlength(hptext), (hp_bar[1] + 7) * S), hptext,
                font=f_small, fill=TEXT)
    else:
        _ = f_ball
