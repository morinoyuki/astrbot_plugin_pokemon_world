"""所有"开战"入口都必须输出战斗画面 + 招式提示。

`/训练家战` 曾经只 `yield plain_result(self._battle_intro(...))` ——
既没有战斗画面,也没有招式提示,玩家只看到"我方派出了 X / 对方派出了 Y",
完全不知道下一步该敲什么。同类的还有 `/道馆 挑战` 与 `/联盟` 的冠军战。
这里用静态扫描 + 端到端各锁一道。
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


def _source() -> str:
    return (Path(_ROOT) / "main.py").read_text(encoding="utf-8")


def test_every_battle_start_goes_through_emit_battle():
    """`B.start()` 之后必须走 `_emit_battle`(否则既没画面也没招式提示)。"""
    lines = _source().split("\n")
    starts = [i for i, ln in enumerate(lines, 1) if re.search(r"\bB\.start\(", ln)]
    emits = [i for i, ln in enumerate(lines, 1) if "_emit_battle(" in ln]
    assert starts, "没找到任何 B.start 调用,正则可能过期了"
    orphans = [s for s in starts if not any(s < e <= s + 40 for e in emits)]
    assert not orphans, (
        "这些开战入口没走 _emit_battle(会缺战斗画面与招式提示):" f"行 {orphans}"
    )
    # 也不允许再出现裸的 plain_result(self._battle_intro(...))
    bare = [
        i for i, ln in enumerate(lines, 1)
        if "plain_result(self._battle_intro(" in ln
    ]
    assert not bare, f"行 {bare} 直接发开场白,应改为 _emit_battle"


def _start(tmp):
    p = _Cmd(tmp)
    p.config = {"ui_image": True, "battle_image": True,
                "battle_image_scale": 2, "quest_enable": False}
    run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
    return p


def test_npc_battle_shows_image_and_move_hint():
    """`/训练家战` 要给出战斗画面 + 我方招式 + 行动示例。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _start(tmp)
        t = p._load(_Event(""))
        t.data["location"] = "kanto-route-2"      # 这条路上有训练家
        p._save(t)
        ev = _Event("/训练家战 1")
        run_cmd(p, ev, p.cmd_npc)
        out = "".join(str(x) for x in ev.outputs)
        assert "<chain:" in out, f"没有战斗画面:{out[:200]}"
        assert "招式:" in out, f"没有列出我方招式:{out[:300]}"
        assert "/对战" in out, f"没有行动示例:{out[:300]}"
        assert p._load(_Event("")).data.get("battle"), "这一战没真正开起来"


def test_gym_battle_shows_image_and_move_hint():
    """`/道馆 挑战` 同样要有战斗画面与招式提示。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _start(tmp)
        t = p._load(_Event(""))
        t.data["location"] = "pewter-city"        # 小刚
        t.data["region"] = "kanto"
        p._save(t)
        ev = _Event("/道馆 挑战")
        run_cmd(p, ev, p.cmd_gym)
        out = "".join(str(x) for x in ev.outputs)
        assert "<chain:" in out, f"没有战斗画面:{out[:200]}"
        assert "招式:" in out, f"没有列出我方招式:{out[:300]}"
        assert p._load(_Event("")).data.get("battle")
