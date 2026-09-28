"""`/招式` 必须能查队伍里**任意一只**宝可梦的招式(序号或名字),且老用法不退化。"""

import random
import sys
import tempfile

sys.path.insert(0, "tests")

from test_commands import _Cmd, _Event, run_cmd

from pw.engine import create_pokemon

random.seed(7)


def _setup(tmp):
    p = _Cmd(tmp)
    p.config = {"quest_enable": False, "narrate_enable": False}
    run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
    data = p.trainers.load("g10086", "u1")
    data["party"].append(create_pokemon("pikachu", 25).to_dict())
    p.trainers.save("g10086", "u1", data)
    return p


def _out(p, args):
    out = run_cmd(p, _Event(args), p.cmd_move)
    return "".join(
        (str(x[1]) if isinstance(x, tuple) and len(x) > 1 else str(x))
        for x in out
    )


def test_current_mons_move_by_index_still_shows_detail():
    """`/招式 <序号>`(非战斗)不能因新能力变含义 —— 仍是当前宝可梦的第 N 招详情。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _setup(tmp)
        detail = _out(p, "/招式 1")
        assert "杰尼龟" in detail and "威力" in detail, detail


def test_party_name_lists_moves():
    """`/招式 <宝可梦名>`:按名字(物种中文/昵称)定位队伍成员。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _setup(tmp)
        text = _out(p, "/招式 皮卡丘")
        assert "皮卡丘 Lv25 的招式" in text, text
        assert "高速移动" in text, text


def test_party_name_move_detail():
    """`/招式 <宝可梦名> <招式名>`:那只宝可梦的具体招式资料。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _setup(tmp)
        detail = _out(p, "/招式 皮卡丘 高速移动")
        assert "皮卡丘 Lv25" in detail, detail
        assert "威力" in detail and "命中" in detail and "PP" in detail, detail


def test_move_detail_for_each_party_slot():
    """`/招式 <队伍序号> <招式序号>`:每只都能查详情。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _setup(tmp)
        d1 = _out(p, "/招式 1 1")
        assert "杰尼龟" in d1 and "威力" in d1, d1
        d2 = _out(p, "/招式 2 1")
        assert "皮卡丘" in d2 and "威力" in d2, d2


def test_unlearned_move_query_still_falls_back_to_dex():
    """队伍里没叫这个名的宝可梦时,招式名查询(全图鉴)不能被名字查找劫持。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _setup(tmp)
        text = _out(p, "/招式 十万伏特")
        assert "十万伏特" in text and "没有学会这招" in text, text


def test_out_of_range_index_keeps_old_move_detail_meaning():
    """`/招式 <N>` 且 N 超出队伍规模:保留老含义 —— 当前宝可梦的第 N 招详情。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _setup(tmp)
        text = _out(p, "/招式 9")
        assert "它只有" in text, text      # 杰尼龟 2 招,第 9 招不存在
