"""页面前端与 bridge 的适配层(bridge.js)—— 用 node 真跑一遍逻辑。

为什么单独测这个:插件页面最容易"看着正常、其实全挂" —— 症状是**总览空白、
保存无声失败**。两个真因:

  ① bridge 对 endpoint 的拼接方式随 AstrBot 版本不同(官方示例 `apiGet("ping")`
     对应后端 `/{插件名}/ping`;别的版本会再补 `page/` 前缀)。只写一种写法,
     换个版本整页失效 —— 实测就是"总览无数据、编辑存不上"。
  ② bridge 常挂在 `window.parent` 而不是 iframe 自己身上。

所以 bridge.js 逐个风格试探 + 记住成功的那种 + 统一三种响应信封;
这里用 node 直接驱动它(纯逻辑,不需要浏览器)。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_PAGE = _ROOT / "pages" / "manage"
_BRIDGE = _PAGE / "bridge.js"

NODE = shutil.which("node")


def _run_node(script: str) -> dict:
    """在 node 里 require bridge.js 并执行脚本,返回它打印的 JSON。"""
    if not NODE:
        pytest.skip("没有 node,跳过前端逻辑测试")
    code = (
        f'const B = require({json.dumps(str(_BRIDGE))});\n'
        "B.resetCache();\n"
        "async function main() {\n" + script + "\n}\n"
        "main().then((r) => console.log(JSON.stringify(r)))\n"
        "  .catch((e) => { console.error(e); process.exit(1); });\n"
    )
    proc = subprocess.run([NODE, "-e", code], capture_output=True, text=True,
                          timeout=60, cwd=str(_ROOT))
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().split("\n")[-1])


def test_bridge_file_exists_and_parses():
    assert _BRIDGE.is_file()
    js = _BRIDGE.read_text(encoding="utf-8")
    assert "AstrBotPluginPage" in js
    # 必须同时看 window.parent(沙箱里 bridge 常挂父窗口)
    assert "parent" in js and "AstrBotPluginPage" in js
    if NODE:
        proc = subprocess.run([NODE, "--check", str(_BRIDGE)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr


def test_endpoint_candidates_cover_multiple_styles():
    """候选里必须同时有 '只补插件名' 与 '补 page/ 前缀' 两种风格。"""
    r = _run_node("return B.candidates('api/overview');")
    styles = {c["style"] for c in r}
    endpoints = {c["endpoint"] for c in r}
    assert {"page", "bare", "slash", "fullSlash"} <= styles, styles
    assert "api/overview" in endpoints                      # 官方示例风格
    assert "page/api/overview" in endpoints                 # 另一种风格
    assert "/astrbot_plugin_pokemon_world/page/api/overview" in endpoints
    assert all(not e.startswith("//") for e in endpoints), endpoints


def test_request_uses_bare_endpoint_first():
    """裸路由(`api/xxx`)必须是**第一个**候选。

    依据(读已安装的 dashboard 实现):前端把 endpoint 拼成
    `/api/v1/plugins/extensions/<插件名>/<endpoint>`,后端再与注册路由 fullmatch
    比对 —— 所以 endpoint 只能是"去掉插件名、去掉前导斜杠"的裸路由。
    写 `/api/xxx`(前导斜杠)会拼出双斜杠 → 404 → 页面表现为"总览无数据、
    保存无声失败"(这就是用户反馈的 bug)。
    """
    r = _run_node("""
      const tried = [];
      const bridge = { ready: async () => {},
        apiGet: async (ep) => { tried.push(ep); return { ok: true, players: [] }; },
        apiPost: async () => ({ ok: true }) };
      const win = { AstrBotPluginPage: bridge };
      await B.request(win, "GET", "api/players", {});
      return { tried };
    """)
    assert r["tried"] == ["api/players"], r


def test_request_falls_back_to_other_styles_and_caches():
    """别的版本/端口模式风格不同 → 必须能自动换风格,并**缓存**命中的那种。"""
    r = _run_node("""
      const tried = [];
      let phase = 0;
      const bridge = { ready: async () => {},
        apiGet: async (ep) => {
          tried.push({ ep, phase });
          if (ep === "page/api/players") return { ok: true, players: [{ uid: "u1" }] };
          throw new Error("未找到该路由");
        },
        apiPost: async () => ({ ok: true }) };
      const win = { AstrBotPluginPage: bridge };
      const first = await B.request(win, "GET", "api/players", {});
      const afterFirst = tried.length;
      phase = 1;
      const second = await B.request(win, "GET", "api/players", {});
      return { first, tried, afterFirst, afterSecond: tried.length - afterFirst,
               secondPhase: tried.slice(afterFirst).map((x) => x.phase) };
    """)
    assert r["first"]["ok"] and r["first"]["players"][0]["uid"] == "u1", r
    # 第一次先试 bare(失败)再试 page(成功)
    assert [x["ep"] for x in r["tried"][:2]] == ["api/players", "page/api/players"], r["tried"]
    assert r["afterSecond"] == 1, f"第二次应直接用缓存风格,实际试了 {r['afterSecond']} 次"
    assert r["secondPhase"] == [1], r


def test_request_continues_when_route_missing_is_returned_not_raised():
    """路由不存在时 bridge 也可能**不抛异常**,而是回一个错误对象 —— 也要继续试。"""
    r = _run_node("""
      const tried = [];
      const bridge = { ready: async () => {},
        apiGet: async (ep) => {
          tried.push(ep);
          if (ep === "api/overview") return { status: "error", message: "未找到该路由" };
          return { ok: true, players: 3 };
        },
        apiPost: async () => ({ ok: true }) };
      const win = { AstrBotPluginPage: bridge };
      const res = await B.request(win, "GET", "api/overview", {});
      return { res, tried };
    """)
    assert r["res"]["ok"] and r["res"]["players"] == 3, r
    assert len(r["tried"]) >= 2, r


def test_request_reads_bridge_from_parent_window():
    r = _run_node("""
      const bridge = { ready: async () => {}, apiGet: async () => ({ ok: true, players: 1 }),
                       apiPost: async () => ({ ok: true }) };
      const child = {};
      child.parent = { AstrBotPluginPage: bridge };
      child.parent.parent = child.parent;
      const res = await B.request(child, "GET", "api/players", {});
      const picked = B.getBridge(child) === bridge;
      return { res, picked };
    """)
    assert r["picked"], "没从 window.parent 找到 bridge"
    assert r["res"]["ok"] and r["res"]["players"] == 1


def test_request_reports_missing_bridge_clearly():
    r = _run_node("""
      const win = {}; win.parent = win;
      return await B.request(win, "GET", "api/overview", {});
    """)
    assert r["ok"] is False
    assert "bridge" in r["error"]


def test_request_sends_post_body_unchanged():
    r = _run_node("""
      let seen = null;
      const bridge = { ready: async () => {},
        apiGet: async () => ({ ok: true }),
        apiPost: async (ep, body) => { seen = { ep, body }; return { ok: true, done: ["x"] }; } };
      const win = { AstrBotPluginPage: bridge };
      const res = await B.request(win, "POST", "api/player/update",
        { body: { scope: "g1", uid: "u1", set: { money: 5 } } });
      return { seen, res };
    """)
    assert r["seen"]["body"]["scope"] == "g1"
    assert r["seen"]["body"]["set"]["money"] == 5
    assert r["res"]["ok"] and r["res"]["done"] == ["x"]


def test_normalize_handles_all_envelopes():
    r = _run_node("""
      return {
        mine: B.normalize({ ok: true, players: [1] }),
        success: B.normalize({ success: true, data: { players: [2] } }),
        wrapped: B.normalize({ data: { players: [3] } }),
        plain: B.normalize({ players: [4] }),
        errOk: B.normalize({ ok: false, message: "存档不存在" }),
        errStatus: B.normalize({ status: "error", message: "boom" }),
        arr: B.normalize([1, 2]),
        nil: B.normalize(null),
      };
    """)
    assert r["mine"]["players"] == [1]
    assert r["success"]["players"] == [2], r["success"]
    assert r["wrapped"]["players"] == [3]
    assert r["plain"]["players"] == [4]
    assert r["errOk"]["ok"] is False and "存档不存在" in r["errOk"]["error"]
    assert r["errStatus"]["ok"] is False and "boom" in r["errStatus"]["error"]
    assert r["arr"]["ok"] and r["arr"]["data"] == [1, 2]
    assert r["nil"]["ok"] is False


def test_frontend_routes_match_backend_registration():
    """app.js 里调用的每个端点,都必须能在 main.py 注册的路由里找到(且不带前导斜杠)。

    这正是"总览无数据"的另一半原因:前端写 `/api/overview`,后端注册的是
    `/{插件名}/api/overview` —— 多一个斜杠就对不上。
    """
    import re

    app = (_PAGE / "app.js").read_text(encoding="utf-8")
    main_py = (_ROOT / "main.py").read_text(encoding="utf-8")
    registered = set(re.findall(r'f"\{_WEB_BASE\}(/api/[^"]+)"', main_py))
    assert registered, "没从 main.py 解析到路由"

    calls = set(re.findall(r'api(?:Get|Post)\("([^"]+)"', app))
    calls |= set(re.findall(r"api(?:Get|Post)\(`([^`]+)`", app))
    assert calls, "没从 app.js 解析到调用"
    for call in calls:
        assert not call.startswith("/"), f"{call} 带了前导斜杠(endpoint 拼接会错位)"
        path = "/" + call.split("${")[0].rstrip("/")   # 模板参数先截掉
        hit = [r for r in registered
               if r == path or (r.endswith(path) and "<" in r) or r.startswith(path)]
        assert hit, f"app.js 调用了 {call},但后端没有对应路由:{sorted(registered)[:4]}…"
    # 模板端点(带 <scope>/<uid>)也要能被覆盖
    assert any("<" in r for r in registered), registered


def test_registered_view_wraps_dict_in_json_response():
    """注册给框架的视图要把 dict 包成 json_response;handler 本体仍是纯 dict。

    两条都要:框架那条能给出标准响应(带 status/body),测试这条可以直接断言字段。
    """
    import asyncio
    import tempfile

    from test_commands import _Cmd, _Event, run_cmd

    class Ctx:
        def __init__(self):
            self.routes = []

        def register_web_api(self, route, handler, methods, desc=""):
            self.routes.append((route, handler, methods))

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "battle_image": False, "quest_enable": False}
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        p.context = Ctx()
        p._register_web_apis()
        assert len(p.context.routes) >= 10
        raw = asyncio.run(p._web_overview())
        assert isinstance(raw, dict) and raw["ok"]
        view = p.context.routes[0][1]
        wrapped = asyncio.run(view())
        assert not isinstance(wrapped, dict), "注册视图应返回框架响应对象"
        assert getattr(wrapped, "status_code", None) == 200
        assert b'"ok":true' in bytes(getattr(wrapped, "body", b"")), wrapped
