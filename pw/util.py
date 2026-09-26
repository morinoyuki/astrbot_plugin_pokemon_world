"""通用工具:存档读写、游戏日界(凌晨 4 点)、稳定随机、文本格式化。"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import re
import time
from datetime import datetime, timedelta

from astrbot.api import logger

# ── 游戏日界:以本地时间凌晨 4 点为界 ──────────────────────────────
DAY_START_HOUR = 4


def game_day(now: datetime | None = None) -> int:
    """返回"游戏日"序号(自纪元起的天数,以 04:00 为分界)。

    04:00 之前算前一天 —— 这样"凌晨 4 点刷新"在跨日时天然成立。
    """
    now = now or datetime.now()
    shifted = now - timedelta(hours=DAY_START_HOUR)
    return shifted.date().toordinal()


def game_day_str(day: int | None = None) -> str:
    """游戏日 → 可读日期(该游戏日的 04:00 ~ 次日 04:00 对应的日历日)。"""
    day = game_day() if day is None else day
    d = datetime.fromordinal(day) + timedelta(hours=DAY_START_HOUR)
    return f"{d.year}-{d.month:02d}-{d.day:02d}"


def seconds_until_next_day(now: datetime | None = None) -> int:
    """距离下一次 04:00 刷新的秒数。"""
    now = now or datetime.now()
    nxt = (now - timedelta(hours=DAY_START_HOUR)).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1, hours=DAY_START_HOUR)
    return max(0, int((nxt - now).total_seconds()))


# ── 稳定随机:同样的种子永远得到同样的结果(便于回放/复现) ────────
def stable_rng(*parts) -> random.Random:
    raw = "|".join(str(p) for p in parts)
    h = hashlib.sha256(raw.encode("utf-8")).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def hash_int(*parts) -> int:
    raw = "|".join(str(p) for p in parts)
    return int.from_bytes(hashlib.sha256(raw.encode("utf-8")).digest()[:8], "big")


# ── 存档路径安全化 ────────────────────────────────────────────────
_SAFE = re.compile(r"[^\w.\-]+")


def safe_name(name: str) -> str:
    n = _SAFE.sub("_", str(name or "")).strip("._")
    return n or "default"


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def read_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning("宝可梦世界: 存档损坏 %s: %s", path, e)
        return None
    if not isinstance(data, dict):
        logger.warning("宝可梦世界: 存档非对象,已忽略 %s", path)
        return None
    return data


def write_json_atomic(path: str, data: dict) -> None:
    import uuid

    tmp = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    ensure_dir(os.path.dirname(path) or ".")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            with contextlib.suppress(OSError):
                os.remove(tmp)


def now_ts() -> int:
    return int(time.time())


# ── 数值/文本辅助 ─────────────────────────────────────────────────
def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def coerce_int(value, default: int | None = None) -> int | None:
    """把 LLM/用户传来的任意值强转 int;失败返回 default。"""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "").replace("，", "")
        m = re.search(r"-?\d+", s)
        if m:
            return int(m.group())
    return default


def bar(cur: int, total: int, width: int = 10, fill: str = "█", empty: str = "░") -> str:
    total = max(1, int(total))
    cur = clamp(cur, 0, total)
    n = round(width * cur / total)
    return fill * n + empty * (width - n)


def pct(cur: int, total: int) -> str:
    total = max(1, int(total))
    return f"{round(100 * clamp(cur, 0, total) / total)}%"


def zh_num(n: int) -> str:
    """1 → 一 … 用于"第 N 天""第 N 个徽章"这类展示。"""
    digits = "零一二三四五六七八九"
    n = int(n)
    if n < 0:
        return str(n)
    if n < 10:
        return digits[n]
    if n < 20:
        return "十" + (digits[n % 10] if n % 10 else "")
    if n < 100:
        return digits[n // 10] + "十" + (digits[n % 10] if n % 10 else "")
    return str(n)


def fmt_money(n: int) -> str:
    return f"{int(n):,}₽"
