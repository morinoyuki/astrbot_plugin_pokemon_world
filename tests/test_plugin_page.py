"""插件页面(pages/manage)与它的数据管理 REST 接口。

页面本体是静态文件,由 AstrBot dashboard 自动发现并以 iframe + bridge SDK 加载;
这里验证三件事:
  ① 页面文件齐全、引用了 bridge SDK(否则页面打不开);
  ② 路由注册:前缀、路径、方法都对(dashboard 按这些匹配);
  ③ 每个接口真的能读写存档(改金钱/治愈/发道具/授徽章/改宝可梦/删存档/重置世界),
     以及数据自检真的会跑。
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tests"))

# 顺序有讲究:test_commands 会以 `pw_plugin` 之名加载本插件包,所以先 import 它,
# 再去拿 pw_plugin.main(isort 会把这两行调换位置,导致 ModuleNotFoundError ——
# 用 noqa 明确锁住这个顺序)
from test_commands import _Cmd, _Event, run_cmd  # noqa: E402,I001

import pw_plugin.main as M  # noqa: E402


class FakeContext:
    """替身 context:只记录 register_web_api 的调用。"""

    def __init__(self):
        self.routes: list[tuple] = []

    def register_web_api(self, route, handler, methods, desc=""):
        self.routes.append((route, handler, tuple(methods), desc))


def _plugin(tmp, *, with_context: bool = False, today: bool = True):
    p = _Cmd(tmp)
    p.config = {"ui_image": False, "battle_image": False, "quest_enable": False}
    if with_context:
        p.context = FakeContext()
    run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
    if today:
        run_cmd(p, _Event("/今日"), p.cmd_today)   # 生成世界数据
    return p


def run(coro):
    return asyncio.run(coro)


# ── ① 页面文件 ─────────────────────────────────────────────────────
def test_page_files_exist_and_wire_the_bridge():
    page = _ROOT / "pages" / "manage"
    for name in ("index.html", "app.js", "bridge.js", "style.css"):
        f = page / name
        assert f.is_file(), f"缺少页面文件 {name}"
        assert f.stat().st_size > 200, f"{name} 太小,可能是空文件"
    html = (page / "index.html").read_text(encoding="utf-8")
    # bridge SDK 由 AstrBot 注入,页面**不需要**手写 <script src=...bridge-sdk.js>;
    # 但页面自己的资源(含适配层 bridge.js)必须按相对路径引用 ——
    # 绝对路径在 dashboard 的资源重写(带 asset_token)下会失效。
    for asset in ("./bridge.js", "./app.js", "./style.css"):
        assert asset in html, f"index.html 没引用 {asset}"
    br = (page / "bridge.js").read_text(encoding="utf-8")
    assert "AstrBotPluginPage" in br, "bridge.js 没找 bridge"
    js = (page / "app.js").read_text(encoding="utf-8")
    # app.js 只通过适配层说话(不直接碰 SDK),这样 endpoint 风格差异只在一处处理
    assert "PWPageBridge" in js, "app.js 没用适配层"
    assert "window.AstrBotPluginPage.api" not in js, "app.js 不该直接调 SDK"
    # 端点必须是**裸路由**(不带前导斜杠、不带插件名):dashboard 会拼成
    # `/api/v1/plugins/extensions/<插件名>/<endpoint>` 再与注册路由 fullmatch 比对,
    # 多一个前导斜杠就 404 —— 表现为"总览无数据、保存无声失败"。
    for ep in ("api/overview", "api/scopes", "api/players", "api/selfcheck",
               "api/maintenance", "api/cleanup", "api/player/update", "api/world/reset"):
        assert f'apiGet("{ep}"' in js or f'apiPost("{ep}"' in js, f"app.js 没调用 {ep}"
    import re as _re

    assert not _re.search(r'api(?:Get|Post)\("/', js), "端点带了前导斜杠"


# ── ② 路由注册 ─────────────────────────────────────────────────────
def test_routes_registered_with_plugin_prefix():
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp, with_context=True)
        ctx = p.context
        p._register_web_apis()
        paths = {r[0]: r for r in ctx.routes}
        assert len(paths) >= 10, ctx.routes
        for path, _h, methods, desc in ctx.routes:
            assert path.startswith("/astrbot_plugin_pokemon_world/api/"), path
            assert methods and set(methods) <= {"GET", "POST"}, methods
            assert desc.startswith("宝可梦世界:"), desc
        # 关键路由与 HTTP 方法
        expect = {
            "/astrbot_plugin_pokemon_world/api/overview": "GET",
            "/astrbot_plugin_pokemon_world/api/players": "GET",
            "/astrbot_plugin_pokemon_world/api/player/<scope>/<uid>": "GET",
            "/astrbot_plugin_pokemon_world/api/player/update": "POST",
            "/astrbot_plugin_pokemon_world/api/player/delete": "POST",
            "/astrbot_plugin_pokemon_world/api/world/<scope>": "GET",
            "/astrbot_plugin_pokemon_world/api/world/reset": "POST",
            "/astrbot_plugin_pokemon_world/api/selfcheck": "GET",
            "/astrbot_plugin_pokemon_world/api/maintenance": "GET",
            "/astrbot_plugin_pokemon_world/api/cleanup": "POST",
        }
        for path, method in expect.items():
            assert path in paths, f"没注册 {path}"
            assert method in paths[path][2], f"{path} 方法不对:{paths[path][2]}"


def test_register_web_apis_tolerates_missing_context():
    """旧版 AstrBot(没有 register_web_api)不能因为注册失败而崩。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)          # context = None
        p._register_web_apis()  # 不应抛异常


# ── ③ 接口行为 ─────────────────────────────────────────────────────
def test_overview_and_selfcheck():
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        ov = run(p._web_overview())
        assert ov["ok"] and ov["players"] == 1 and ov["scopes"] >= 1
        assert ov["data"]["species"] > 1000 and ov["data"]["tms"] >= 25
        ck = run(p._web_selfcheck())
        assert ck["ok"]
        assert ck["passed"], f"自检不应有失败项:{[c for c in ck['checks'] if not c['ok']]}"
        names = {c["name"] for c in ck["checks"]}
        assert {"招式机数据", "道具获取途径", "昼夜条件", "道馆可达"} <= names, names


def test_players_and_detail():
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        pl = run(p._web_players())
        assert pl["ok"] and pl["total"] == 1
        row = pl["players"][0]
        assert row["name"] == "小智" and row["region_zh"] == "关都"
        assert row["party_zh"] and not row["broken"]
        d = run(p._web_player(row["scope"], row["uid"]))
        assert d["ok"]
        assert d["summary"]["money"] == row["money"]
        assert d["party"][0]["zh"] == "杰尼龟"
        assert "raw" in d and isinstance(d["raw"], dict)
        # 不存在的存档 → 404
        bad = run(p._web_player("nope", "nope"))
        assert getattr(bad, "status_code", 200) == 404


def test_update_writes_through():
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        scope, uid = "g10086", "u1"
        # 没有请求体(测试直调)时应当被拒绝而不是抛异常
        no_body = run(p._web_player_update())
        assert getattr(no_body, "status_code", 200) == 400, no_body

        # 直调带 body:替换模块级 _web_body
        import pw_plugin.main as PM

        async def fake_body():
            return {
                "scope": scope, "uid": uid,
                "set": {"money": 12345, "name": "小茂"},
                "actions": [
                    {"op": "heal"},
                    {"op": "item", "key": "potion", "count": 3},
                    {"op": "badge", "region": "kanto", "order": 2},
                    {"op": "mon", "where": "party", "index": 1,
                     "set": {"level": 20, "friendship": 200, "nickname": "水箭"}},
                ],
            }

        potion0 = int((p.trainers.load(scope, uid).get("bag") or {}).get("potion") or 0)
        orig = PM._web_body
        PM._web_body = fake_body
        try:
            out = run(p._web_player_update())
        finally:
            PM._web_body = orig
        assert out["ok"], out
        d = p.trainers.load(scope, uid)
        assert d["money"] == 12345
        assert d["name"] == "小茂"
        assert "kanto:2" in d["badges"]
        # 初始背包就有伤药,所以比"多了 3 个"(别写死绝对值)
        assert d["bag"]["potion"] == potion0 + 3, (potion0, d["bag"].get("potion"))
        mon = d["party"][0]
        assert mon["level"] == 20 and mon["friendship"] == 200
        assert mon["nickname"] == "水箭"
        assert all(m["cur_hp"] == m["max_hp"] for m in d["party"]), "治愈应回满"


def test_update_rejects_bad_input():
    import pw_plugin.main as PM

    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        cases = [
            ({}, "缺少"),
            ({"scope": "g10086", "uid": "u1", "actions": [{"op": "nope"}]}, "未知操作"),
            ({"scope": "g10086", "uid": "u1",
              "actions": [{"op": "item", "key": "根本没有这个道具"}]}, "没有这个道具"),
            ({"scope": "g10086", "uid": "u1",
              "actions": [{"op": "mon", "where": "party", "index": 99}]}, "找不到"),
        ]
        for body, _want in cases:
            async def fake_body(_b=body):
                return _b

            orig = PM._web_body
            PM._web_body = fake_body
            try:
                r = run(p._web_player_update())
            finally:
                PM._web_body = orig
            status = getattr(r, "status_code", 200)
            assert status == 400, f"{body} 应被拒绝,实际 {r}"


def test_delete_player_and_scope_needs_confirm():
    import pw_plugin.main as PM

    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)

        async def body(payload):
            async def _f():
                return payload
            return _f

        def call(payload):
            f = run(body(payload))
            orig = PM._web_body
            PM._web_body = f
            try:
                return run(p._web_player_delete())
            finally:
                PM._web_body = orig

        # 整范围删除必须显式确认
        r = call({"scope": "g10086", "all": True})
        assert getattr(r, "status_code", 200) == 400, "整范围删除没要确认就执行了"
        assert p.trainers.list_players("g10086") == ["u1"]
        # 单个 uid
        ok = call({"scope": "g10086", "uid": "u1"})
        assert ok["ok"] and p.trainers.list_players("g10086") == []
        # 再删同一 uid → 404
        again = call({"scope": "g10086", "uid": "u1"})
        assert getattr(again, "status_code", 200) == 404


def test_world_view_and_reset():
    import pw_plugin.main as PM

    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        w = run(p._web_world("g10086"))
        assert w["ok"] and w["summary"]["day_no"] >= 1
        assert len(w["summary"]["weather"]) >= 8
        # 没有世界数据的 scope → 404(带生成提示)
        bad = run(p._web_world("u999"))
        assert getattr(bad, "status_code", 200) == 404

        # 写点封锁与事件,再重置
        st = p.worlds.load("g10086")
        st["locks"] = {"kanto-route-3": 99}
        st["events"] = [{"location": "kanto-route-3", "kind": "swarm"}]
        st["modifiers"] = {"encounter_mult": 9.0}
        p.worlds.save("g10086", st)

        async def fake_body():
            return {"scope": "g10086", "what": "all"}

        orig = PM._web_body
        PM._web_body = fake_body
        try:
            r = run(p._web_world_reset())
        finally:
            PM._web_body = orig
        assert r["ok"] and len(r["reset"]) == 5
        after = p.worlds.load("g10086")
        assert after["locks"] == {} and after["events"] == []
        assert after["modifiers"] != {"encounter_mult": 9.0}, "增益应重置"


def test_maintenance_and_cleanup():
    import os

    import pw_plugin.main as PM

    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        m = run(p._web_maintenance())
        assert m["ok"] and m["db"]["trainers"] == 1
        # 造两个"过期"的临时图
        tmpdir = m["temp"]["dir"]
        made = []
        for i in range(2):
            path = os.path.join(tmpdir, f"pw_apitest_{i}.png")
            with open(path, "wb") as f:
                f.write(b"x" * 1024)
            os.utime(path, (0, 0))        # 1970 → 必然过期
            made.append(path)
        try:
            async def fake_body():
                return {"keep_seconds": 60}

            orig = PM._web_body
            PM._web_body = fake_body
            try:
                r = run(p._web_cleanup())
            finally:
                PM._web_body = orig
            assert r["ok"] and r["removed"] >= 2, r
            assert not os.path.exists(made[0]), "过期临时图没被清掉"
        finally:
            for path in made:
                if os.path.exists(path):
                    os.remove(path)


def test_web_helpers_are_module_level():
    """`_web_query`/`_web_body` 必须**是模块级函数**。

    写成 `@staticmethod` 时,测试宿主的 `_Cmd` 会把它们重绑成实例方法,
    `self._web_query("scope")` 就变成了 `(self, "scope")` —— 读到的不是参数,
    会静默返回默认值(实测:页面"按范围筛选"失效,玩家列表永远为空)。
    """
    import inspect

    assert inspect.isfunction(M._web_query), type(M._web_query)
    assert inspect.iscoroutinefunction(M._web_body), type(M._web_body)
    assert "self._web_query(" not in (_ROOT / "main.py").read_text(encoding="utf-8")
    assert "self._web_body(" not in (_ROOT / "main.py").read_text(encoding="utf-8")


def test_unknown_scope_lists_everything():
    """不带 scope 参数时列出所有范围(而不是一个都列不出来)。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _plugin(tmp)
        r = run(p._web_players())
        assert r["total"] == 1, r
        assert json.dumps(r, ensure_ascii=False)      # 可序列化(能直接返回给前端)
