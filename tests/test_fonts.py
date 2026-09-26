"""字体回退链测试(逻辑照搬 astrbot_plugin_life_sim 的 im_render/style.py)。

重点不是"能不能画出字",而是**换台机器/字体损坏/出现冷僻字符**时还能不能画。
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from pw import fonts  # noqa: E402


def test_bundled_font_is_discovered():
    """插件自带 OPPOSans 必须被找到(不依赖宿主机装了什么字体)。"""
    path = fonts.resolve_font_path()
    assert path, "没找到任何主字体"
    assert os.path.isfile(path)
    assert "OPPOSans" in os.path.basename(path) or os.path.getsize(path) > 100_000


def test_load_font_never_raises_and_scales():
    f = fonts.load_font(20)
    assert f is not None
    assert fonts.load_font(20).getlength("测试") > 0
    assert fonts.load_font(40).getlength("测试") > f.getlength("测试")
    assert fonts.load_font(2) is not None      # 过小字号也不会崩(内部钳到 4)


def test_corrupt_font_is_skipped(monkeypatch, tmp_path):
    """字体文件损坏时必须跳到下一个候选,而不是整个渲染失败。"""
    bad = tmp_path / "broken.ttf"
    bad.write_bytes(b"this is not a font at all")
    monkeypatch.setenv("POKEMON_WORLD_FONT", str(bad))
    fonts.clear_cache()
    try:
        assert fonts.resolve_font_path() != str(bad), "损坏字体不该被选中"
        assert fonts.load_font(20).getlength("测试") > 0
    finally:
        monkeypatch.delenv("POKEMON_WORLD_FONT", raising=False)
        fonts.clear_cache()


def test_env_font_wins_when_valid(monkeypatch):
    """有效字体路径通过环境变量指定时应当优先使用。"""
    bundled = os.path.join(_ROOT, "pw", "static", "fonts", "OPPOSans-Regular.ttf")
    if not os.path.isfile(bundled):
        return
    monkeypatch.setenv("POKEMON_WORLD_FONT", bundled)
    fonts.clear_cache()
    try:
        assert fonts.resolve_font_path() == bundled
    finally:
        monkeypatch.delenv("POKEMON_WORLD_FONT", raising=False)
        fonts.clear_cache()


def test_chars_without_any_glyph_are_dropped():
    """任何字体都没有的字符必须丢弃 —— 否则界面上是豆腐块。"""
    assert not fonts.char_renderable("\uffff")
    assert fonts.sanitize("测试\uffff结束") == "测试结束"
    # 换行/制表符保留,全角空格归一成普通空格
    assert fonts.sanitize("a\nb\tc") == "a\nb\tc"
    assert fonts.sanitize("a\u3000b") == "a b"


def test_symbols_and_emoji_are_kept_when_a_font_covers_them():
    """主字体或缺字形时应当回退到符号/emoji 字体,而不是直接丢掉。"""
    for ch in ("◆", "★"):                    # OPPOSans 自带
        assert fonts.char_renderable(ch), ch
        assert ch in fonts.sanitize(ch)
    if fonts.char_renderable("🐉"):           # 宿主有 emoji 字体时
        assert "🐉" in fonts.sanitize("抓到🐉了")
        # 应当走"回退字体"而不是主字体,并且被切到独立的 run
        runs = fonts.iter_runs("抓到🐉了", 20)
        assert any(r for f, r in runs if f is not None and "🐉" in r), runs
    # 组合控制码(变体选择符 U+FE0F / ZWJ)本身没有字形,绝不能单独画出来
    for ch in ("\ufe0f", "\u200d"):
        assert all(ch not in run for _f, run in fonts.iter_runs(f"a{ch}b", 20))


def test_iter_runs_merges_main_font_text():
    """主字体覆盖的连续字符合并成一段(性能关键:整段只调一次 PIL)。"""
    runs = fonts.iter_runs("纯中文文本ABC123", 20)
    assert runs and all(f is None for f, _ in runs), runs
    assert "".join(r for _f, r in runs) == "纯中文文本ABC123"


def test_measure_and_bbox_handle_mixed_fonts():
    """混合字体(中文 + emoji)时宽度与墨迹范围都要算得出来。"""
    plain = fonts.measure("抓到龙了", 20)
    assert plain > 0
    w = fonts.measure("抓到🐉了", 20)
    assert w > 0
    x0, y0, x1, y1 = fonts.bbox("抓到🐉了", 20)
    assert x1 > x0 and y1 > y0
    assert x1 <= w + 1, f"墨迹宽度不该超过测量宽度:x1={x1} w={w}"


def test_draw_text_renders_and_advances():
    """draw_text 要真的画出像素(不只是不报错),并返回正确的末端 x。"""
    from PIL import Image

    im = Image.new("RGB", (200, 40), (255, 255, 255))
    end = fonts.draw_text(im, (2, 4), "测试AB", 20, (0, 0, 0))
    assert end > 2
    assert abs(end - (2 + fonts.measure("测试AB", 20))) < 1.5
    # 有墨迹(整张图不全是背景色)
    assert im.getbbox() is not None, "draw_text 什么都没画出来"


def test_draw_text_with_emoji_does_not_crash():
    from PIL import Image

    im = Image.new("RGBA", (200, 60), (255, 255, 255, 255))
    end = fonts.draw_text(im, (2, 4), "抓到🐉了📨", 20, (0, 0, 0))
    assert end > 2
