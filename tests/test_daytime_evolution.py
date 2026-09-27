"""昼夜进化:判定语义、所有入口都传昼夜、以及 `/今日` 要能看见昼夜。

起因:游戏里白天/黑夜只体现在进化条件上,却**没有任何地方告诉玩家现在是白天
还是夜晚**;而且 `/进化` 这条路漏传了 `daytime` —— 太阳伊布/月亮伊布在
`/进化` 里永远显示"条件未满足"(只有升级/糖果能触发)。
"""

from __future__ import annotations

import ast
import sys
import tempfile
from datetime import datetime
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402


def test_daytime_boundary_is_system_clock():
    """昼夜按**系统本地时间**判定:06:00 天亮、18:00 天黑。"""
    from pw.battle import daytime_of

    cases = {0: "night", 5: "night", 5 + 59 / 60: "night", 6: "day",
             12: "day", 17: "day", 17 + 59 / 60: "day", 18: "night", 23: "night"}
    for h, want in cases.items():
        hour = int(h)
        minute = round((h - hour) * 60)
        got = daytime_of(datetime(2026, 1, 1, hour, minute))
        assert got == want, f"{hour}:{minute:02d} 应为 {want},实际 {got}"


def test_evolution_options_respect_day_and_night():
    from pw.dex import get_dex

    dex = get_dex()

    def met(daytime):
        return {
            o["target"]: bool(o.get("met"))
            for o in dex.evolution_options("eevee", level=40, friendship=220,
                                           daytime=daytime)
        }

    day, night = met("day"), met("night")
    assert day.get("espeon") and not day.get("umbreon"), day
    assert night.get("umbreon") and not night.get("espeon"), night
    # 昼夜未知时**不能**算条件成立(否则白天黑夜都会进化错)
    unknown = met(None)
    assert not unknown.get("espeon") and not unknown.get("umbreon"), unknown


def _eevee_trainer(tmp):
    from pw.engine import create_pokemon

    p = _Cmd(tmp)
    p.config = {"ui_image": False, "battle_image": False, "quest_enable": False}
    run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
    t = p._load(_Event(""))
    mon = create_pokemon("eevee", 40).to_dict()
    mon["id"] = "m2"
    mon["friendship"] = 220
    t.data["party"].append(mon)
    p._save(t)
    return p


def _patch_daytime(monkeypatch, when):
    """打补丁要打在**插件加载的那份模块**上(tests 以 pw_plugin 载入,两份是不同的模块对象)。"""
    import pw_plugin.pw.battle as PB

    monkeypatch.setattr(PB, "daytime_of", lambda now=None: when, raising=False)


def test_evolve_command_respects_daytime(monkeypatch):
    """`/进化` 必须按当前昼夜选分支(以前漏传 daytime,两个分支都进化不了)。"""
    for when, want in (("day", "espeon"), ("night", "umbreon")):
        with tempfile.TemporaryDirectory() as tmp:
            p = _eevee_trainer(tmp)
            _patch_daytime(monkeypatch, when)
            ev = _Event("/进化 2")
            run_cmd(p, ev, p.cmd_evolve)
            got = p._load(ev).party[1]["species"]
            assert got == want, f"{when} 应进化成 {want},实际 {got}:{ev.outputs}"


def test_evolve_list_and_hint_respect_daytime(monkeypatch):
    """`/进化` 一览与"可进化提示"也不能按写死的白天判定。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _eevee_trainer(tmp)
        _patch_daytime(monkeypatch, "night")
        ev = _Event("/进化")
        run_cmd(p, ev, p.cmd_evolve)
        out = "".join(str(x) for x in ev.outputs)
        assert "月亮伊布" in out, out
        # 夜里不该把太阳伊布说成"条件已满足"
        for line in out.split("\n"):
            if "太阳伊布" in line and ("✅" in line or "可以" in line):
                raise AssertionError(f"夜里误报太阳伊布可进化:{line}")


def test_level_up_evolution_uses_daytime(monkeypatch):
    """升级路径(战斗/糖果)同样按昼夜分叉。"""
    from pw import growth
    from pw.engine import create_pokemon

    for when, want in (("day", "espeon"), ("night", "umbreon")):
        mon = create_pokemon("eevee", 39)
        mon.friendship = 230
        res = growth.gain_exp(mon, 100000, daytime=when)
        assert mon.species == want, f"{when} 升级应进化成 {want},实际 {mon.species}"
        assert res.evolved_to == want


def test_hold_item_night_evolution(monkeypatch):
    """携带道具 + 夜晚的进化(狃拉 → 玛狃拉 需要锐利之爪且在夜里)。"""
    from pw.dex import get_dex

    dex = get_dex()
    day = dex.evolution_options("sneasel", level=60, item="Razor Claw", daytime="day")
    night = dex.evolution_options("sneasel", level=60, item="Razor Claw",
                                  daytime="night")
    assert not [o for o in day if o["target"] == "weavile" and o["met"]], day
    assert [o for o in night if o["target"] == "weavile" and o["met"]], night


def test_every_evolution_options_call_passes_daytime():
    """静态检查:main.py 里每个 `evolution_options(...)` 都必须传 `daytime=`。

    漏传的后果是**静默**的:条件永远判不成立、玩家只看到"暂时无法进化",
    没有任何报错 —— 所以用静态检查兜住。
    """
    src = (Path(_ROOT) / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "attr", None) or getattr(fn, "id", None)
        if name != "evolution_options":
            continue
        if "daytime" not in {k.arg for k in node.keywords}:
            bad.append(node.lineno)
    assert not bad, f"这些 evolution_options 调用没传 daytime(进化条件会静默失效):{bad}"


def test_today_reports_day_or_night(monkeypatch):
    """`/今日` 要写明现在白天还是夜晚(玩家没法从别处知道)。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "battle_image": False, "quest_enable": False}
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        for when, want in (("day", "白天"), ("night", "夜晚")):
            _patch_daytime(monkeypatch, when)
            ev = _Event("/今日")
            run_cmd(p, ev, p.cmd_today)
            out = "".join(str(x) for x in ev.outputs)
            assert want in out, f"{when} 的 /今日 没写「{want}」:{out[:200]}"


def test_news_image_shows_daytime_chip():
    from pw import ui_info as I
    from pw import ui_render as UI

    seen: list[str] = []
    orig = UI.Screen.text
    try:
        UI.Screen.text = lambda self, x, y, s, **kw: (
            seen.append(str(s)), orig(self, x, y, s, **kw))[1]
        data = I.render_news(3, region_zh="关都", location_zh="真新镇",
                             weather_zh="晴天", daytime_zh="夜晚", scale=1)
    finally:
        UI.Screen.text = orig
    assert data
    assert any("夜晚" in s for s in seen), seen[:10]
