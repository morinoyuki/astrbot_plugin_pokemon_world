"""玩家会照着抄的指令引用必须真实存在。

界面的底部提示条/附带提示里写着 `/xxx`,玩家会直接照抄。之前出现过三个
"看起来像指令但根本不存在"的写法,照着敲只会得到"未知指令":
  · `/换招`            —— 学招式其实叫 `/学招`
  · `/飞行 <地点>`      —— 飞行是 `/前往 飞行 <城镇>`
  · `/主线 继续`        —— 主线只有 `/主线 挑战`
这里把"footer/hint 里出现的 `/指令`"全部对着注册表校验一遍。
"""

from __future__ import annotations

import ast
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 允许出现的"看似指令"的东西:路径、协议、以及正文里的斜杠词
_ALLOW = {
    "mnt", "usr", "home", "System", "Library", "Windows", "Fonts", "etc",
    "tmp", "var", "opt", "share", "fonts", "static", "http", "https",
    "build", "genus", "RSE", "FRLG", "GBA", "DS", "PW", "pw",
}
# 指令后允许跟的子指令(与 main.py 里的解析分支一致)
_SUBS = {
    "前往": {"飞行", "fly", "飞", "坐飞机"},
    "队伍": {"存入", "取出", "换位", "交换", "放生", "电脑", "deposit", "withdraw",
             "swap", "release", "box"},
    "商店": {"买", "卖", "页", "第", "page", "buy", "sell"},
    "任务": {"放弃", "drop", "取消"},
    "道馆": {"挑战", "challenge", "打", "开战"},
    "联盟": {"挑战", "challenge", "打", "开战"},
    "大赛": {"挑战", "challenge", "打", "开战"},
    "神兽": {"挑战", "challenge", "打", "开战"},
    "主线": {"挑战", "challenge", "打", "开战"},
    "电脑": {"放生", "release"},
    "背包": set(),
}
_PLACEHOLDER = re.compile(r"^[<\[(].*")


def _registered_commands() -> set[str]:
    with open(os.path.join(_ROOT, "main.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "command":
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    names.add(a.value)
            for kw in node.keywords:
                if kw.arg == "alias" and isinstance(kw.value, (ast.Set, ast.List, ast.Tuple)):
                    for e in kw.value.elts:
                        if isinstance(e, ast.Constant) and isinstance(e.value, str):
                            names.add(e.value)
    return names


def _hint_strings() -> list[tuple[str, int, str]]:
    """收集 footer(...) 的实参与 hint=/plain_result(...) 里的字符串。"""
    out: list[tuple[str, int, str]] = []
    files = [os.path.join(_ROOT, "main.py"), os.path.join(_ROOT, "prompts.py")]
    files += [os.path.join(_ROOT, "pw", n) for n in sorted(os.listdir(os.path.join(_ROOT, "pw")))
              if n.startswith("ui_") and n.endswith(".py")]
    for path in files:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = getattr(node.func, "attr", "") or getattr(node.func, "id", "")
            keep: list[ast.expr] = []
            if fn == "footer" or fn == "plain_result":
                keep = list(node.args)
            else:
                keep = [kw.value for kw in node.keywords if kw.arg == "hint"]
            for arg in keep:
                out.extend(
                    (os.path.basename(path), sub.lineno, sub.value)
                    for sub in ast.walk(arg)
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                )
    return out


def test_hints_only_reference_real_commands():
    names = _registered_commands()
    assert len(names) > 50, "没解析到指令注册表,测试本身有问题"
    bad: list[str] = []
    # 只认两种"玩家会照抄"的写法:反引号里的 `/指令 子指令`,以及提示条的 `◆ /指令`。
    # 不这么限定的话,正文里的"出招/捕捉/逃跑"、"胡地/耿鬼/怪力"都会被当成指令。
    pat = re.compile(
        r"(?:`/([A-Za-z\u4e00-\u9fff]{1,8})(?:\s+([^`\s]{1,10}))?)|"
        r"(?:◆\s*/([A-Za-z\u4e00-\u9fff]{1,8})(?:\s+([^`\s]{1,10}))?)"
    )
    for fname, line, text in _hint_strings():
        for m in pat.finditer(text):
            cmd = m.group(1) or m.group(3)
            sub = m.group(2) or m.group(4)
            if cmd in _ALLOW:
                continue
            if cmd not in names:
                bad.append(f"{fname}:L{line} 不存在的指令 /{cmd} ← {text.strip()[:60]}")
                continue
            if not sub or _PLACEHOLDER.match(sub):
                continue
            allowed = _SUBS.get(cmd)
            if allowed is not None and sub not in allowed:
                bad.append(
                    f"{fname}:L{line} /{cmd} 没有子指令「{sub}」← {text.strip()[:60]}"
                )
    assert not bad, "提示里的指令引用有误:\n" + "\n".join(bad)
