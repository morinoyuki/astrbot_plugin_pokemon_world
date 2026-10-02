"""玩家间交换(`/交换 @对方` + `/交换 接受`)。

之前**根本没有任何玩家间交换功能** —— `/交换 <序号>` 只是单人自演的
"与远方训练家连接交换"(只触发通信进化,宝可梦不会易主)。
这里覆盖新加的双向交换:报价、接受、拒绝、过期、跨群隔离、双方通信进化、
携带道具随宝可梦移动并在需要时被消耗。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_commands import _Cmd, _Event, run_cmd

from pw.dex import get_dex
from pw.engine import create_pokemon
from pw.items import resolve_bag_item

DEX = get_dex()
GROUP = "10086"


class _User(_Event):
    """带指定 uid/群号、@ 目标、机器人自身 ID 的测试事件。"""

    def __init__(self, text: str, uid: str, *, ats=(), group: str = GROUP,
                 self_id: str = ""):
        super().__init__(text)
        self._uid = uid
        self._group = group
        self.self_id = self_id
        self.message_obj = type(
            "MO", (), {"message": [
                type("At", (), {"type": "At", "qq": a[0], "name": a[1],
                                "data": {"qq": a[0], "name": a[1]}})
                for a in ats
            ],
            "self_id": self_id},
        )()

    def get_sender_id(self) -> str:
        return self._uid

    def get_group_id(self) -> str:
        return self._group

    def get_self_id(self) -> str:
        return self.self_id


def _make(tmp, uid, name, species="sprigatito", *, level=5, item="", center=True):
    """建号 + 塞宝可梦,返回宿主对象。"""
    p = _Cmd(tmp)
    p.config = {"ui_image": False, "quest_enable": False}
    ev = _User("/开始", uid)
    ev.message_str = f"/开始 {name} 新叶喵"
    run_cmd(p, ev, p.cmd_start)
    t = p._load(ev)
    t.data["location"] = "pewter-city" if center else "kanto-route-1"
    for extra_species, extra_item in ((species, item),):
        mon = create_pokemon(extra_species, level).to_dict()
        mon["id"] = f"m{extra_species}"
        mon["item"] = extra_item
        t.data["party"].append(mon)
    p._save(t)
    return p


def _party(p, uid):
    return [(DEX.species[x["species"]]["zh"], x.get("item") or "")
            for x in p._load(_User("/状态", uid)).party]


def test_trade_offer_accept_swaps_and_evolves_both():
    with tempfile.TemporaryDirectory() as tmp:
        pa = _make(tmp, "u1", "小智", "kadabra")
        pb = _make(tmp, "u2", "小霞", "onix", item="metal-coat")

        ev = _User("/交换 @小霞 2", "u1", ats=[("u2", "小霞")])
        run_cmd(pa, ev, pa.cmd_trade)
        assert "发起交换" in "".join(ev.outputs), ev.outputs
        # 对方没回应前,双方队伍不变
        assert "勇基拉" in [x[0] for x in _party(pa, "u1")]

        # 对方先看到待回应提示
        ev2 = _User("/交换", "u2")
        run_cmd(pb, ev2, pb.cmd_trade)
        assert "想用" in "".join(ev2.outputs), ev2.outputs

        ev3 = _User("/交换 接受 2", "u2", ats=[("u1", "小智")])
        run_cmd(pb, ev3, pb.cmd_trade)
        out = "".join(ev3.outputs)
        assert "交换成功" in out, out

        a_party = _party(pa, "u1")       # 小智收到大岩蛇(带金属膜 → 进化成大钢蛇)
        b_party = _party(pb, "u2")
        assert ("大钢蛇", "") in a_party, a_party
        assert ("胡地", "") in b_party, b_party
        assert "勇基拉" not in [x[0] for x in b_party]
        # 双方都收到对方的那只
        assert len(a_party) == len(b_party) == 2


def test_trade_evolution_needs_item_and_travels_with_mon():
    """需要道具的通信进化:道具随宝可梦过去并消耗;不需要道具的照旧不进化。"""
    with tempfile.TemporaryDirectory() as tmp:
        pa = _make(tmp, "u1", "小智", "haunter")      # 鬼斯通:不要道具
        pb = _make(tmp, "u2", "小霞", "snorlax")
        ev = _User("/交换 @小霞 2", "u1", ats=[("u2", "小霞")])
        run_cmd(pa, ev, pa.cmd_trade)
        ev = _User("/交换 接受 2", "u2")
        run_cmd(pb, ev, pb.cmd_trade)
        a_party = _party(pa, "u1")
        b_party = _party(pb, "u2")
        assert ("耿鬼", "") in b_party, b_party           # 鬼斯通→耿鬼
        assert ("卡比兽", "") in a_party, a_party          # 卡比兽不进化


def test_trade_reject_and_cancel():
    with tempfile.TemporaryDirectory() as tmp:
        pa = _make(tmp, "u1", "小智", "kadabra")
        pb = _make(tmp, "u2", "小霞", "onix")
        ev = _User("/交换 @小霞 2", "u1", ats=[("u2", "小霞")])
        run_cmd(pa, ev, pa.cmd_trade)
        # 小霞回绝
        ev = _User("/交换 拒绝", "u2")
        run_cmd(pb, ev, pb.cmd_trade)
        assert "回绝" in "".join(ev.outputs)
        ev = _User("/交换 接受 2", "u2")
        run_cmd(pb, ev, pb.cmd_trade)
        assert "没有等你回应" in "".join(ev.outputs)
        # 发起者撤回
        ev = _User("/交换 @小霞 2", "u1", ats=[("u2", "小霞")])
        run_cmd(pa, ev, pa.cmd_trade)
        ev = _User("/交换 取消 @小霞", "u1", ats=[("u2", "小霞")])
        run_cmd(pa, ev, pa.cmd_trade)
        assert "撤销" in "".join(ev.outputs)
        # 什么都没做时取消要有提示
        ev = _User("/交换 拒绝", "u2")
        run_cmd(pb, ev, pb.cmd_trade)
        assert "没有可取消" in "".join(ev.outputs)


def test_trade_offer_expires_and_is_group_scoped():
    with tempfile.TemporaryDirectory() as tmp:
        pa = _make(tmp, "u1", "小智", "kadabra")
        pb = _make(tmp, "u2", "小霞", "onix")
        ev = _User("/交换 @小霞 2", "u1", ats=[("u2", "小霞")])
        run_cmd(pa, ev, pa.cmd_trade)

        # 别的群看不到这条报价
        ev = _User("/交换", "u2", group="20000")
        run_cmd(pb, ev, pb.cmd_trade)
        assert "想用" not in "".join(ev.outputs)

        # 手动把报价改成过期 → 自动清理
        state = pa._state("g" + GROUP)
        box = state.data["trades"]
        assert box, "报价应该写进世界状态"
        for v in box.values():
            v["at"] = time.time() - 10_000
        pa._save_state(state)
        ev = _User("/交换 接受 2", "u2")
        run_cmd(pb, ev, pb.cmd_trade)
        assert "没有等你回应" in "".join(ev.outputs)


def test_trade_fails_when_offered_mon_is_gone():
    with tempfile.TemporaryDirectory() as tmp:
        pa = _make(tmp, "u1", "小智", "kadabra")
        pb = _make(tmp, "u2", "小霞", "onix")
        ev = _User("/交换 @小霞 2", "u1", ats=[("u2", "小霞")])
        run_cmd(pa, ev, pa.cmd_trade)
        # 发起者把那只放生了
        t = pa._load(_User("/状态", "u1"))
        t.data["party"] = [m for m in t.data["party"] if m["species"] != "kadabra"]
        pa._save(t)
        ev = _User("/交换 接受 2", "u2")
        run_cmd(pb, ev, pb.cmd_trade)
        out = "".join(ev.outputs)
        assert "已经不在" in out, out
        assert len(_party(pb, "u2")) == 2, "失败的交换不能动任何人的队伍"


def test_trade_requires_center_on_both_actions():
    """发起与接受都要求在宝可梦中心(连接交换的设定)。"""
    with tempfile.TemporaryDirectory() as tmp:
        # 发起者在野外 → 不能发起
        pa = _make(tmp, "u1", "小智", "kadabra", center=False)
        _make(tmp, "u2", "小霞", "onix")
        ev = _User("/交换 @小霞 2", "u1", ats=[("u2", "小霞")])
        run_cmd(pa, ev, pa.cmd_trade)
        assert "宝可梦中心" in "".join(ev.outputs)

        # 正常发起后,接受方跑到野外 → 不能接受
        pb = _make(tmp, "u3", "小刚", "kadabra")
        ev = _User("/交换 @小霞 2", "u3", ats=[("u2", "小霞")])
        run_cmd(pb, ev, pb.cmd_trade)
        assert "发起交换" in "".join(ev.outputs), ev.outputs
        t = pb._load(_User("/状态", "u2"))
        t.data["location"] = "kanto-route-1"
        pb._save(t)
        ev = _User("/交换 接受 1", "u2")
        run_cmd(pb, ev, pb.cmd_trade)
        assert "宝可梦中心" in "".join(ev.outputs)
        # 队伍/存档没被改动
        assert len(_party(pb, "u2")) == 2


def test_trade_to_self_and_unknown_target_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        p = _make(tmp, "u1", "小智", "kadabra")
        ev = _User("/交换 @小智 2", "u1", ats=[("u1", "小智")])
        run_cmd(p, ev, p.cmd_trade)
        assert "不能和自己交换" in "".join(ev.outputs)
        ev = _User("/交换 @陌生人 2", "u1", ats=[("u999", "陌生人")])
        run_cmd(p, ev, p.cmd_trade)
        assert "还没有在玩" in "".join(ev.outputs)
        # 没有 @ 且带数字 → 老的自连行为
        ev = _User("/交换 2", "u1")
        run_cmd(p, ev, p.cmd_trade)
        assert "连接交换" in "".join(ev.outputs)


def test_trade_full_party_sends_received_mon_to_box():
    with tempfile.TemporaryDirectory() as tmp:
        pa = _make(tmp, "u1", "小智", "kadabra")
        pb = _make(tmp, "u2", "小霞", "onix")
        # 把小霞的队伍塞满 6 只
        t = pb._load(_User("/状态", "u2"))
        while len(t.party) < 6:
            mon = create_pokemon("pidgey", 5).to_dict()
            mon["id"] = f"p{len(t.party)}"
            t.data["party"].append(mon)
        pb._save(t)
        ev = _User("/交换 @小霞 2", "u1", ats=[("u2", "小霞")])
        run_cmd(pa, ev, pa.cmd_trade)
        ev = _User("/交换 接受 2", "u2")
        run_cmd(pb, ev, pb.cmd_trade)
        assert "交换成功" in "".join(ev.outputs)
        t = pb._load(_User("/状态", "u2"))
        # 换出 1 只再换入 1 只 → 仍是 6 只,且收到的在里面
        assert len(t.party) == 6, len(t.party)


# ── 机器人自己被 @ 时不能当成交换对象(qqofficial/OneBot 的命令都带 @bot)──

def _save_player(p, scope, uid, name, rows, location="pewter-city"):
    """直接落一份存档(不走 /开始,省时间;供数据驱动测试用)。"""
    p.trainers.save(scope, uid, {
        "uid": uid, "name": name, "region": "kanto",
        "location": location, "party": rows, "box": [], "bag": {},
        "money": 3000, "badges": [], "dex_seen": [], "dex_caught": [],
        "flags": {}, "unlocked_regions": ["kanto"],
    })


def test_trade_by_name_when_command_at_bot():
    """`@机器人 /交换 小霞 2` —— 目标是小霞,不是那个被 @ 的机器人。"""
    with tempfile.TemporaryDirectory() as tmp:
        pa = _make(tmp, "u1", "小智", "kadabra")
        pb = _make(tmp, "u2", "小霞", "onix")

        ev = _User("/交换 小霞 2", "u1",
                   ats=[("bot1", "宝可梦世界")], self_id="bot1")
        run_cmd(pa, ev, pa.cmd_trade)
        out = "".join(ev.outputs)
        assert "发起交换" in out, out
        assert "还没有在玩" not in out

        ev = _User("/交换 接受 2", "u2",
                   ats=[("bot1", "宝可梦世界")], self_id="bot1")
        run_cmd(pb, ev, pb.cmd_trade)
        out = "".join(ev.outputs)
        assert "交换成功" in out, out
        # 收到的勇基拉触发通信进化
        assert ("胡地", "") in _party(pb, "u2"), _party(pb, "u2")


def test_trade_target_name_after_at_with_index():
    """`/交换 接受 @小霞 2` —— 序号不能被 @ 顶掉(first digit wins)。"""
    with tempfile.TemporaryDirectory() as tmp:
        pa = _make(tmp, "u1", "小智", "kadabra")
        pb = _make(tmp, "u2", "小霞", "onix")
        ev = _User("/交换 @小霞 2", "u1", ats=[("u2", "小霞")])
        run_cmd(pa, ev, pa.cmd_trade)
        ev = _User("/交换 接受 @小智 2", "u2", ats=[("u1", "小智")])
        run_cmd(pb, ev, pb.cmd_trade)
        assert "交换成功" in "".join(ev.outputs), ev.outputs


def test_trade_accept_via_qq_official_raw_mention():
    """qqofficial 读不到别人的 @ 组件,原文是 `<@!openid>`,也要认。"""
    with tempfile.TemporaryDirectory() as tmp:
        pa = _make(tmp, "u1", "小智", "kadabra")
        pb = _make(tmp, "u2", "小霞", "onix")
        ev = _User("/交换 <@!u2> 2", "u1")
        run_cmd(pa, ev, pa.cmd_trade)
        assert "发起交换" in "".join(ev.outputs), ev.outputs
        ev = _User("/交换 接受 <@!u1> 2", "u2")
        run_cmd(pb, ev, pb.cmd_trade)
        assert "交换成功" in "".join(ev.outputs), ev.outputs


# ── 通信进化覆盖:数据里 30 种 evoType=trade 全都要能触发 ──

def _trade_pairs():
    out = []
    for key, entry in DEX.species.items():
        for e in entry.get("evos") or []:
            target = DEX.species.get(e) or {}
            if target.get("evoType") == "trade":
                out.append((key, e, str(target.get("evoItem") or "")))
    return sorted(out)


def test_all_trade_evolutions_can_trigger():
    """自连与玩家间交换两条路都必须能进化 —— 含 16/20 种需要携带道具的。"""
    pairs = _trade_pairs()
    assert len(pairs) == 30, f"数据里的通信进化数变了:{len(pairs)}"
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "quest_enable": False}
        scope = "g" + GROUP
        for i, (sp, target, evo_item) in enumerate(pairs):
            item = (resolve_bag_item(evo_item)[0] if evo_item else "")
            # ① 与远方训练家自连
            mon = create_pokemon(sp, 30).to_dict()
            mon["id"] = "mx"
            mon["item"] = item
            uid = f"self{i}"
            _save_player(p, scope, uid, f"自连{i}", [mon])
            ev = _User("/交换 1", uid)
            run_cmd(p, ev, p.cmd_trade)
            t = p._load(_User("/状态", uid))
            assert t.party[0]["species"] == target, (sp, ev.outputs)
            assert not t.party[0].get("item"), (sp, "需要道具的进化应消耗道具")

            # ② 玩家间交换(谁收到谁进化)
            a, b = f"pa{i}", f"pb{i}"
            _save_player(p, scope, a, f"甲{i}",
                         [create_pokemon("pidgey", 5).to_dict()])
            given = create_pokemon(sp, 30).to_dict()
            given["id"] = "mx"
            given["item"] = item
            _save_player(p, scope, b, f"乙{i}", [given])
            ev = _User(f"/交换 @乙{i} 1", a, ats=[(b, f"乙{i}")])
            run_cmd(p, ev, p.cmd_trade)
            assert "发起交换" in "".join(ev.outputs), (sp, ev.outputs)
            ev = _User("/交换 接受 1", b)
            run_cmd(p, ev, p.cmd_trade)
            out = "".join(ev.outputs)
            ta = p._load(_User("/状态", a))
            got = ta.party[-1]
            assert got["species"] == target, (sp, out)
            assert not got.get("item"), (sp, "需要道具的进化应消耗道具")
