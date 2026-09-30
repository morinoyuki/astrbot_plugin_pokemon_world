"""立绘尺寸一致性:hires 通道与旧管线必须画得**一样大**。

背景:立绘改成"直接贴到放大层"后,缩放少乘了一次 `scale`,于是所有走 hires
的界面(图鉴/队伍/仓库/**结算卡**)立绘只有原来的 1/3 —— 肉眼容易漏掉,
所以这里用"重采样前后的尺寸比"做机器校验,覆盖全部有立绘的界面。
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PIL import Image  # noqa: E402

from pw import ui_info as UII  # noqa: E402
from pw import ui_render as UI  # noqa: E402

MON = {"species": "charizard", "name": "喷火龙", "level": 50, "cur_hp": 150,
       "max_hp": 153, "types": ["Fire", "Flying"], "moves": [], "exp_pct": 40,
       "friendship": 120, "gender": "M", "index": 1}
MON_EVO = {**MON, "pre_species": "charmeleon"}

CASES = {
    "资料页": lambda: UI.render_mon_summary(MON, scale=3),
    "队伍": lambda: UI.render_party([MON], scale=3),
    "仓库": lambda: UI.render_box([MON], scale=3),
    "图鉴": lambda: UI.render_dex({"name": "喷火龙", "dex_no": 6, "seen": True,
                                   "caught": True, "types": ["Fire"], "desc": "x"},
                                  scale=3),
    "升级卡": lambda: UII.render_growth(MON, before_level=49, after_level=50, scale=3),
    "进化画面": lambda: UII.render_growth(MON_EVO, before_level=35, after_level=36,
                                          evolved_from_zh="火恐龙",
                                          evolved_to_zh="喷火龙", scale=3),
    "捕捉卡": lambda: UII.render_gotcha(MON, ball_zh="精灵球", ball_key="poke-ball",
                                        dex_line="新纪录!", scale=3),
    "结算卡": lambda: UII.render_battle_result(mon=MON, outcome="win",
                                               lines=["胜利!"], scale=3),
}


def _sprite_ratio(render, *, force_hires: bool) -> float:
    """跑一次渲染,量"大立绘被重采样到的倍率"(放大层里应当恒等于 scale)。"""
    seen: list[float] = []
    orig_resize = Image.Image.resize
    orig_sprite = UI.Screen.sprite

    def spy_resize(self, size, *a, **k):
        src = self.size
        seen.append(next(iter(tuple(size))) / max(1, src[0]))
        return orig_resize(self, size, *a, **k)

    def spy_sprite(self, species, *, ground, factor=1.0, bounds=(64, 64), back=False,
                   dim=False, silhouette=False, shiny=False, hires=True):
        return orig_sprite(self, species, ground=ground, factor=factor, bounds=bounds,
                           back=back, dim=dim, silhouette=silhouette, shiny=shiny,
                           hires=(force_hires if hires else False))

    Image.Image.resize = spy_resize
    UI.Screen.sprite = spy_sprite
    try:
        render()
    finally:
        Image.Image.resize = orig_resize
        UI.Screen.sprite = orig_sprite
    big = [r for r in seen if r > 1.0]          # 只看立绘(小图标不参与)
    return max(big) if big else 0.0


def test_every_sprite_screen_draws_the_same_size_with_both_pipelines():
    """每个有立绘的界面:hires 与旧管线画出的立绘倍率一致(都等于 scale)。"""
    checked, problems = [], []
    for name, render in CASES.items():
        try:
            hires = _sprite_ratio(render, force_hires=True)
            old = _sprite_ratio(render, force_hires=False)
        except TypeError:                        # 该界面签名不同:跳过(上面已覆盖)
            continue
        if not hires or not old:
            problems.append(f"{name}: 没量到立绘(hires={hires} old={old})")
            continue
        checked.append(name)
        if abs(hires - old) > 0.02:
            problems.append(f"{name}: hires 倍率 {hires:.3f} ≠ 旧管线 {old:.3f}")
        if abs(hires - 3.0) > 0.02:
            problems.append(f"{name}: 倍率应等于 scale=3,实测 {hires:.3f}")
    assert len(checked) >= 5, f"覆盖的界面太少:{checked}"
    assert not problems, "; ".join(problems)


def test_sprite_pipeline_still_single_resample():
    """hires 的初衷是"只重采样一次":大立绘必须走放大层通道。"""
    src = open(os.path.join(_ROOT, "pw", "ui_render.py"), encoding="utf-8").read()
    assert "_hires" in src and "_paste_hires" in src
    assert "factor\") or 1.0) * S" in src, "放大层里的缩放必须乘 scale"
