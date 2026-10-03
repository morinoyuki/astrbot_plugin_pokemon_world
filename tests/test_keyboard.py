"""消息按钮(QQ 官方 InlineKeyboard)适配测试。

覆盖三件事:
1. `pw/keyboard.py` 只在 QQ 官方平台出按钮,别的平台/旧版 AstrBot 静默退化;
2. 战斗/商店/电脑/背包的按钮内容就是合法指令(点击后能走同一套逻辑);
3. `on_keyboard_click` 能把按钮 data 当指令执行(INTERACTION_CREATE 回调)。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402

import pw_plugin.main as M  # noqa: E402  # isort: skip
from pw import keyboard as KB  # noqa: E402


class _KBResult:
    """带 chain 的结果替身(MessageEventResult 的最小面)。"""

    def __init__(self, chain=None):
        self.chain = list(chain or [])
        self.use_markdown_ = None

    def use_markdown(self, use=True):
        self.use_markdown_ = use
        return self


class _QQEvent(_Event):
    """QQ 官方事件替身:平台名 + 按钮回调接口 + 可挂键盘的结果对象。"""

    def __init__(self, text="", data=""):
        super().__init__(text)
        self.button_data = data
        self.acked = []
        self.results = []

    def get_platform_name(self):
        return "qq_official"

    def is_button_interaction(self):
        return bool(self.button_data)

    def get_interaction_button_data(self):
        return self.button_data

    async def ack_interaction(self, code=0):
        self.acked.append(code)

    def plain_result(self, text):
        self.outputs.append(str(text))
        res = _KBResult()
        self.results.append(res)
        return res

    def chain_result(self, comps):
        texts = "".join(str(getattr(c, "text", "") or "") for c in comps)
        self.outputs.append(f"<chain:{len(comps)}>{texts}")
        res = _KBResult(comps)
        self.results.append(res)
        return res


def _buttons(rows, event=None):
    """把按钮行拍平成 [(label, data), ...]。"""
    kb = KB.keyboard(event or _QQEvent(), rows)
    assert kb is not None, "QQ 官方平台应该能生成键盘"
    return [
        (btn["render_data"]["label"], btn["action"]["data"])
        for btn_row in kb.to_dict()["content"]["rows"]
        for btn in btn_row["buttons"]
    ]


def _has_keyboard(event):
    return any(
        any(type(c).__name__ == "QQCKeyboard" for c in (res.chain or []))
        for res in event.results
    )


def test_only_qq_official_gets_keyboard():
    rows = KB.grid([("出招", "/对战 1")])
    assert KB.keyboard(_QQEvent(), rows) is not None
    assert KB.keyboard(_Event(), rows) is None, "非 QQ 官方平台不能挂 QQ 组件"

    KB.MODE["mode"] = "never"
    try:
        assert KB.keyboard(_QQEvent(), rows) is None
    finally:
        KB.MODE["mode"] = "auto"


def test_attach_appends_and_marks_markdown():
    res = _KBResult()
    rows = KB.grid([("出招", "/对战 1"), ("换人", "/对战 switch 2")])
    out = KB.attach(_QQEvent(), res, rows)
    assert out is res
    assert len(res.chain) == 1
    assert res.use_markdown_ is True
    # 非 QQ 平台:原样返回,不往消息里塞组件
    res2 = _KBResult()
    assert KB.attach(_Event(), res2, rows) is res2
    assert res2.chain == []


def test_label_and_shape_limits():
    rows = KB.grid([(f"按钮{i}", f"/对战 {i}") for i in range(30)], per_row=7)
    kb = KB.keyboard(_QQEvent(), rows)
    rows_out = kb.to_dict()["content"]["rows"]
    assert len(rows_out) <= KB.MAX_ROWS
    assert all(len(r["buttons"]) <= KB.MAX_PER_ROW for r in rows_out)
    btn = KB.button("一二三四五六七八九十十一", "/对战 1")
    assert len(btn.to_dict()["render_data"]["label"]) <= KB.MAX_LABEL


def _start_battle(tmp):
    p = _Cmd(tmp)
    p.config = {"ui_image": True, "battle_image": True,
                "battle_image_scale": 2, "quest_enable": False}
    run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
    t = p._load(_Event(""))
    t.data["location"] = "kanto-route-2"      # 这条路上有训练家
    p._save(t)
    run_cmd(p, _Event("/训练家战 1"), p.cmd_npc)
    return p, p._load(_Event(""))


def test_battle_keys_are_valid_commands():
    with tempfile.TemporaryDirectory() as tmp:
        p, t = _start_battle(tmp)
        cells = _buttons(p._battle_keys(t, switch_only=True))
        assert cells, "倒下换人时应该给出队伍按钮"
        assert all(data.startswith("/对战 switch ") for _lb, data in cells)

        cells = _buttons(p._battle_keys(t))
        datas = [d for _lb, d in cells]
        assert any(d.startswith("/对战 ") and "switch" not in d for d in datas), (
            f"战斗按钮要有出招:{datas}")
        assert all(len(lb) <= KB.MAX_LABEL for lb, _d in cells)


def test_battle_keyboard_is_attached_to_screen():
    with tempfile.TemporaryDirectory() as tmp:
        p, _t = _start_battle(tmp)
        ev = _QQEvent("/对战 1")
        run_cmd(p, ev, p.cmd_battle)
        joined = "".join(str(x) for x in ev.outputs)
        assert "<chain:" in joined, f"应该有战斗画面:{joined[:200]}"
        assert _has_keyboard(ev), "战斗画面应该带上按钮组件"


def test_shop_and_box_keys_navigate():
    with tempfile.TemporaryDirectory() as tmp:
        host = _Cmd(tmp)
        entries = [{"zh": f"道具{i}", "price": 100 * i} for i in range(1, 10)]
        datas = [d for _lb, d in _buttons(host._shop_keys(entries, 2))]
        assert "/商店 页 1" in datas and "/商店 页 3" in datas
        assert any(d.startswith("/商店 买 道具") for d in datas)

        datas = [d for _lb, d in _buttons(host._box_keys(2, 4))]
        assert "/电脑 上一页" in datas and "/电脑 下一页" in datas
        assert "/电脑 1" in datas and "/电脑 4" in datas


def test_keyboard_click_routes_to_command():
    with tempfile.TemporaryDirectory() as tmp:
        p, _t = _start_battle(tmp)
        before = json.dumps(
            p._load(_Event("")).data.get("battle"), sort_keys=True, default=str
        )
        ev = _QQEvent("", data="/对战 1")
        run_cmd(p, ev, p.on_keyboard_click)
        assert ev.outputs, f"按钮点击应该有回复:{ev.outputs}"
        assert ev.acked == [0], "点击后应 ack 交互"
        after = json.dumps(
            p._load(_Event("")).data.get("battle"), sort_keys=True, default=str
        )
        assert after != before, "按钮点击要真的推进一回合"
        assert any("使用了" in str(x) for x in ev.outputs), (
            f"按钮应该执行了战斗指令:{ev.outputs}")

        # 陈旧/未知按钮:提示重发,不抛异常
        ev2 = _QQEvent("", data="/不存在的指令")
        run_cmd(p, ev2, p.on_keyboard_click)
        assert any("失效" in str(x) for x in ev2.outputs)


def test_missing_starter_gets_pick_buttons():
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        ev = _QQEvent("/开始 小智")
        run_cmd(p, ev, p.cmd_start)
        joined = "".join(str(x) for x in ev.outputs)
        assert "选择你的初始宝可梦" in joined
        assert _has_keyboard(ev), "初始宝可梦菜单应带一键选择按钮"


def test_help_menu_buttons_available():
    p = _Cmd.__new__(_Cmd)          # 不需要存档/数据库
    datas = [d for _lb, d in _buttons(p._help_keys())]
    for want in ("/状态", "/队伍", "/背包", "/地图", "/帮助"):
        assert want in datas, f"帮助按钮缺少 {want}:{datas}"


def test_click_filter_only_passes_interactions():
    from pw_plugin.main import _KeyboardClickFilter

    flt = _KeyboardClickFilter()
    assert flt.filter(_QQEvent("", data="/对战 1"), None) is True
    assert flt.filter(_QQEvent("/对战 1"), None) is False
    assert flt.filter(_Event("/对战 1"), None) is False


def test_keyboard_module_degrades_without_components():
    old = KB.QQCButton, KB.QQCKeyboard
    KB.QQCButton = KB.QQCKeyboard = None
    try:
        assert KB.available() is False
        assert KB.keyboard(_QQEvent(), KB.grid([("a", "/a")])) is None
        res = _KBResult()
        assert KB.attach(_QQEvent(), res, KB.grid([("a", "/a")])) is res
    finally:
        KB.QQCButton, KB.QQCKeyboard = old


def test_main_imports():
    assert M.PokemonWorldPlugin is not None
