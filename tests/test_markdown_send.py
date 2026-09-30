"""文本发送格式:QQ 官方接口默认按 markdown 发送(其他平台不受影响)。"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pw_plugin.main as M  # noqa: E402
from test_commands import _Cmd, _Event, run_cmd  # noqa: E402,F401  注册 pw_plugin 包


class _Result:
    def __init__(self, text):
        self.text = text
        self.use_markdown_ = None

    def use_markdown(self, use=True):
        self.use_markdown_ = use
        return self


class _Ev:
    """最小事件替身:只需要 plain_result 与平台名。"""

    def __init__(self, platform=""):
        self.platform = platform
        self.out = []

    def plain_result(self, text):
        r = _Result(text)
        self.out.append(r)
        return r

    def get_platform_name(self):
        if self.platform is None:
            raise RuntimeError("这个平台拿不到名字")
        return self.platform


def _with_mode(mode, fn):
    old = M._SEND_MD.get("mode")
    M._SEND_MD["mode"] = mode
    try:
        return fn()
    finally:
        M._SEND_MD["mode"] = old


def _send(platform, mode="auto", text="x"):
    return _with_mode(mode, lambda: M._res(_Ev(platform), text))


def test_qqofficial_uses_markdown_by_default():
    for plat in ("qq_official", "QQ_Official", "qqofficial"):
        res = _send(plat, text="**粗体** 文本")
        assert res.use_markdown_ is True, (plat, res.use_markdown_)


def test_other_platforms_stay_plain_text():
    for plat in ("aiocqhttp", "telegram", "discord", "lark", "wechatpadpro"):
        res = _send(plat, text="普通文本")
        assert res.use_markdown_ is None, (plat, res.use_markdown_)


def test_never_and_always_override_the_platform():
    assert _send("qq_official", "never").use_markdown_ is None
    assert _send("aiocqhttp", "always").use_markdown_ is True


def test_platform_without_name_never_breaks_sending():
    res = _send(None)       # get_platform_name() 抛异常 → 退回纯文本
    assert res.use_markdown_ is None and res.text == "x"


def test_all_plugin_text_outputs_go_through_the_helper():
    """所有文本出口都必须走 _res —— 否则有的消息会漏掉 markdown 格式。"""
    src = (M.__file__ or "")
    with open(src, encoding="utf-8") as f:
        text = f.read()
    # 唯一允许出现裸调用的地方是 _res 自己(它就是包装器)
    assert text.count("event.plain_result(") == 1, "除了 _res 内部不该再有裸的 plain_result"
    assert text.count("_res(event, ") > 200, "文本出口没有全部走 _res"
