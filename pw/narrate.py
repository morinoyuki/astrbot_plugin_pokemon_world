"""LLM 叙事封装。

内核(数值/判定)与文字完全分离:
  · 内核把"事实"整理成结构化文本;
  · LLM 只被允许把这些事实润色成叙事,禁止新增/修改任何数值;
  · LLM 不可用/超时/输出异常时,直接返回内核的模板文本(游戏永远能继续)。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from astrbot.api import logger

from .events import parse_llm_json

Generate = Callable[[str, str], Awaitable[str]]


@dataclass
class Reply:
    text: str = ""
    error: str = ""
    used_fallback: bool = False


class Narrator:
    """把 `generate(system_prompt, user_prompt) -> str` 包一层,失败自动降级。"""

    def __init__(self, generate: Generate | None = None):
        self._generate = generate

    def available(self) -> bool:
        return self._generate is not None

    async def say(self, system: str, user: str, *, fallback: str = "") -> Reply:
        if self._generate is None:
            return Reply(text=fallback, used_fallback=True)
        try:
            text = await self._generate(system, user)
        except Exception as e:
            logger.warning("宝可梦世界: LLM 调用失败,使用兜底文本: %s", e)
            return Reply(text=fallback, error=str(e), used_fallback=True)
        text = (text or "").strip()
        if not text:
            return Reply(text=fallback, error="empty", used_fallback=True)
        return Reply(text=text)

    async def json(self, system: str, user: str, *, fallback: str = "{}") -> dict:
        rep = await self.say(system, user, fallback=fallback)
        return parse_llm_json(rep.text)


# ── 叙事提示词 ───────────────────────────────────────────────────
NARRATE_SYSTEM = """你是一款宝可梦文字游戏的叙事者。
你会收到游戏内核给出的**既成事实**(战斗结果、捕获、升级、事件、地形等)。
你的职责只有一件事:把这些事实写成有画面感、节奏利落的简体中文叙事。

铁律(违反即失败):
1. 不得新增、删除、修改任何数值(等级/HP/伤害/经验/金钱/道具数量/徽章);
2. 不得虚构事实中不存在的宝可梦、道具或人物;
3. 不得替玩家做决定,不得推进玩家未选择的剧情分支;
4. 长度 120~320 字,分段自然,不要使用 Markdown 标题;
5. 结尾可以留一个"玩家可以做什么"的方向,但不要列出编号选项。"""


def facts_block(*, title: str, facts: list[str], extra: str = "") -> str:
    lines = [f"## 场景:{title}", "## 既成事实(不可改动):"]
    lines += [f"- {f}" for f in facts if f]
    if extra:
        lines.append(f"## 补充:{extra}")
    return "\n".join(lines)
