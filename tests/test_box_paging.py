"""/电脑 翻页:序号必须是**全局编号**,页头要把总数/本页数写清楚。"""

from __future__ import annotations

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402

from pw.engine import create_pokemon  # noqa: E402
from pw.player import mon_to_dict  # noqa: E402

PER = 20


def _box_with(n: int):
    tmp = tempfile.TemporaryDirectory()
    p = _Cmd(tmp.name)
    p.config = {"ui_image": False}
    run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
    t = p._load(_Event())
    t.data["box"] = [mon_to_dict(create_pokemon("pikachu", 10 + i)) for i in range(n)]
    p._save(t)
    return tmp, p


def _run(p, arg: str) -> str:
    ev = _Event(f"/电脑 {arg}".strip())
    run_cmd(p, ev, p.cmd_box)
    return "\n".join(ev.outputs)


def _numbers(out: str) -> list[int]:
    got = []
    for line in out.split("\n"):
        head = line.split(".", 1)[0].strip()
        if head.isdigit():
            got.append(int(head))
    return got


def test_page_two_keeps_absolute_numbers_and_reports_real_counts():
    """第 2 页要从 21 数起;页头写“共 25 只 · 本页 5 只:第 21-25 只”。"""
    tmp, p = _box_with(25)
    with tmp:
        page1 = _run(p, "")
        assert _numbers(page1) == list(range(1, PER + 1)), _numbers(page1)
        assert f"共 25 只 · 本页 {PER} 只:第 1-{PER} 只" in page1, page1.split("\n")[1]

        page2 = _run(p, "2")
        assert _numbers(page2) == [21, 22, 23, 24, 25], _numbers(page2)
        assert "共 25 只 · 本页 5 只:第 21-25 只" in page2, page2.split("\n")[1]
        # 说明白序号是全局编号(玩家不用猜"/队伍 取出"填哪个数)
        assert "全局编号" in page2, page2


def test_page_number_is_clamped_and_survives_next_prev():
    """页码越界夹到最后一页;上一页/下一页 与页码状态一致。"""
    tmp, p = _box_with(25)
    with tmp:
        assert _numbers(_run(p, "9")) == [21, 22, 23, 24, 25]      # 越界 → 本轮显示最后一页
        # 越界页码只影响这一次显示(不写进存档),所以接着「上一页」仍是从第 1 页算
        assert _numbers(_run(p, "上一页")) == list(range(1, PER + 1))
        assert _numbers(_run(p, "下一页")) == [21, 22, 23, 24, 25]
        assert _numbers(_run(p, "上一页")) == list(range(1, PER + 1))


def test_single_page_box_has_no_page_noise():
    """不足一页时:页头仍然写清总数,序号从 1 开始。"""
    tmp, p = _box_with(3)
    with tmp:
        out = _run(p, "")
        assert _numbers(out) == [1, 2, 3], _numbers(out)
        assert "共 3 只" in out and "本页 3 只" in out, out
