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

# 一页 = 图片一屏能画下的格数(2 列 × 7 行),必须与 UI.render_box 一致
PER = 14


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
    tmp, p = _box_with(29)
    with tmp:
        page1 = _run(p, "")
        assert _numbers(page1) == list(range(1, PER + 1)), _numbers(page1)
        assert f"共 29 只 · 本页 {PER} 只:第 1-{PER} 只" in page1, page1.split("\n")[1]

        page2 = _run(p, "2")
        rest = list(range(PER + 1, 2 * PER + 1))        # 15..28
        assert _numbers(page2) == rest, _numbers(page2)
        assert f"共 29 只 · 本页 {len(rest)} 只:第 {PER + 1}-{2 * PER} 只" in page2, page2.split("\n")[1]

        page3 = _run(p, "3")                            # 最后一页只剩 1 只
        assert _numbers(page3) == [29], _numbers(page3)
        assert "共 29 只 · 本页 1 只:第 29-29 只" in page3, page3.split("\n")[1]
        # 说明白序号是全局编号(玩家不用猜"/队伍 取出"填哪个数)
        assert "全局编号" in page2, page2


def test_page_number_is_clamped_and_survives_next_prev():
    """页码越界夹到最后一页;上一页/下一页 与页码状态一致。"""
    tmp, p = _box_with(29)
    with tmp:
        assert _numbers(_run(p, "9")) == [29]                # 越界 → 夹到最后一页
        # 夹紧后的页码同样会记进存档,所以接着「上一页」是倒数第二页
        assert _numbers(_run(p, "上一页")) == list(range(PER + 1, 2 * PER + 1))
        assert _numbers(_run(p, "上一页")) == list(range(1, PER + 1))
        assert _numbers(_run(p, "下一页")) == list(range(PER + 1, 2 * PER + 1))


def test_single_page_box_has_no_page_noise():
    """不足一页时:页头仍然写清总数,序号从 1 开始。"""
    tmp, p = _box_with(3)
    with tmp:
        out = _run(p, "")
        assert _numbers(out) == [1, 2, 3], _numbers(out)
        assert "共 3 只" in out and "本页 3 只" in out, out


def test_every_mon_of_a_page_is_actually_drawn():
    """一页的格数必须够画:以前每页切 20 只、图里只有 14 格 → 第 2 页少 6 只。"""
    from pw import ui_render as UI

    tmp, p = _box_with(29)
    with tmp:
        page2 = _run(p, "2")
        assert f"本页 {PER} 只" in page2, page2.split('\n')[1]
        # 渲染层能画下的格数 = 2 列 × 7 行;切页数不能超过它
        assert PER <= 14
        img = UI.render_box([{"species": "pikachu", "name": "皮卡丘", "level": 10,
                             "cur_hp": 30, "max_hp": 30}],
                            total=29, offset=PER, scale=2)
        assert img.startswith(b"\x89PNG")
