"""消息按钮(QQ 官方 InlineKeyboard)适配层。

只有 QQ 官方适配器(Websocket/Webhook)支持消息按钮;其他平台、以及没合并
QQ keyboard 支持的老版本 AstrBot,本模块会安静地退化成「什么也不加」——
按钮只是锦上添花,任何一步失败都绝不能影响原有指令流程。

按钮的 action 用**回调**(type=1):点击后 QQ 推 INTERACTION_CREATE,
插件里的 `on_keyboard_click` 把按钮的 data 当成指令文本执行 ——
所以按钮和手打 `/对战 1`、`/商店 页 2` 走的是**同一套内核逻辑**,
不存在两套行为分叉。

QQ 的限制(超过直接 400):最多 5 行、每行最多 5 个按钮、label 最多 10 字符;
本模块统一在这里裁剪,调用方随便传。
"""

from __future__ import annotations

from typing import Any

try:  # 新版 AstrBot(合并了 QQ keyboard 支持)才有这两个组件
    from astrbot.core.platform.sources.qqofficial.components import (
        QQCButton,
        QQCKeyboard,
    )
except Exception:  # pragma: no cover - 旧版 AstrBot:静默退化
    QQCButton = None  # type: ignore[assignment]
    QQCKeyboard = None  # type: ignore[assignment]

MODE: dict[str, str] = {"mode": "auto"}
"""auto:仅 QQ 官方平台加按钮;never:完全关闭。由插件配置 `keyboard` 写入。"""

MAX_ROWS = 5
MAX_PER_ROW = 5
MAX_LABEL = 10


def available() -> bool:
    """当前 AstrBot 是否具备 QQ 按钮组件。"""
    return QQCButton is not None and QQCKeyboard is not None


def supported(event) -> bool:
    """这条消息要不要附按钮(平台 + 配置)。"""
    if not available():
        return False
    mode = str(MODE.get("mode") or "auto").strip().lower()
    if mode in ("never", "off", "false", "0", "no"):
        return False
    if mode in ("always", "on", "true", "1", "yes"):
        return True
    try:
        plat = str(event.get_platform_name() or "").lower()
    except Exception:
        return False
    return "qq" in plat and "official" in plat


def button(label: str, data: str, *, style: int = 1, enter: bool = False) -> Any:
    """造一个回调按钮(点击 → INTERACTION_CREATE,data 由插件当指令执行)。

    style: 0 灰框 / 1 蓝框(默认)。
    """
    if not available():
        return None
    text = str(label or "").strip()[:MAX_LABEL] or "·"
    payload = str(data or "").strip()
    if not payload:
        return None
    try:
        return QQCButton(
            id=payload[:64],
            label=text,
            data=payload,
            style=int(style),
            action_type=1,  # 回调
            enter=bool(enter),
        )
    except Exception:  # 组件构造失败就当没有按钮
        return None


def row(*items) -> list:
    """把若干 (label, data) 或按钮对象拼成一行(超过 5 个自动截断)。"""
    out: list = []
    for item in items or []:
        if item is None:
            continue
        if isinstance(item, tuple):
            if len(item) < 2:
                continue
            btn = button(item[0], item[1], **(item[2] if len(item) > 2 else {}))
        else:
            btn = item
        if btn is not None:
            out.append(btn)
    return out[:MAX_PER_ROW]


def grid(items, *, per_row: int = 5) -> list[list]:
    """[(label, data), ...] → 多行按钮(超过 5 行自动截断)。"""
    per = max(1, min(int(per_row or 1), MAX_PER_ROW))
    flat = [x for x in (items or []) if x]
    rows = [row(*flat[i : i + per]) for i in range(0, len(flat), per)]
    return [r for r in rows if r][:MAX_ROWS]


def keyboard(event, rows) -> Any | None:
    """rows(二维列表)→ QQCKeyboard;不支持按钮时返回 None。"""
    if not supported(event):
        return None
    clean: list[list] = []
    for r in rows or []:
        if not r:
            continue
        btns = [b for b in r if b is not None][:MAX_PER_ROW]
        if btns:
            clean.append(btns)
        if len(clean) >= MAX_ROWS:
            break
    if not clean:
        return None
    try:
        return QQCKeyboard(rows=clean)
    except Exception:
        return None


def attach(event, result, rows):
    """把键盘挂到一条 MessageEventResult 上(原地追加,失败原样返回)。

    带按钮的消息 QQ 只认 markdown 格式,这里显式标记 use_markdown(True):
    适配器也会自动开 markdown,但显式写出来日志里不会有那条 warning。
    """
    kb = keyboard(event, rows)
    if kb is None or result is None:
        return result
    try:
        chain = getattr(result, "chain", None)
        if chain is None:
            return result
        chain.append(kb)
        with_md = getattr(result, "use_markdown", None)
        if callable(with_md):
            with_md(True)
    except Exception:
        return result
    return result


def click_data(event) -> str:
    """按钮点击回调里的按钮 data;非交互事件返回空串。

    只认 QQ 官方事件类提供的 `get_interaction_button_data()`(合并 PR 后新增);
    其他平台 / 老版本没有这个方法 → 永远返回空串,过滤器不会误伤普通消息。
    """
    getter = getattr(event, "get_interaction_button_data", None)
    if not callable(getter):
        return ""
    try:
        return str(getter() or "")
    except Exception:
        return ""


def is_click(event) -> bool:
    """当前事件是不是按钮点击回调。"""
    checker = getattr(event, "is_button_interaction", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    return bool(click_data(event))
