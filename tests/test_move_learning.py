"""升级学招的**归属**与**信息完整度**。

起因:一只杰尼龟 + 一只波波的队伍打赢之后,成长信息看起来像"波波 学会了
缩入壳中"。排查后确认数据没错(杰尼龟的 pending 才是 withdraw),问题在显示:

1. 「└ 学会了「X」」这类续行**不带宝可梦名字**,一旦换行落到下一条目附近就会
   被读成别人学的;
2. 结算卡把成长条目用 `" · "` 拼成一整段再折行,换行位置正好落在条目中间;
3. 待替换的招式列表只有名字,看不出"这招做什么"(用户要求优化)。

这里把归属与"招式说明"两件事都锁住。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402


def _pending_footer(t):
    """模块级小工具(与 main.py 里同名,便于测试直调)。"""
    import pw_plugin.main as M
    from test_commands import _MOD  # noqa: F401

    return M._pending_footer(t)


def _trainer_with_party(tmp):
    """杰尼龟(4 招) + 波波(4 招),两只都停在"差一点升级"。"""
    from pw.dex import get_dex
    from pw.engine import create_pokemon

    dex = get_dex()
    p = _Cmd(tmp)
    p.config = {"ui_image": False, "battle_image": False, "quest_enable": False}
    run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
    t = p._load(_Event(""))
    t.data["region"] = "kanto"
    pidgey = create_pokemon("pidgey", 9).to_dict()
    pidgey["id"] = "m2"
    t.data["party"].append(pidgey)
    # 都塞满 4 招 → 升级学到的新招会进 pending
    t.data["party"][0]["moves"] = ["watergun", "tailwhip", "tackle", "bite"]
    t.data["party"][1]["moves"] = ["twister", "aerialace", "tackle", "sandattack"]
    for m in t.data["party"]:
        m["exp"] = dex.exp_for_level(dex.growth_of(m["species"]), m["level"] + 1) - 2
    p._save(t)
    return p


def _win_battle(p):
    """打一场能升级的胜利,返回 (文本输出, turn_result)。"""
    from pw import battle as B
    from pw.util import game_day

    t = p._load(_Event(""))
    B.start(t, [{"species": "caterpie", "level": 6}], kind="trainer",
            meta={"kind": "trainer", "title": "测试战"}, day=game_day())
    p._save(t)
    ev = None
    for _ in range(25):
        ev = _Event("/对战 move 1")
        run_cmd(p, ev, p.cmd_battle)
        if not B.in_battle(p._load(ev)):
            break
    return "".join(str(x) for x in ev.outputs)


def test_pending_moves_do_not_leak_across_party():
    """两只都学新招,各归各的 —— 不能把杰尼龟的招记到波波头上。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _trainer_with_party(tmp)
        _win_battle(p)
        t = p._load(_Event(""))
        squirtle, pidgey = t.data["party"][0], t.data["party"][1]
        assert squirtle["species"] == "squirtle"
        assert pidgey["species"] == "pidgey"
        # 各自的待定招式只能是**自己**能学的
        from pw.dex import get_dex

        dex = get_dex()
        for mon in (squirtle, pidgey):
            learn = dex.level_up_moves(mon["species"], 1, 100)
            for mv in mon.get("pending") or []:
                assert mv in learn, f"{mon['species']} 的待定招式 {mv} 不属于它"
        assert "withdraw" not in (pidgey.get("pending") or []), "波波不该有待定缩入壳中"
        assert "withdraw" not in pidgey["moves"], "波波不该学会缩入壳中"


def test_growth_lines_always_carry_the_owner_name():
    """「学会了 / 想学」的续行必须自带宝可梦名字,否则会读成别人的。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _trainer_with_party(tmp)
        out = _win_battle(p)
        from pw.growth import move_zh

        names = ["杰尼龟", "波波", "卡咪龟"]
        for line in out.split("\n"):
            if "学会了" in line or "想学" in line:
                assert any(n in line for n in names), f"这行没写清是谁学的:{line}"
                assert move_zh("withdraw") in line or "「" in line, line


def test_pending_lists_show_move_effect():
    """待替换招式要能看出效果(用户:"带替换招式 中没有技能说明")。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _trainer_with_party(tmp)
        out = _win_battle(p)
        # 结算文本里的"想学"行要带一行效果
        want = [ln for ln in out.split("\n") if "想学" in ln]
        assert want, out[-400:]
        assert any("[" in ln or "——" in ln for ln in want), want

        # `/学招` 列出可替换招式时,每个候选都带效果
        t = p._load(_Event(""))
        t.data["party"][0]["pending"] = ["withdraw"]
        t.data["party"][0]["level"] = 8
        p._save(t)
        ev = _Event("/学招 1")
        run_cmd(p, ev, p.cmd_learn)
        shown = "".join(str(x) for x in ev.outputs)
        assert "缩入壳中 [水/变化]" in shown, shown
        assert "—— " in shown, f"待替换/现有招式没有效果说明:{shown}"
        assert "水枪 [水/特殊]" in shown and "——" in shown, shown

        # 替换成功后,待定列表清空
        ev2 = _Event("/学招 1 替换 2")
        run_cmd(p, ev2, p.cmd_learn)
        assert "学会了" in "".join(str(x) for x in ev2.outputs)
        after = p._load(ev2).data["party"][0]
        assert "withdraw" in after["moves"]
        assert not (after.get("pending") or []), after.get("pending")
        # 决定过就不能再改:再发一次 `/学招` 只会说"没有要决定的招式"
        ev3 = _Event("/学招 1 替换 1")
        run_cmd(p, ev3, p.cmd_learn)
        assert "没有要决定的招式" in "".join(str(x) for x in ev3.outputs)
        assert p._load(ev3).data["party"][0]["moves"] == after["moves"], "决定后不该再变"


def test_mon_panel_reports_pending_move():
    """`/宝可梦` 要提示"有招没学",并带上效果与怎么学。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _trainer_with_party(tmp)
        t = p._load(_Event(""))
        t.data["party"][0]["level"] = 8
        t.data["party"][0]["pending"] = ["withdraw"]
        p._save(t)
        ev = _Event("/宝可梦 1")
        run_cmd(p, ev, p.cmd_mon)
        out = "".join(str(x) for x in ev.outputs)
        assert "待学" in out and "缩入壳中" in out, out
        assert "[水/变化]" in out, f"没写属性分类:{out}"
        assert "/学招" in out, f"没写怎么学:{out}"


def test_screen_wrap_honors_newlines():
    """`Screen.wrap` 必须把 \\n 当强制换行(否则多条目会被连成一段)。"""
    from pw import ui_render as UI

    sc = UI.Screen(scale=1)
    lines = sc.wrap("第一条\n第二条\n第三条", 400, size=8, limit=6)
    assert lines == ["第一条", "第二条", "第三条"], lines
    # 宽度折行仍然生效
    assert len(sc.wrap("一二三四五六七八九十", 20, size=8, limit=9)) > 1


def test_result_card_does_not_merge_growth_entries():
    """结算卡里每条成长信息必须独立成行(不能拼成一段后再折行)。"""
    from pw import ui_info as I
    from pw import ui_render as UI
    from pw.growth import move_brief

    growth = [
        "杰尼龟: +24 EXP → Lv6",
        f"　└ 杰尼龟 想学「{move_brief('withdraw')}」(招式已满)",
        "波波: +24 EXP → Lv10",
    ]
    drawn: list[str] = []
    orig = UI.Screen.text

    def spy(self, x, y, s, **kw):
        drawn.append(str(s))
        return orig(self, x, y, s, **kw)

    UI.Screen.text = spy
    try:
        data = I.render_battle_result(outcome="win", title="测试战", lines=["赢了!"],
                                      rewards=["+24 EXP"], growth=growth,
                                      mon={"species": "squirtle", "level": 6}, scale=1)
    finally:
        UI.Screen.text = orig
    assert data
    # 没有任何一行同时出现两只宝可梦的名字(说明条目没被拼在一起)
    for line in drawn:
        assert not ("杰尼龟" in line and "波波" in line), f"条目被拼成一行:{line}"


def _growth_card_after_victory(tmp, *, boost: str):
    """打一场胜利,**只让指定的一只**升级,返回成长卡真正拿到的参数。

    `boost` 是 species key(该只的经验推到差 2 点升级)。
    """

    from pw import battle as B
    from pw.dex import get_dex
    from pw.engine import create_pokemon
    from pw.util import game_day

    dex = get_dex()
    p = _Cmd(tmp)
    p.config = {"ui_image": True, "battle_image": False,
                "battle_image_scale": 2, "quest_enable": False}
    run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
    t = p._load(_Event(""))
    t.data["region"] = "kanto"
    pidgey = create_pokemon("pidgey", 9).to_dict()
    pidgey["id"] = "m2"
    t.data["party"].append(pidgey)
    t.data["party"][0]["moves"] = ["watergun", "tailwhip", "tackle", "bite"]
    t.data["party"][1]["moves"] = ["twister", "aerialace", "tackle", "sandattack"]
    for m in t.data["party"]:
        # 只有目标那只有足够经验升级;另一只留在原地
        m["exp"] = 0
    target = t.data["party"][0 if boost == "squirtle" else 1]
    target["exp"] = dex.exp_for_level(dex.growth_of(boost), target["level"] + 1) - 2
    p._save(t)

    B.start(t, [{"species": "caterpie", "level": 6}], kind="trainer",
            meta={"kind": "trainer", "title": "测试战"}, day=game_day())
    p._save(t)
    cap: dict = {}
    mod = sys.modules["pw_plugin.pw.ui_info"]
    orig = mod.render_growth

    def spy(mon, **kw):
        cap["mon"] = mon
        cap.update(kw)
        return orig(mon, **kw)

    mod.render_growth = spy
    try:
        for _ in range(25):
            ev = _Event("/对战 move 1")
            run_cmd(p, ev, p.cmd_battle)
            if not B.in_battle(p._load(ev)):
                break
    finally:
        mod.render_growth = orig
    return p, cap


def test_growth_card_belongs_to_the_mon_that_leveled_up():
    """战斗胜利升级后,成长卡只能讲**升级的那一只**。

    这是用户报的原始现象:杰尼龟(队伍第 1 只)的"想学「缩入壳中」"
    出现在了波波的成长卡上 —— 因为旧实现把 `res.growth` 里**全队**的文字行
    一起刮出来喂给成长卡,而卡片主角取的是"当前出战的那只"。
    """
    with tempfile.TemporaryDirectory() as tmp:
        _p, cap = _growth_card_after_victory(tmp, boost="squirtle")
        assert cap, "没出成长卡"
        assert cap["mon"].get("name") == "杰尼龟", cap["mon"].get("name")
        assert cap["pending"] == ["withdraw"], cap["pending"]
        assert cap["before_level"] == 5 and cap["after_level"] == 6

    # 换一只升级:卡片主角换成波波,而且**不能**带上杰尼龟的待学招式
    with tempfile.TemporaryDirectory() as tmp:
        _p, cap = _growth_card_after_victory(tmp, boost="pidgey")
        assert cap, "没出成长卡"
        assert cap["mon"].get("name") == "波波", cap["mon"].get("name")
        assert "withdraw" not in (cap["pending"] or []), (
            f"波波的成长卡里出现了杰尼龟的招式:{cap['pending']}"
        )
        for mv in (cap["pending"] or []) + (cap["learned"] or []):
            assert mv in ("featherdance", "twister", "wingattack", "quickattack",
                          "gust", "sandattack", "hurricane"), mv


def test_candy_levelup_lines_also_carry_name_and_effect():
    """用神奇糖果升级的提示同样要写清"谁学的、什么效果"。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "quest_enable": False}
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        t = p._load(_Event(""))
        t.data["party"][0]["moves"] = ["watergun", "tailwhip", "tackle", "bite"]
        t.data["party"][0]["exp"] = 0
        t.add_item("rare-candy", 2)
        p._save(t)
        ev = _Event("/使用 神奇糖果 1")
        run_cmd(p, ev, p.cmd_use)
        out = "".join(str(x) for x in ev.outputs)
        assert ("杰尼龟" in out and "升级" not in out) or "升到了" in out, out
        for line in out.split("\n"):
            if "想学" in line or "学会了" in line:
                assert "杰尼龟" in line, f"没写清是谁学的:{line}"
                assert "[" in line and "]" in line, f"没写属性分类:{line}"


# ══════════════════════════════════════════════════════════════════
# 成长卡:只涨经验不出卡,升级时主角要对
# ══════════════════════════════════════════════════════════════════
def _settle(tmp, *, near=None, second_near=None):
    """打一场"训练家战胜利"并返回 (res, trainer)。

    near:两只都停在"差 near 点经验升级";second_near:只让第 2 只接近升级。
    """
    from pw import battle as B
    from pw.dex import get_dex
    from pw.engine import Battle, Side, create_pokemon
    from pw.player import new_trainer
    from pw.util import game_day

    dex = get_dex()
    day = game_day()
    t = new_trainer("u1", "g1", "小智", starter="杰尼龟", day=day)
    mon = create_pokemon("pidgey", 5).to_dict()
    mon["id"] = "m2"
    t.data["party"].append(mon)
    for i, m in enumerate(t.data["party"]):
        n = second_near if (second_near is not None and i == 1) else (near or 90)
        m["exp"] = dex.exp_for_level(dex.growth_of(m["species"]), m["level"] + 1) - n
    b = Battle(player=Side.from_dict({}), enemy=Side.from_dict({}))
    b.player.party = [B.dict_to_mon(x) for x in t.data["party"]]
    b.enemy.party = [create_pokemon("caterpie", 3)]
    res = B.TurnResult(outcome="win")
    B._finish_win(t, b, {"kind": "trainer"}, res, daytime="day",
                  money_mult=1.0, day=day)
    return res, t


def test_no_growth_card_when_nobody_levels_up():
    """只涨经验(没人升级)不能出成长卡 —— 否则会画出"Lv5 → Lv5"这种荒唐画面。

    实测反馈:杰尼龟打赢、波波也涨了经验,卡片写"lv5 → lv5"。
    根因:旧代码只要 `res.growth` 非空就出卡,这时没有升级明细可选,
    等级两处都退化成当前等级。
    """
    with tempfile.TemporaryDirectory() as tmp:
        res, _t = _settle(tmp, near=90)          # 两只都不升级
        assert res.growth, "应该还有经验文本(给结果卡用)"
        assert all(not d["levels"] for d in res.growth_detail), res.growth_detail
        leveled = next((d for d in res.growth_detail if d.get("levels")), None)
        assert leveled is None, "没人升级就不该有升级明细(调用方据此不出卡)"


def test_growth_card_belongs_to_the_leveled_mon():
    """成长卡主角 = **真的升级了的那一只**,等级跳变必须正确。"""
    with tempfile.TemporaryDirectory() as tmp:
        res, _t = _settle(tmp, near=None, second_near=3)   # 只有波波升级
        leveled = next((d for d in res.growth_detail if d.get("levels")), None)
        assert leveled, res.growth_detail
        assert leveled["name"] == "波波", leveled
        assert (leveled["from_level"], leveled["to_level"]) == (5, 6), leveled

        # 都升级时:第一个升级的(杰尼龟)当主角
        res2, _t2 = _settle(tmp, near=3)
        first = next((d for d in res2.growth_detail if d.get("levels")), None)
        assert first and first["name"] == "杰尼龟", res2.growth_detail
        assert (first["from_level"], first["to_level"]) == (5, 6)

        # 另一只的升级也要在明细里(各自独立,不互相覆盖)
        names = {d["name"]: d["to_level"] for d in res2.growth_detail}
        assert names.get("波波") == 6, names


def test_growth_renderer_never_shows_equal_level_arrow():
    """兜底:等级没变时渲染层也不许画 "Lv5 → 5"。"""
    from pw import ui_info as I
    from pw import ui_render as UI

    seen: list[str] = []
    orig = UI.Screen.text
    try:
        UI.Screen.text = lambda self, x, y, s, **kw: (
            seen.append(str(s)), orig(self, x, y, s, **kw))[1]
        data = I.render_growth({"species": "pidgey", "name": "波波", "level": 5,
                                "exp_pct": 30.0},
                               before_level=5, after_level=5, scale=1)
    finally:
        UI.Screen.text = orig
    assert data
    assert not any(s == "→" for s in seen), seen
    assert "Lv5" in " ".join(seen)


def test_all_pending_moves_are_announced_after_a_battle():
    """多只同时升级、各自都有新招式时,必须**逐个**列出来。

    成长卡一次只画一只,别的宝可梦的新招式容易被忽略(用户反馈)。
    """
    with tempfile.TemporaryDirectory() as tmp:
        p = _trainer_with_party(tmp)
        t = p._load(_Event(""))
        # 队伍里的每一只都各有一条待决定招式
        for md, mv in zip(t.data["party"], ("withdraw", "featherdance"), strict=False):
            md["pending"] = [mv]
        p._save(t)
        notice = p._pending_notice(p._load(_Event("")))
        pairs = (("杰尼龟", "缩入壳中"), ("波波", "羽毛舞"))
        for i, (name, mv) in enumerate(pairs, 1):
            assert name in notice, f"{name} 没被列出来:{notice}"
            assert mv in notice, f"{mv} 没被列出来:{notice}"
            assert f"/学招 {i}" in notice, f"没给第 {i} 只的用法:{notice}"
        assert notice.count("· ") >= len(pairs), notice
        # 底注要能提醒(图里也能看到,不只靠文字)
        foot = _pending_footer(t)
        assert str(len(pairs)) in foot and "学招" in foot, foot
        # 跳过某一只(单独播报过)时不再重复
        skip = p._pending_notice(t, skip=2)
        assert "波波" not in skip and "杰尼龟" in skip, skip


def test_pending_footer_is_drawn_on_the_result_card(monkeypatch):
    """结果卡的底注要真的画出来(有待决定招式时提示 /学招)。"""
    from pw import ui_info as I
    from pw import ui_render as UI

    drawn: list[str] = []
    orig = UI.Screen.text

    def spy(self, x, y, s, **kw):
        drawn.append(str(s))
        return orig(self, x, y, s, **kw)

    monkeypatch.setattr(UI.Screen, "text", spy)
    data = I.render_battle_result(
        outcome="win", title="测试战", lines=["赢了!"], rewards=["+24 EXP"],
        growth=["杰尼龟: +24 EXP → Lv6"], footer="◆ 有 2 个新招式待决定,用 /学招 <队伍序号>",
        mon={"species": "squirtle", "level": 6}, scale=1,
    )
    assert data
    assert any("待决定" in x for x in drawn), drawn[-8:]


# ══════════════════════════════════════════════════════════════════
# 进化必须有画面:每只升级/进化的各出一张成长卡
# ══════════════════════════════════════════════════════════════════
def _spy_growth(p):
    """拦截 render_growth,记下每张卡的参数(仍照常渲染,顺便覆盖渲染层)。"""
    import pw_plugin.pw.ui_info as UII

    calls: list[dict] = []
    orig = UII.render_growth

    def spy(view, **kw):
        calls.append(kw)
        return orig(view, **kw)

    UII.render_growth = spy
    return calls, lambda: setattr(UII, "render_growth", orig)


def _battle_to_win(tmp, p, extra_party=(), *, starter="杰尼龟"):
    """打一场真实指令流的野生对战并打到胜利(敌人换成 2 级鲤鱼王,稳赢)。

    返回 (存档, 最后一回合发出的全部内容) —— 奖励行有没有发出去也要能断言。
    """
    from pw import battle as B
    from pw.dex import get_dex
    from pw.util import game_day

    dex = get_dex()
    day = game_day()
    run_cmd(p, _Event(f"/开始 小智 {starter}"), p.cmd_start)
    t = p._load(_Event())
    # 出战的那只也压到"差 1 点升级",才能制造"多只同时升级、只有后一只进化"
    sq = t.data["party"][0]
    sq["exp"] = dex.exp_for_level(dex.growth_of(sq["species"]), sq["level"] + 1) - 1
    for sp, lv in extra_party:
        md = B.create_pokemon(sp, lv).to_dict()
        md["id"] = f"m{len(t.data['party'])}"
        md["exp"] = dex.exp_for_level(dex.growth_of(sp), lv + 1) - 1
        t.data["party"].append(md)
    B.start(t, [{"species": "magikarp", "level": 2, "shiny": False}],
            kind="wild", wild=True, meta={"title": "野生的鲤鱼王"}, day=day)
    p._save(t)
    out: list[str] = []
    for _ in range(10):
        t = p._load(_Event())
        if not B.in_battle(t):
            break
        ev = _Event("/对战 1")
        run_cmd(p, ev, p.cmd_battle)
        out = ev.outputs
    return p._load(_Event()), "\n".join(out)


def test_every_evolved_mon_gets_its_own_growth_card():
    """多只同时升级时,进化也必须有画面。

    实测反馈:战斗胜利升级,队伍里一只进化了却"没显示相关的图片画面" ——
    旧实现只给**第一只升级的**出卡,进化的那只被一行文字带过
    (杰尼龟升到 Lv6 的卡 + 绿毛虫→铁甲蛹 只出现在文本里)。
    """
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        calls, restore = _spy_growth(p)
        try:
            t, _out = _battle_to_win(tmp, p, extra_party=[("caterpie", 6)])
        finally:
            restore()
        assert [m["species"] for m in t.data["party"]] == ["squirtle", "metapod"]
        evos = [c for c in calls if c.get("evolved_to_zh")]
        assert evos, f"进化没有画面:{calls}"
        assert evos[0]["evolved_to_zh"] == "铁甲蛹"
        assert (evos[0]["before_level"], evos[0]["after_level"]) == (6, 7)
        # 两只都升级 → 两张卡(进化那张排前面)
        assert len(calls) == 2, f"两只升级应各有一张成长卡:{calls}"
        assert calls[0].get("evolved_to_zh"), "进化卡要排在前面(卡片多了也不能被截掉)"


def test_level_up_win_still_reports_rewards():
    """成长卡顶替结果卡时,奖励行(经验)不能一起消失。

    结果卡才有奖励栏,而"有人升级"时发的是成长卡 —— 图片路径又不会发 text,
    于是升级的那一场玩家看不到经验(实测反馈的姊妹问题)。
    """
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": True}
        t, out = _battle_to_win(tmp, p, extra_party=[("caterpie", 6)])
        assert "获得经验" in out, out
        assert t.data["party"][1]["species"] == "metapod"


def test_catch_card_does_not_swallow_the_evolution_card():
    """捕获成功也会结算经验:进化不能被捕获卡吃掉(旧实现直接 return)。"""
    import asyncio

    from pw import battle as B
    from pw.engine import create_pokemon
    from pw.player import mon_to_dict

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": True}
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        t = p._load(_Event())
        # 模拟"打赢的这一球让队伍第一只进化":
        t.data["party"][0] = mon_to_dict(create_pokemon("metapod", 7))
        p._save(t)
        calls, restore = _spy_growth(p)
        ev = _Event("")
        res = B.TurnResult(
            finished=True, outcome="caught", item_key="poke-ball",
            rewards=["🎉 捕获成功!"], caught=mon_to_dict(create_pokemon("magikarp", 4)),
            growth_detail=[{
                "index": 0, "name": "铁甲蛹", "levels": 1,
                "from_level": 6, "to_level": 7, "learned": [], "pending": [],
                "evolved_from": "caterpie", "evolved_to": "metapod",
            }],
        )
        t = p._load(_Event())

        async def go():
            return [r async for r in p._emit_result_cards(ev, t, {}, res)]

        try:
            asyncio.run(go())
        finally:
            restore()
        chains = [o for o in ev.outputs if o.startswith("<chain")]
        assert len(chains) == 2, f"捕获卡 + 进化卡都要发:{ev.outputs}"
        assert any(c.get("evolved_to_zh") == "铁甲蛹" for c in calls), calls


def test_growth_cards_are_capped_and_evolution_wins_a_slot():
    """升级的太多时最多发 3 张,但**进化一定占一个名额**,其余走文字。"""
    import asyncio

    from pw import battle as B

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": True}
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        t = p._load(_Event())
        details = [
            {"index": 0, "name": f"测试{i}", "levels": 1, "from_level": 5,
             "to_level": 6, "learned": [], "pending": [], "evolved_from": "",
             "evolved_to": ""}
            for i in range(3)
        ]
        # 第 4 只(排在最后)进化 —— 按顺序截断的话它会被丢掉
        details.append({"index": 0, "name": "绿毛虫", "levels": 1, "from_level": 6,
                        "to_level": 7, "learned": [], "pending": [],
                        "evolved_from": "caterpie", "evolved_to": "metapod"})
        res = B.TurnResult(finished=True, outcome="win", rewards=["+10 EXP"],
                           growth=["…"], growth_detail=details)
        calls, restore = _spy_growth(p)
        ev = _Event("")

        async def go():
            return [r async for r in p._emit_result_cards(ev, t, {}, res)]

        try:
            asyncio.run(go())
        finally:
            restore()
        assert len(calls) == 3, f"最多 3 张成长卡:{len(calls)}"
        assert any(c.get("evolved_to_zh") == "铁甲蛹" for c in calls), \
            f"进化被截掉了:{calls}"
        assert any("其余成长" in o for o in ev.outputs), ev.outputs


def test_rare_candy_evolution_has_a_picture():
    """非战斗路径(神奇糖果)升级进化同样要出画面。"""
    from pw import battle as B

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": True}
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        t = p._load(_Event())
        md = B.create_pokemon("caterpie", 6).to_dict()
        md["id"] = "m2"
        t.data["party"].append(md)
        t.add_item("rare-candy", 1)
        p._save(t)
        calls, restore = _spy_growth(p)
        ev = _Event("/使用 神奇糖果 2")
        try:
            run_cmd(p, ev, p.cmd_use)
        finally:
            restore()
        t = p._load(_Event())
        assert t.data["party"][1]["species"] == "metapod"
        assert [c.get("evolved_to_zh") for c in calls] == ["铁甲蛹"], calls
        assert any(o.startswith("<chain") for o in ev.outputs), ev.outputs
