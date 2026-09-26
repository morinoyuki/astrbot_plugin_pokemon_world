"""道具图标测试:覆盖全部 BAG_ITEMS,并校验越界与可区分性。

图标画在 240×160 的逻辑层,坐标被别的界面用来排布,所以这里用真实
`Screen.item_icon` 渲染后逐像素检查方框外没有墨迹。
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageChops

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pw import ui_render as UI  # noqa: E402
from pw.items import BAG_ITEMS  # noqa: E402
from pw.world import SHOP_STOCK  # noqa: E402

SIZE = 9
X, Y = 11, 7


def _render(key_or_kind: str, *, x: int = X, y: int = Y):
    sc = UI.Screen(scale=1)
    sc.item_icon(key_or_kind, x, y, SIZE)
    return sc.small


def _crop(im: Image.Image) -> Image.Image:
    return im.crop((X, Y, X + SIZE, Y + SIZE))


def _sig(key_or_kind: str) -> bytes:
    return _crop(_render(key_or_kind)).tobytes()


def test_item_icon_accepts_every_bag_key_without_exception():
    """(a) 每个 BAG_ITEMS key 都能画出来,不抛异常。"""
    for key in BAG_ITEMS:
        _render(key)


def test_item_icon_draws_inside_box():
    """(b) 图标严格落在 [x, x+size-1] × [y, y+size-1] 内。"""
    for key in BAG_ITEMS:
        im = _render(key)
        allowed = _crop(im)
        # 把允许区域涂成背景色,整张图应只剩背景 —— 说明方框外没有墨迹
        outside = im.copy()
        outside.paste(Image.new("RGB", allowed.size, UI.BG), (X, Y))
        diff = ImageChops.difference(outside, Image.new("RGB", im.size, UI.BG))
        assert diff.getbbox() is None, f"{key} 的图标越出了 {SIZE}×{SIZE} 方框"


def test_item_icons_are_distinguishable():
    """(c) 图标不能千篇一律:整体重复率低,同类内部也要有区别。"""
    sigs = {key: _sig(key) for key in BAG_ITEMS}
    assert len(set(sigs.values())) >= 85, "大量道具图标完全重复"

    balls = [k for k, v in BAG_ITEMS.items() if v.get("kind") == "ball"]
    assert len({sigs[k] for k in balls}) == len(balls), "精灵球图标应当各不相同"

    pairs = [
        ("poke-ball", "great-ball"), ("great-ball", "ultra-ball"),
        ("master-ball", "heal-ball"), ("net-ball", "quick-ball"),
        ("potion", "max-potion"), ("antidote", "burn-heal"),
        ("ether", "max-elixir"), ("revive", "max-revive"),
        ("fire-stone", "water-stone"), ("cheri-berry", "sitrus-berry"),
    ]
    for a, b in pairs:
        assert sigs[a] != sigs[b], f"{a} 与 {b} 的图标不该相同"


def test_kind_fallback_and_unknown_kind():
    """旧界面只传大类时仍能画;未知道具 / 大类退化到默认且不抛异常。"""
    default = _sig("__unknown__")
    for kind in ("ball", "medicine", "status", "revive", "pp", "battle", "berry",
                 "stone", "evo", "rare"):
        assert _sig(kind) != default, f"大类 {kind} 退化成了默认图标"
    # 空串 / 未知字符串 / None 都走默认分支,结果一致且不抛异常
    for junk in ("", "__nope__", "这不是道具", None, "great-ball-typo"):
        assert _sig(junk) == default, f"{junk!r} 没有安全退化到默认图标"


def test_render_bag_uses_item_key_for_icon():
    """render_bag 要按 key 取图标,不能只传大类。

    两条目除 key 外完全相同(含中文名),图像若仍相同就说明还在按 kind 画图标。
    """
    base = {"zh": "测试球", "count": 1, "desc": "测试", "kind": "ball"}
    a = UI.render_bag([{**base, "key": "great-ball"}], active_pocket="balls", scale=2)
    b = UI.render_bag([{**base, "key": "ultra-ball"}], active_pocket="balls", scale=2)
    assert a.startswith(b"\x89PNG") and b.startswith(b"\x89PNG")
    assert a != b, "render_bag 仍按大类画图标(不同 key 的图像完全相同)"


def test_new_items_exist_and_are_obtainable():
    """本次补的道具必须能在商店买到,不能是死数据。"""
    for key in ("leppa-berry", "berry-juice", "sweet-heart"):
        assert key in BAG_ITEMS, f"缺少新道具 {key}"
        assert BAG_ITEMS[key].get("zh"), key
        assert key in SHOP_STOCK, f"新道具 {key} 不可获得"


def test_bag_items_have_complete_fields():
    for key, entry in BAG_ITEMS.items():
        assert entry.get("zh"), key
        assert entry.get("desc"), key
        assert entry.get("kind"), key
        assert isinstance(entry.get("effect"), dict), key
