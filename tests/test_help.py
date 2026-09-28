"""帮助信息必须与**实际支持**的写法保持一致。

用户反馈:"帮助信息里一些命令还是旧的 没更新 比如探索" ——
每次加子命令(探索的指向性目标、背包分页、交换请求…)都容易忘了回填帮助。
这里用两条测试锁住:① 注册的指令一个都不能漏;② 帮助里写的子命令必须真的能用
(直接跑一遍,看会不会回"用法错误")。
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402

# 帮助里必须出现的关键写法(加子命令时同步填这里)
DOCUMENTED = {
    "/探索": ["野生", "训练家", "道具", "事件", "40 步"],
    "/背包": ["分类", "页 2", "道具", "精灵球", "回复", "招式机", "重要"],
    "/商店": ["买", "卖", "页 2", "序号"],
    "/招式": ["电脑", "队伍序号"],
    "/交换": ["@对方", "接受", "拒绝", "取消", "序号"],
    "/持有": ["取下", "序号"],
    "/队伍": ["存入", "取出", "换位", "放生"],
    "/电脑": ["放生"],
    "/任务": ["放弃"],
    "/地图": ["飞行", "世界"],
    "/自动战斗": [],
    "/组队": ["接受", "拒绝", "离开"],
    "/双打": ["switch", "run"],
    "/前往": ["飞行"],
    "/道馆": ["挑战"],
    "/联盟": ["挑战"],
    "/大赛": ["挑战"],
    "/神兽": ["挑战"],
    "/主线": ["挑战"],
    "/对战": ["move", "switch", "item", "run", "forfeit", "tera", "mega"],
    "/mega": ["钥石"],
    "/捕捉": ["精灵球"],
    "/学招": ["替换", "放弃"],
    "/进化": ["道具"],
    "/使用": ["序号"],
    "/训练家战": ["序号"],
    "/重置世界": ["-all"],
}


def _help_text() -> str:
    return sys.modules["pw_plugin.prompts"].HELP_TEXT


def test_help_lists_every_registered_command():
    """注册了新指令就必须写进帮助。"""
    src = (Path(_ROOT) / "main.py").read_text(encoding="utf-8")
    names = [n for n, _ in re.findall(
        r'@filter\.command\("([^"]+)"(?:,\s*alias=\{([^}]*)\})?\)', src
    )]
    assert len(names) >= 25, f"只解析到 {len(names)} 条指令,正则可能过期了"
    ht = _help_text()
    missing = [n for n in names if f"/{n}" not in ht]
    assert not missing, f"帮助里没写这些指令:{missing}"


def test_help_documents_current_subcommands():
    """帮助里提到的子命令写法都要出现(防止子命令加了、帮助没更新)。"""
    ht = _help_text()
    gaps = []
    for cmd, subs in DOCUMENTED.items():
        assert cmd in ht, f"帮助里没有 {cmd}"
        gaps.extend(f"{cmd} 的「{sub}」" for sub in subs if sub not in ht)
    assert not gaps, "帮助信息落后于实现:" + "、".join(gaps)


def test_documented_forms_actually_work():
    """把帮助里写的写法跑一遍:不能回"用法错误"。"""
    p = _Cmd(tempfile.mkdtemp())
    p.config = {"ui_image": False, "quest_enable": False}
    run_cmd(p, _Event("/开始 小智 新叶喵"), p.cmd_start)

    cases = [
        ("/探索 野生", p.cmd_explore),
        ("/探索 道具", p.cmd_explore),
        ("/探索 事件", p.cmd_explore),
        ("/探索 训练家", p.cmd_explore),
        ("/背包 回复", p.cmd_bag),
        ("/背包 回复 页 2", p.cmd_bag),
        ("/背包 精灵球", p.cmd_bag),
        ("/招式 1", p.cmd_move),
        ("/招式 电脑 1", p.cmd_move),
        ("/持有", p.cmd_hold),
        ("/交换", p.cmd_trade),
        ("/交换 拒绝", p.cmd_trade),
        ("/队伍 换位 1 2", p.cmd_team),
        ("/队伍 存入 1", p.cmd_team),
        ("/电脑", p.cmd_box),
        ("/任务 放弃 9", p.cmd_quest),
        ("/图鉴 皮卡丘", p.cmd_dex),
        ("/商店", p.cmd_shop),
        ("/商店 页 2", p.cmd_shop),
        ("/商店 买 伤药 1", p.cmd_shop),
        ("/新手", p.cmd_tutorial),
    ]
    for cmd, fn in cases:
        ev = _Event(cmd)
        run_cmd(p, ev, fn)
        out = "".join(str(x) for x in ev.outputs)
        assert out.strip(), f"{cmd} 没有任何回应"
        assert "❌ 用法" not in out, f"帮助里写的 `{cmd}` 却被当成用法错误:{out[:160]}"
        assert "未知指令" not in out, f"`{cmd}` 不被识别:{out[:160]}"
