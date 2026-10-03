"""主动推送 → 「下次交互补发」。

QQ 官方接口**不能主动发消息** —— 群聊、私聊都一样,到点推送会被平台拒。
所以把要推给某个会话的内容先按 scope 攒起来,等该会话里有人交互
(发指令 / 被 @ / 私聊说话 / 点按钮)时随这次回复补发。

约定:
- 只有在「平台不能主动推」或「主动推失败」时才会入队(见 main.py 的
  `_notify` / `_announce`);aiocqhttp 等能主动推的平台行为完全不变;
- 暂存写 `data/pokemon_world/pending_push.json`,插件重启后依然能补发;
- 每个会话最多留 `MAX_PER_SCOPE` 条、单条最长 `MAX_TEXT`,
  超上限丢最旧的 —— 群里长期没人说话也不会无限堆积;
- 超过 `TTL` 的旧消息在取走时自动丢弃(没人互动的死群不会突然诈尸)。
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time

PENDING: dict[str, list[dict]] = {}
"""scope → [{"text": str, "kind": str, "ts": float}, ...],按入队顺序。"""

MAX_SCOPES = 300
MAX_PER_SCOPE = 5
MAX_TEXT = 700
TTL = 14 * 86400

KIND_DAILY = "daily"
KIND_PVP = "pvp"


def reset() -> None:
    """清空全部暂存(测试用;生产走 load)。"""
    PENDING.clear()


def queue(scope: str, text: str, *, kind: str = "notice",
          ts: float | None = None) -> None:
    """把一条要补发的内容入队(同会话连续重复的内容不叠)。"""
    scope = str(scope or "")
    text = str(text or "").strip()
    if not scope or not text:
        return
    if len(text) > MAX_TEXT:
        text = text[: MAX_TEXT - 1] + "…"
    items = PENDING.setdefault(scope, [])
    if items and str(items[-1].get("text") or "") == text:
        return
    items.append({
        "text": text,
        "kind": str(kind or "notice"),
        "ts": float(ts if ts is not None else time.time()),
    })
    del items[: max(0, len(items) - MAX_PER_SCOPE)]
    if len(PENDING) > MAX_SCOPES:
        for key in list(PENDING)[: len(PENDING) - MAX_SCOPES]:
            PENDING.pop(key, None)


def has(scope: str, *, kind: str | None = None) -> bool:
    """该会话有没有待补发(可只看某一类)。"""
    items = PENDING.get(str(scope or "")) or []
    if kind is None:
        return bool(items)
    return any(str(it.get("kind") or "") == str(kind) for it in items)


def take(scope: str, *, now: float | None = None) -> list[str]:
    """取走该会话的全部待补发文本(顺带丢掉过期的),交给调用方发送。"""
    items = PENDING.pop(str(scope or ""), []) or []
    cutoff = float(now if now is not None else time.time()) - TTL
    out: list[str] = []
    for it in items:
        text = str(it.get("text") or "").strip()
        if not text:
            continue
        if float(it.get("ts") or 0) < cutoff:
            continue
        out.append(text)
    return out


def drop(scope: str, kind: str) -> int:
    """丢掉某类待补发(例如玩家已用 `/今日` 看过世界事件)。

    Returns:
        丢掉了几条。
    """
    scope = str(scope or "")
    items = PENDING.get(scope)
    if not items:
        return 0
    want = str(kind or "")
    keep = [it for it in items if str(it.get("kind") or "") != want]
    lost = len(items) - len(keep)
    if keep:
        PENDING[scope] = keep
    else:
        PENDING.pop(scope, None)
    return lost


def load(path: str) -> int:
    """从 JSON 文件读回暂存(覆盖内存),返回读到的会话数;坏了就当空。"""
    PENDING.clear()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, TypeError):
        return 0
    if not isinstance(data, dict):
        return 0
    for scope, items in data.items():
        if not isinstance(items, list):
            continue
        for it in items[:MAX_PER_SCOPE]:
            if isinstance(it, dict) and str(it.get("text") or "").strip():
                PENDING.setdefault(str(scope), []).append({
                    "text": str(it.get("text")),
                    "kind": str(it.get("kind") or "notice"),
                    "ts": float(it.get("ts") or 0),
                })
    return len(PENDING)


def save(path: str) -> None:
    """原子写回暂存(失败就静默放弃:补发丢了总好过把插件搞崩)。"""
    payload = {
        scope: items
        for scope, items in PENDING.items()
        if items
    }
    root = os.path.dirname(path)
    if root:
        with contextlib.suppress(OSError):
            os.makedirs(root, exist_ok=True)
    tmp = ""
    try:
        fd, tmp = tempfile.mkstemp(dir=root or ".", prefix=".pw_push_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
        tmp = ""
    except OSError:
        if tmp:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
