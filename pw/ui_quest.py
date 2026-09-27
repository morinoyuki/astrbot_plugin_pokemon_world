"""委托板界面(仿 GBA 任务列表)。

3 张委托卡:委托人 + 标题、说明、进度条、报酬(金钱 + 道具图标)。
渲染失败一律返回 b"" 由调用方回退文本。
"""

from __future__ import annotations

import logging

from . import ui_render as U
from .ui_render import (
    BOX_EDGE,
    BOX_HI,
    SCALE_DEFAULT,
    TEXT,
    TEXT_DIM,
    Screen,
)

logger = logging.getLogger("pw.ui_quest")

TITLE_COLOR = (32, 72, 120)
DONE_COLOR = (56, 128, 64)
BAR_BG = (208, 204, 168)


def _fit(sc: Screen, s: str, w: float, size: float) -> str:
    s = str(s or "")
    if sc.tw(s, size) <= w:
        return s
    while s and sc.tw(s + "…", size) > w:
        s = s[:-1]
    return s + "…"


def _progress_bar(sc: Screen, box, ratio: float, *, done: bool = False) -> None:
    x0, y0, x1, y1 = box
    sc.d.rectangle([x0, y0, x1, y1], fill=BAR_BG, outline=BOX_EDGE)
    span = x1 - x0 - 2
    filled = int(span * max(0.0, min(1.0, ratio)))
    if filled > 0:
        sc.d.rectangle([x0 + 1, y0 + 1, x0 + 1 + filled, y1 - 1],
                       fill=DONE_COLOR if done else (72, 152, 216))


def _reward_icons(sc: Screen, items: dict, x: float, y: float, size: float = 8) -> float:
    """在指定位置画报酬道具图标,返回用掉的宽度。"""
    for key in list(items)[:3]:
        sc.item_icon(str(key), x, y, size)
        x += size + 1.5
    return x


def render_quests(
    quests: list[dict],
    *,
    progress: list[tuple[int, int]] | None = None,
    day: int = 1,
    region_zh: str = "",
    done_count: int = 0,
    max_active: int = 4,
    scale: int = SCALE_DEFAULT,
) -> bytes:
    """画委托板。

    quests   : [{"giver","title","desc","objective","reward"}] 最多显示 3 条
    progress : 与 quests 对应的 (当前, 需要) 进度对
    """
    if not U.available():
        return b""
    try:
        sc = Screen(scale=scale)
        sc.title_bar("委托板", right=f"第{int(day)}天")
        if region_zh:
            sc.text(9, 18.2, region_zh, size=7.4, fill=TEXT_DIM)
        sc.text_right(231, 18.2, f"已完成 {int(done_count)} · 上限 {int(max_active)}",
                      size=7.4, fill=TEXT_DIM)
        sc.d.line([5, 27, 235, 27], fill=BOX_HI)

        rows = list(quests or [])[:3]
        if not rows:
            sc.window((6, 34, 234, 96), radius=3)
            sc.text_center(120, 50, "今天还没有委托", size=10, fill=TEXT)
            sc.text_center(120, 66, "新的一天会刷新新的委托", size=8, fill=TEXT_DIM)
            sc.footer("◆ 每天凌晨 4 点刷新 · 完成委托可获得金钱与道具")
            return sc.finish()

        # 3 行 ×(ch+1.5) 必须收在 content_bottom(140)以内:
        # 原本 ch=37 时第 3 行画到 y=145,压住了底部提示条。
        top = 31.0
        ch = 35.0
        for i, q in enumerate(rows):
            y0 = top + i * (ch + 1.5)
            y1 = y0 + ch
            obj = q.get("objective") or {}
            cur, need = (progress[i] if progress and i < len(progress)
                         else (int(q.get("progress") or 0), max(1, int(obj.get("count") or 1))))
            done = cur >= need
            sc.window((5, y0, 235, y1), radius=2, shadow=False)
            # 序号胶囊
            sc.d.rounded_rectangle([8, y0 + 3, 17, y0 + 12], radius=2,
                                   fill=DONE_COLOR if done else BOX_HI, outline=BOX_EDGE)
            sc.text_center(12.5, y0 + 3.6, str(i + 1), size=7.4,
                           fill=(250, 250, 246))
            head = f"{q.get('giver') or '委托人'} · {q.get('title') or '委托'}"
            sc.text(20, y0 + 3.2, _fit(sc, head, 150, 8.4), size=8.4,
                    fill=DONE_COLOR if done else TITLE_COLOR)
            if done:
                sc.text_right(231, y0 + 3.6, "已完成", size=7.2, fill=DONE_COLOR)
            # 说明
            desc = str(q.get("desc") or "")
            if desc:
                sc.text(20, y0 + 13.0, _fit(sc, desc, 210, 7.2), size=7.2, fill=TEXT_DIM)
            # 进度条 + 进度数字
            from . import quests as Q

            sc.text(8, y0 + 21.5, Q.progress_text({**q, "progress": cur}), size=7.4,
                    fill=TEXT)
            _progress_bar(sc, (8, y0 + 29.5, 116, y0 + 33.5),
                          cur / max(1, need), done=done)
            # 报酬
            reward = q.get("reward") or {}
            money = int(reward.get("money") or 0)
            sc.text_right(160, y0 + 21.5, f"{money}₽" if money else "", size=7.4,
                          fill=(160, 112, 24))
            _reward_icons(sc, reward.get("items") or {}, 164, y0 + 20, 9)
        # 剩下的空位也画出来,免得下面空一大片
        for j in range(len(rows), 3):
            y0 = top + j * (ch + 1.5)
            sc.window((5, y0, 235, y0 + ch), radius=2, shadow=False, edge=BOX_HI)
            sc.text(20, y0 + 3.2, "空位", size=8.4, fill=TEXT_DIM)
            sc.text(20, y0 + 14, "新的一天会有新的委托送上门。", size=7.2, fill=TEXT_DIM)
        total = len(quests or [])
        hint = (
            f"◆ 共 {total} 条,仅显示前 3 条 · /任务 放弃 <序号>"
            if total > 3
            else "◆ /任务 放弃 <序号> · 完成即刻发放金钱与道具"
        )
        sc.footer(hint)
        img = sc.finish()
        return img if len(img) else b""
    except Exception:  # 渲染失败必须回退文本
        logger.exception("委托板渲染失败")
        return b""
