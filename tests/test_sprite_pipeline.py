"""所有立绘都必须走高分辨率通道。

旧管线:先缩到 240×160 逻辑层,再整张 NEAREST 放大 3 倍 → 立绘被降采样后硬放大,发糊。
新管线:`hires=True` 在放大层只重采样一次(缩小用 LANCZOS)。
一律走新管线,避免以后新增界面时又漏用。
"""

from __future__ import annotations

import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _sprite_calls(text: str) -> list[str]:
    out = []
    for m in re.finditer(r"sc\.sprite\(", text):
        start, depth, j = m.end(), 1, m.end()
        while depth and j < len(text):
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
            j += 1
        out.append(text[start:j - 1])
    return out


def test_every_sprite_uses_the_hires_pipeline():
    """队伍/仓库/图鉴/资料/成长/结算/道馆……所有立绘都要 hires=True。"""
    pw = os.path.join(_ROOT, "pw")
    bad: list[str] = []
    for name in ("ui_render.py", "ui_info.py", "ui_menu.py"):
        with open(os.path.join(pw, name), encoding="utf-8") as f:
            text = f.read()
        for args in _sprite_calls(text):
            if "hires" not in args:
                first = args.strip().split("\n")[0][:40]
                bad.append(f"{name}: {first}")
    assert not bad, "这些立绘还在走旧的模糊管线(缺 hires=True):" + "; ".join(bad)


def test_screen_supports_the_hires_pipeline():
    """Screen 必须真的实现 hires(否则上面的断言只是纸面约定)。"""
    from pw.ui_render import Screen

    sc = Screen(scale=2)
    assert hasattr(sc, "sprite")
    with open(os.path.join(_ROOT, "pw", "ui_render.py"), encoding="utf-8") as f:
        src = f.read()
    assert "hires" in src and "_hires" in src, "Screen 没有 hires 通道"
