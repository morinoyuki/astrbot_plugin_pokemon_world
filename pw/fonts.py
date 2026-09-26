"""字体探测与字形回退(逻辑照搬 astrbot_plugin_life_sim 的 im_render/style.py)。

光有内置的 OPPOSans 不够:换台机器、字体文件损坏、或者文本里出现
OPPOSans 没有的字形(emoji / 冷僻符号)都会画成豆腐块。这里按 life_sim
的做法做成一套完整的回退链:

1. **主字体候选**:环境变量 > 插件根目录 ``fonts/`` > ``pw/static/fonts/`` >
   pillowmd 内置雅黑 > 系统常见 CJK 字体。逐个用 ``ImageFont.truetype(p, 12)``
   验证,损坏/不可读的跳过。
2. **加载兜底**:主字体整个加载失败时退回 Pillow 的默认位图字体(难看但不会崩)。
3. **逐字符回退**:主字体缺某字形时,在备用符号/emoji 字体列表
   (系统彩色 emoji 字体 > 系统字体目录扫描 > 插件自带 Symbola)里找第一个
   覆盖它的字体。
4. **位图字体**:NotoColorEmoji 这类 CBDT/CBLC 字体只有固定尺寸字形
   (109px),非内置尺寸加载会报 ``invalid pixel size`` —— 按内置尺寸加载,
   绘制时再等比缩放贴回画布(``draw_text`` 里处理)。
5. **丢弃兜底**:所有字体都没有的字符直接丢掉,绝不画豆腐块;
   但如果字体覆盖信息读不到(fontTools 缺失)就保守照常绘制,避免误删整段文字。
"""

from __future__ import annotations

import contextlib
import logging
import os
from functools import lru_cache

logger = logging.getLogger("pw.fonts")

__all__ = [
    "available",
    "bbox",
    "char_renderable",
    "draw_text",
    "iter_runs",
    "load_font",
    "main_font_supports",
    "measure",
    "resolve_font_path",
    "sanitize",
    "search_fonts",
]

try:  # Pillow 缺失时整个图片功能降级(available() 返回 False)
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - 没装 Pillow 的环境
    Image = ImageDraw = ImageFont = None  # type: ignore[assignment]

HERE = os.path.dirname(os.path.abspath(__file__))
_FONT_DIRS = (
    os.path.join(HERE, "static", "fonts"),   # 插件自带
    os.path.join(os.path.dirname(HERE), "fonts"),  # 插件根目录 fonts/(用户放置)
    os.path.join(os.getcwd(), "fonts"),
)

# 常见 CJK 字体(找不到自带字体时的系统兜底)
_OS_FONT_CANDIDATES: tuple[str, ...] = (
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/wenquanyi/wqy-zenhei.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # Windows(WSL 下也能按 /mnt/c 访问)
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/mnt/c/Windows/Fonts/msyh.ttc",
    "/mnt/c/Windows/Fonts/simhei.ttf",
    "/mnt/c/Windows/Fonts/simsun.ttc",
)

# 粗体候选(找不到就用常规字重顶替)
_OS_BOLD_CANDIDATES: tuple[str, ...] = (
    "C:/Windows/Fonts/msyhbd.ttc",
    "/mnt/c/Windows/Fonts/msyhbd.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
    "C:/Windows/Fonts/simhei.ttf",
    "/mnt/c/Windows/Fonts/simhei.ttf",
)


def _add_file(found: list[str], path: str) -> None:
    if path and os.path.isfile(path) and path not in found:
        found.append(path)


def _scan_font_dir(d: str, found: list[str]) -> None:
    """扫一个字体目录,把 ttf/ttc/otf 加进候选(目录不存在就跳过)。"""
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return
    for fn in names:
        if fn.lower().endswith((".ttf", ".ttc", ".otf")):
            _add_file(found, os.path.join(d, fn))


def _discover_font_candidates() -> tuple[str, ...]:
    """构建主字体候选:自带字体 > 环境变量 > 根目录 fonts/ > pillowmd > 系统字体。

    注意自带字体放在最前:它是随插件分发的,一定存在且覆盖中文,
    比依赖宿主机装了什么字体可靠得多。
    """
    found: list[str] = []
    for d in _FONT_DIRS:
        _scan_font_dir(d, found)

    env = (
        os.environ.get("POKEMON_WORLD_FONT", "").strip()
        or os.environ.get("LIFE_SIM_FONT", "").strip()
    )
    if env:
        _add_file(found, env)

    # pillowmd 内置雅黑(装了的话字体通常比系统字体全)
    try:
        import pillowmd  # type: ignore

        _add_file(
            found,
            os.path.join(
                os.path.dirname(os.path.abspath(pillowmd.__file__)),
                "data",
                "fonts",
                "yahei.ttf",
            ),
        )
    except Exception:  # 没装 pillowmd 很正常
        pass

    for p in _OS_FONT_CANDIDATES:
        _add_file(found, p)
    return tuple(found)


FALLBACK_FONT_CANDIDATES: tuple[str, ...] = _discover_font_candidates()
BOLD_FONT_CANDIDATES: tuple[str, ...] = (
    *_OS_BOLD_CANDIDATES,
    *FALLBACK_FONT_CANDIDATES,
)

_font_path: str | None = None
_bold_font_path: str | None = None
_font_searched = False


def _loadable(path: str) -> bool:
    """存在、可读、且 ImageFont 真的能打开(损坏的字体文件会被跳过)。"""
    if not (path and os.path.isfile(path) and os.access(path, os.R_OK)):
        return False
    try:
        ImageFont.truetype(path, 12)
    except (OSError, ValueError):
        return False
    return True


def _find_font(candidates) -> str | None:
    """挑第一个真的能加载的字体。"""
    for p in candidates:
        if _loadable(p):
            return p
    return None


def _env_font_path() -> str:
    """环境变量指定的字体(最高优先级;每次探测都重读,清了缓存即可生效)。"""
    return (
        os.environ.get("POKEMON_WORLD_FONT", "").strip()
        or os.environ.get("LIFE_SIM_FONT", "").strip()
    )


def search_fonts() -> None:
    """探测一次并缓存结果(幂等)。"""
    global _font_searched, _font_path, _bold_font_path
    if _font_searched:
        return
    _font_searched = True
    if ImageFont is None:
        return
    env = [p for p in (_env_font_path(),) if p]
    _font_path = _find_font([*env, *FALLBACK_FONT_CANDIDATES])
    _bold_font_path = _find_font([*env, *BOLD_FONT_CANDIDATES])
    if _font_path:
        logger.debug("宝可梦世界: 主字体 %s", _font_path)
    else:
        logger.warning("宝可梦世界: 没找到任何可用字体,界面文字将使用 Pillow 默认字体")


def resolve_font_path() -> str | None:
    search_fonts()
    return _font_path


@lru_cache(maxsize=64)
def _cached_truetype(path: str, size: int):
    return ImageFont.truetype(path, int(size))


@lru_cache(maxsize=16)
def _cached_default(size: int):
    try:
        return ImageFont.load_default(size=int(size))
    except TypeError:  # 老版本 Pillow 没有 size 参数
        return ImageFont.load_default()


def load_font(size: int, bold: bool = False):
    """加载主字体;全挂时退回 Pillow 默认字体(保证不抛异常)。"""
    search_fonts()
    size = max(4, int(size))
    if ImageFont is None:
        return None
    path = (_bold_font_path if (bold and _bold_font_path) else None) or _font_path
    if path:
        try:
            return _cached_truetype(path, size)
        except OSError:
            pass  # 主字体加载失败 → 默认字体
    try:
        return _cached_default(size)
    except Exception:
        return None


def clear_cache() -> None:
    """清空字体缓存(改了字体文件/环境变量后用)。"""
    global _emoji_fonts_cache, _font_searched, _font_path, _bold_font_path
    _cached_truetype.cache_clear()
    _cached_default.cache_clear()
    _cmap_cache.clear()
    _emoji_fonts_cache = None
    _font_searched = False
    _font_path = _bold_font_path = None


# ═════════════════════════════════════════════════════════════════
# 字形覆盖判定(cmap)
# ═════════════════════════════════════════════════════════════════
_cmap_cache: dict[str, frozenset[int] | None] = {}


def _charset(path: str) -> frozenset[int] | None:
    """读字体的 cmap 码点集合;读不到返回 None(调用方保守处理)。"""
    if path not in _cmap_cache:
        s: frozenset[int] | None
        try:
            from fontTools.ttLib import TTFont

            with TTFont(path, fontNumber=0) as f:
                cmap = f.getBestCmap()
            s = frozenset(cmap.keys()) if cmap else frozenset()
        except Exception:
            s = None      # fontTools 缺失或字体读不了
        _cmap_cache[path] = s
    return _cmap_cache[path]


def _supports(path: str | None, char: str) -> bool:
    """该字体是否含此字形。覆盖信息不可得时返回 True(保守)。"""
    if not path or not char:
        return True
    s = _charset(path)
    if s is None:
        return True
    return ord(char) in s


def main_font_supports(char: str) -> bool:
    """**主字体**是否含该字形。

    必须走这个函数而不是导入 `_font_path` 的值 —— 按值导入会拿到探测前的
    None,判定恒为 True,整套回退就失效了。
    """
    search_fonts()
    return _supports(_font_path, char)


# ═════════════════════════════════════════════════════════════════
# emoji / 符号字体回退
# ═════════════════════════════════════════════════════════════════
_EMOJI_FALLBACK_NAMES = (
    "NotoColorEmoji-Regular.ttf",
    "NotoColorEmoji.ttf",
    "Segoe UI Emoji.ttf",
    "SegoeUIEmoji.ttf",
    "OpenMoji-black.ttf",
    "OpenMoji-black.otf",
    "TwemojiSans.ttf",
    "NotoEmoji-Regular.ttf",
    "Apple Color Emoji.ttc",
)

_OS_EMOJI_FONT_CAND = (
    # Windows(含 WSL 挂载)
    "C:/Windows/Fonts/Segoe UI Emoji.ttf",
    "C:/Windows/Fonts/SegoeUIEmoji.ttf",
    "/mnt/c/Windows/Fonts/Segoe UI Emoji.ttf",
    "/mnt/c/Windows/Fonts/seguiemj.ttf",
    # macOS
    "/System/Library/Fonts/Apple Color Emoji.ttc",
    "/Library/Fonts/Apple Color Emoji.ttc",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoColorEmoji-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji-Regular.ttf",
    "/usr/share/fonts/noto/NotoColorEmoji-Regular.ttf",
    "/usr/share/fonts/noto-cjk/NotoColorEmoji-Regular.ttf",
    "/usr/share/fonts/twemoji/TwitterColorEmoji-SVGinOT.ttf",
    "/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf",
)

_OS_EMOJI_FONT_DIRS = (
    "C:/Windows/Fonts",
    "/mnt/c/Windows/Fonts",
    "/System/Library/Fonts",
    "/Library/Fonts",
    "/usr/share/fonts",
    "/usr/share/fonts/truetype/noto",
    "/usr/share/fonts/opentype/noto",
)

_emoji_fonts_cache: list[str] | None = None


def _discover_emoji_fonts() -> list[str]:
    """备用符号/emoji 字体列表(按优先级)。

    顺序:系统彩色 emoji 字体 > 扫描系统字体目录 > 插件自带 Symbola。
    自带的 Symbola 只覆盖 2014 年前的 emoji,所以系统若有彩色字体就先用。
    """
    global _emoji_fonts_cache
    if _emoji_fonts_cache is not None:
        return list(_emoji_fonts_cache)
    found: list[str] = []
    for p in _OS_EMOJI_FONT_CAND:
        _add_file(found, p)
    for d in _OS_EMOJI_FONT_DIRS:
        for fn in _EMOJI_FALLBACK_NAMES:
            _add_file(found, os.path.join(d, fn))
    for d in _FONT_DIRS:
        for fn in (*_EMOJI_FALLBACK_NAMES, "Symbola_hint.ttf", "Symbola.ttf"):
            _add_file(found, os.path.join(d, fn))
    _emoji_fonts_cache = found
    return list(found)


# 位图 emoji 字体(CBDT/CBLC)只有固定尺寸字形,非内置尺寸加载会失败
_EMOJI_BITMAP_SIZE = 109
_BITMAP_FONT_PATHS: set[str] = set()


def _load_font_fallback(path: str, size: int):
    """加载备用字体:位图字体在目标尺寸失败时按内置尺寸加载(绘制时缩放)。"""
    try:
        return _cached_truetype(path, size)
    except OSError:
        font = _cached_truetype(path, _EMOJI_BITMAP_SIZE)
        _BITMAP_FONT_PATHS.add(path)
        return font


def is_bitmap_font(font) -> bool:
    """是否固定尺寸位图字体(需要缩放贴图,且必须 embedded_color 才彩色)。"""
    try:
        return bool(font.path) and font.path in _BITMAP_FONT_PATHS
    except Exception:
        return False


def emoji_font_for(char: str, size: int):
    """主字体缺该字形时,在备用列表里选第一个覆盖它的;都不覆盖返回 None。"""
    search_fonts()
    if not char or ImageFont is None:
        return None
    if _supports(_font_path, char):
        return None  # 主字体就有
    for alt in _discover_emoji_fonts():
        if not _supports(alt, char):
            continue
        try:
            return _load_font_fallback(alt, size)
        except Exception:
            continue          # 字体读不了 → 试下一个
    return None


def font_for_char(char: str, size: int, *, bold: bool = False):
    """按字符选字体:主字体有就用主字体,否则用能覆盖它的备用字体。"""
    return emoji_font_for(char, size) or load_font(size, bold)


def char_renderable(char: str) -> bool:
    """是否**没有任何**可用字体覆盖该字符(此时必须丢弃,否则是豆腐块)。

    覆盖信息读不到时返回 True(保守:照常画,避免误删整段文字)。
    """
    search_fonts()
    if _supports(_font_path, char):
        return True
    for alt in _discover_emoji_fonts():
        s = _charset(alt)
        if s is None:
            return True
        if ord(char) in s:
            return True
    return False


def _is_emoji_control(ch: str) -> bool:
    """变体选择符/零宽连接符等组合控制码点:本身没有字形,必须跳过。

    逐字符渲染无法合成 ZWJ 序列,单独绘制会被当成缺字打成方块。
    """
    if not ch:
        return True
    cp = ord(ch)
    return (
        cp in (0x200B, 0x200C, 0x200D, 0x200E, 0xFEFF)
        or 0xFE00 <= cp <= 0xFE0F
        or 0xE0100 <= cp <= 0xE01EF
    )


# ═════════════════════════════════════════════════════════════════
# 分段 / 测量 / 绘制
# ═════════════════════════════════════════════════════════════════
def iter_runs(text: str, size: int, *, bold: bool = False) -> list[tuple[object, str]]:
    """把文本按字体切段:``[(font, run), ...]``,font=None 表示主字体。

    主字体覆盖的连续字符合并成一段(中文正文几乎全落在这里,整段测量/绘制
    只调一次 PIL),emoji 之类要回退的字符单独成段(彩色位图字体不适合整串画)。
    所有字体都不覆盖的字符直接丢弃。
    """
    runs: list[tuple[object, str]] = []
    batch: list[str] = []
    for ch in str(text):
        if ch in "\n\t":
            batch.append(" ")
            continue
        if _is_emoji_control(ch):
            continue
        ef = emoji_font_for(ch, size)
        if ef is None:
            if char_renderable(ch):
                batch.append(ch)
            continue
        if batch:
            runs.append((None, "".join(batch)))
            batch = []
        runs.append((ef, ch))
    if batch:
        runs.append((None, "".join(batch)))
    return runs


def sanitize(text) -> str:
    """去掉**任何字体都没有**的字符,避免豆腐块;换行与可渲染字符保留。"""
    out: list[str] = []
    for ch in str(text):
        if ch in "\n\t" or ord(ch) < 128:
            out.append(ch)
        elif ch == "\u3000":
            out.append(" ")
        elif char_renderable(ch):
            out.append(ch)
    return "".join(out)


def _resolve_run_font(font, size: int, bold: bool):
    return font if font is not None else load_font(size, bold=bold)


def _scale_of(font, size: int) -> float:
    """位图字体的缩放系数(内置尺寸 → 目标字号);非位图字体为 1。"""
    try:
        return float(size) / font.size if is_bitmap_font(font) else 1.0
    except Exception:
        return 1.0


def _scale_of(font, size: int) -> float:
    """位图字体的缩放系数(内置尺寸 → 目标字号);非位图字体为 1。"""
    try:
        return float(size) / font.size if is_bitmap_font(font) else 1.0
    except Exception:
        return 1.0


def measure(text: str, size: int, *, bold: bool = False) -> float:
    """测量宽度(主字体段整体测,回退字符逐个测;位图字体按比例换算)。"""
    total = 0.0
    for font, run in iter_runs(text, size, bold=bold):
        f = _resolve_run_font(font, size, bold)
        if f is None:
            continue
        try:
            total += f.getlength(run) * _scale_of(f, size)
        except Exception:
            continue
    return total


def bbox(text: str, size: int, *, bold: bool = False) -> tuple[float, float, float, float]:
    """文本墨迹范围 (x0,y0,x1,y1),相对绘制原点。

    混合字体时取所有段的并集;拿不到 bbox 时退化成 (0,0,测量宽度,字号)。
    """
    x0 = y0 = 0.0
    x1 = y1 = 0.0
    first = True
    cursor = 0.0
    for font, run in iter_runs(text, size, bold=bold):
        f = _resolve_run_font(font, size, bold)
        if f is None:
            continue
        ratio = _scale_of(f, size)
        try:
            bx0, by0, bx1, by1 = f.getbbox(run)
        except (ValueError, OSError):
            continue
        bx0, bx1 = bx0 * ratio, bx1 * ratio
        by0, by1 = by0 * ratio, by1 * ratio
        if first:
            x0, y0, x1, y1 = cursor + bx0, by0, cursor + bx1, by1
            first = False
        else:
            x0 = min(x0, cursor + bx0)
            y0 = min(y0, by0)
            x1 = max(x1, cursor + bx1)
            y1 = max(y1, by1)
        cursor += f.getlength(run) * ratio
    if first:  # 什么都没测到
        return (0.0, 0.0, 0.0, float(size))
    return (x0, y0, x1, y1)


def _draw_bitmap_emoji(canvas, xy, ch: str, font, size: int) -> float:
    """画固定尺寸位图 emoji:内置尺寸渲染 → 等比缩放贴回(返回推进宽度)。

    NotoColorEmoji 的字形位图会超出 em box(实测右下越界约 0.5em),
    临时画布必须留足 padding,否则字形边缘被裁。
    """
    x, y = xy
    ratio = size / font.size
    pad = font.size // 2
    tmp = Image.new("RGBA", (font.size + pad * 2, font.size + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((pad, pad), ch, font=font, embedded_color=True)
    box = tmp.getbbox()
    if box:
        tmp = tmp.crop(box)
        w = max(1, int(tmp.width * ratio))
        h = max(1, int(tmp.height * ratio))
        tmp = tmp.resize((w, h), Image.LANCZOS)
        # 底边对齐行底,视觉上与文字基线一致
        canvas.alpha_composite(tmp, (int(x), int(y + max(0, size - h))))
    return font.getlength(ch) * ratio


def draw_text(canvas, xy, text: str, size: int, fill=(0, 0, 0), *,
              bold: bool = False, stroke_width: int = 0,
              stroke_fill=None) -> float:
    """绘制文本(含 emoji/符号回退与位图 emoji 缩放),返回末端 x。"""
    if canvas is None or ImageDraw is None:
        return float(xy[0])
    x, y = float(xy[0]), float(xy[1])
    draw = ImageDraw.Draw(canvas)
    for font, run in iter_runs(text, size, bold=bold):
        f = _resolve_run_font(font, size, bold)
        if f is None:
            continue
        if is_bitmap_font(f):
            for ch in run:
                x += _draw_bitmap_emoji(canvas, (x, y), ch, f, int(size))
            continue
        kw = {}
        if stroke_width > 0:
            kw["stroke_width"] = int(stroke_width)
            kw["stroke_fill"] = stroke_fill or fill
        with contextlib.suppress(ValueError, OSError):
            draw.text((x, y), run, font=f, fill=fill, **kw)
        x += f.getlength(run)
    return x


def available() -> bool:
    """Pillow 与至少一个字体可用。"""
    if Image is None or ImageFont is None:
        return False
    search_fonts()
    return _font_path is not None or _cached_default(20) is not None
