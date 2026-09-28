"""调度器凌晨推送:必须用 astrbot 认可的 MessageChain 载荷,且玩家指令先滚好今天也要推。"""

import asyncio
import tempfile

from astrbot.api.event import MessageChain
from test_commands import _Cmd, _Event, run_cmd

from pw.util import game_day


class _FakeCtx:
    """模仿真实 astrbot 的 send_message 约定:第二参数必须是 MessageChain。

    真实适配器(aiocqhttp 等)拿 `chain.chain` 遍历组件,传裸 list 直接
    AttributeError —— 测试就是要卡住这种用法。
    """

    def __init__(self):
        self.sent: list[tuple[str, MessageChain]] = []

    async def send_message(self, umo, chain):
        assert isinstance(chain, MessageChain), f"send_message 收到裸对象:{type(chain)}"
        self.sent.append((umo, chain))


def _text(chain: MessageChain) -> str:
    return "".join(str(getattr(c, "text", "") or "") for c in chain.chain)


def _start(p):
    run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)


def test_daily_scheduler_push_uses_message_chain():
    """世界停在昨天 → 调度器滚出新一天,推送必须带着真 MessageChain。

    回归:1.16.0 起 `_notify` 把裸 list 传给 send_message,平台适配器拿
    `.chain` 抛 AttributeError 被吞掉 —— 每日推送一直发不出去。
    """
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"quest_enable": False, "narrate_enable": False}
        _start(p)
        # 真实玩家跑过指令后,世界状态才会落盘并带上 umo
        run_cmd(p, _Event("/状态"), p.cmd_status)
        ctx = _FakeCtx()
        p.context = ctx
        st = p._state("g10086")
        st.data["last_roll_day"] = int(game_day()) - 1        # 世界停在昨天
        p._save_state(st)
        asyncio.run(p._roll_all(game_day()))
        assert ctx.sent, "每日推送没有发出去"
        umo, chain = ctx.sent[0]
        assert str(umo).startswith("test:GroupMessage:"), umo
        text = _text(chain)
        assert "世界第" in text and "/今日" in text, text


def test_daily_scheduler_still_pushes_when_player_rolled_first():
    """4 点整恰有玩家操作时,玩家指令先滚好今天(调度器 rolled=False),
    今日事件也要照样推给群里 —— 否则这一天就不会有推送了。
    """
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"quest_enable": False, "narrate_enable": False}
        _start(p)
        # 真实玩家跑过指令后,世界状态才会落盘并带上 umo
        run_cmd(p, _Event("/状态"), p.cmd_status)
        ctx = _FakeCtx()
        p.context = ctx
        day = int(game_day())
        st = p._state("g10086")
        st.data["last_roll_day"] = day                        # 玩家指令已经滚好
        st.data.setdefault("events", []).append({
            "id": "e1", "kind": "world", "region": "kanto",
            "title": "野生宝可梦骚动", "desc": "常磐森林里出现了异常脚印。",
            "day": day, "created_day": day, "until_day": day, "location": "",
        })
        p._save_state(st)
        asyncio.run(p._roll_all(day))
        assert ctx.sent, "玩家先滚好时调度器没推送"
        text = _text(ctx.sent[0][1])
        assert "野生宝可梦骚动" in text, text
