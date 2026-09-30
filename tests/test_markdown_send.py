"""文本发送格式:QQ 官方接口默认按 markdown 发送(其他平台不受影响)。"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 必须先导入 test_commands:它负责把插件注册成 `pw_plugin` 包
from test_commands import _Cmd, _Event, run_cmd  # noqa: E402,F401

import pw_plugin.main as M  # noqa: E402  # isort: skip


class _Img:
    """图片替身(有 convert_to_file_path,没有 text)。"""

    def convert_to_file_path(self):
        return "/tmp/x.png"


class _Txt:
    def __init__(self, text):
        self.text = text


class _Result:
    def __init__(self, text):
        self.text = text
        self.use_markdown_ = None

    def use_markdown(self, use=True):
        self.use_markdown_ = use
        return self


class _Ev:
    """最小事件替身:只需要 plain_result / chain_result 与平台名。"""

    """最小事件替身:只需要 plain_result 与平台名。"""

    def __init__(self, platform=""):
        self.platform = platform
        self.out = []

    def plain_result(self, text):
        r = _Result(text)
        self.out.append(r)
        return r

    def chain_result(self, comps):
        r = _Result(comps)
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


def test_image_plus_text_is_one_plain_message_without_markdown_markup():
    """图 + 文本:仍然是**一条**消息,且文案不带 markdown(反引号会被去掉)。"""
    img = _Img()
    txt = _Txt("行动:`/对战 1` 或 **/捕捉**")
    one = _with_mode("auto", lambda: M._cres(_Ev("qq_official"), [img, txt]))
    assert len(one) == 1, one                      # 不拆消息
    assert one[0].use_markdown_ is None, "带图的消息不该用 markdown"
    got = one[0].text
    assert got[0] is img or got[0].text is not None
    caption = next(c for c in got if getattr(c, "text", None) is not None).text
    assert "`" not in caption and "**" not in caption, caption
    assert "/对战 1" in caption and "/捕捉" in caption
    # 纯文本出口不受影响:仍是 markdown(反引号保留,客户端渲染成代码)
    res = _with_mode("auto", lambda: M._res(_Ev("qq_official"), "行动:`/对战 1`"))
    assert res.use_markdown_ is True and "`" in res.text
    # 其他平台:一条、无 markdown(反引号同样清理)
    plain = _with_mode("auto", lambda: M._cres(_Ev("aiocqhttp"), [_Img(), _Txt("`x`")]))
    assert len(plain) == 1 and plain[0].use_markdown_ is None


def test_all_plugin_text_outputs_go_through_the_helper():
    """所有文本出口都必须走 _res —— 否则有的消息会漏掉 markdown 格式。"""
    src = (M.__file__ or "")
    with open(src, encoding="utf-8") as f:
        text = f.read()
    # 唯一允许出现裸调用的地方是 _res 自己(它就是包装器)
    assert text.count("event.plain_result(") == 1, "除了 _res 内部不该再有裸的 plain_result"
    assert text.count("event.chain_result(") == 1, "除了 _cres 内部不该再有裸的 chain_result"
    assert text.count("_res(event, ") > 200, "文本出口没有全部走 _res"
    assert text.count("_cres(event") >= 3, "带图消息没有全部走 _cres"
