"""指令层集成测试:把 PokemonWorldPlugin 的所有方法绑到一个假对象上,
用假 event 跑完整指令流(不启动 AstrBot,不联网,不调用 LLM)。

这是唯一能一次性验证「指令 → 内核 → 存档」整条链路的测试。
"""

import asyncio
import contextlib
import importlib.util
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


def _load_plugin_package():
    spec = importlib.util.spec_from_file_location(
        "pw_plugin",
        os.path.join(_ROOT, "__init__.py"),
        submodule_search_locations=[_ROOT],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pw_plugin"] = mod
    spec.loader.exec_module(mod)
    return mod


_MOD = _load_plugin_package()
Plugin = _MOD.PokemonWorldPlugin

from pw.player import TrainerStore  # noqa: E402
from pw.sqlite_store import SqliteBackend  # noqa: E402
from pw.world import WorldMap  # noqa: E402
from pw.worldstate import WorldStore  # noqa: E402


class _Cmd:
    """只绑定方法、不执行 __init__ 的宿主对象。

    存档后端与真实插件保持一致(默认 SQLite):`config=None` 时 `_cfg` 走默认值,
    所以这里也按默认建 SQLite 后端 —— 否则测试跑的是"文件存储"这条**玩家不会走**
    的路径,存档相关的问题测不出来。
    """

    def __init__(self, tmp, *, storage: str = "sqlite"):
        self.config = None  # → _cfg 全部走默认值(provider_id 为空 = 不调用 LLM)
        self._storage = storage
        self._db = SqliteBackend(tmp) if storage != "json" else None
        if self._db is not None:
            self._db.import_legacy()
        self.trainers = TrainerStore(tmp, backend=self._db)
        self.worlds = WorldStore(tmp, backend=self._db)
        self._locks = {}
        self.context = None
        self.data_dir = tmp


# 把插件类上除 __init__ 外的所有方法挂到 _Cmd
for _name in dir(Plugin):
    if _name.startswith("__"):
        continue
    _fn = getattr(Plugin, _name)
    if callable(_fn):
        setattr(_Cmd, _name, _fn)


class _Event:
    def __init__(self, text=""):
        self.message_str = text
        self.unified_msg_origin = "test:GroupMessage:g1"
        self.group_id = "10086"
        self.sender_id = "u1"
        self.outputs = []

    def get_group_id(self):
        return "10086"

    def get_sender_id(self):
        return "u1"

    def plain_result(self, text):
        self.outputs.append(str(text))
        return ("text", str(text))

    def chain_result(self, comps):
        # 图片卡片里也可能夹着 Plain 文本,测试需要能断言到它
        texts = "".join(str(getattr(c, "text", "") or "") for c in comps)
        self.outputs.append(f"<chain:{len(comps)}>{texts}")
        return ("chain", comps)


def run_cmd(plugin, ev, method):
    async def go():
        return [r async for r in method(ev)]

    return asyncio.run(go())


SCOPE = "g10086"


def test_full_command_flow():
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        ev = _Event()

        # /帮助
        out = run_cmd(p, ev, p.cmd_help)
        assert out and "宝可梦世界" in "".join(ev.outputs)

        # 未开始时的提示
        ev = _Event("/状态")
        run_cmd(p, ev, p.cmd_status)
        assert any("还没有开始旅程" in x for x in ev.outputs)

        # /开始(不带御三家 → 弹出选择菜单,不建档)
        ev = _Event("/开始 小智")
        run_cmd(p, ev, p.cmd_start)
        assert any("选择你的初始宝可梦" in x for x in ev.outputs)
        assert not p.trainers.exists(SCOPE, "u1")

        # /开始 小智 新叶喵
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        assert p.trainers.exists(SCOPE, "u1")
        assert any("欢迎来到宝可梦世界" in x for x in ev.outputs)
        t = p._load(ev)
        assert t.party[0]["species"] == "sprigatito"
        assert t.bag["poke-ball"] == 5

        # /状态 —— 内容断言走文本回退路径(图片路径只带提示行,不再重复整段文本)
        p.config = {"ui_image": False}
        ev = _Event("/状态")
        run_cmd(p, ev, p.cmd_status)
        assert any("训练家档案" in x for x in ev.outputs)
        assert any("真新镇" in x or "出发" in x or "危险度" in x for x in ev.outputs)
        p.config = None

        # /队伍
        p.config = {"ui_image": False}
        ev = _Event("/队伍")
        run_cmd(p, ev, p.cmd_team)
        assert any("新叶喵" in x for x in ev.outputs)
        assert any("库存" in x for x in ev.outputs), "文本回退应给出完整队伍信息"
        p.config = None

        # /地图
        p.config = {"ui_image": False}
        ev = _Event("/地图")
        run_cmd(p, ev, p.cmd_map)
        assert any("真新镇" in x for x in ev.outputs)
        p.config = None

        # /前往(相邻 → 成功;不相邻 → 拒绝)
        world = WorldMap()
        start = p._load(_Event()).location
        nxt = world.neighbors(start)[0]
        ev = _Event(f"/前往 {world.node_zh(nxt)}")
        run_cmd(p, ev, p.cmd_go)
        assert any("你来到了" in x for x in ev.outputs), ev.outputs
        assert p._load(_Event()).location == nxt

        far = next(
            k for k in world.nodes("kanto")
            if k != nxt and k not in world.neighbors(nxt)
        )
        ev = _Event(f"/前往 {world.node_zh(far)}")
        run_cmd(p, ev, p.cmd_go)
        assert any(("只能前往相邻地点" in x) or ("危险度过高" in x) for x in ev.outputs)

        # /商店(非城镇 → 拒绝)
        ev = _Event("/商店")
        run_cmd(p, ev, p.cmd_shop)
        assert any("没有商店" in x for x in ev.outputs)

        # /治疗(非城镇 → 拒绝)
        ev = _Event("/治疗")
        run_cmd(p, ev, p.cmd_heal)
        assert any("没有宝可梦中心" in x for x in ev.outputs)

        # /今日(含事件)
        p.config = {"ui_image": False}
        ev = _Event("/今日")
        run_cmd(p, ev, p.cmd_today)
        joined = "".join(ev.outputs)
        assert "世界日" in joined or "第" in joined
        p.config = None

        # /探索:反复探索直到开战或拿到道具
        started = False
        for _ in range(12):
            ev = _Event("/探索")
            run_cmd(p, ev, p.cmd_explore)
            joined = "".join(ev.outputs)
            # 图片路径不会把战斗正文再发一遍,所以用**内核状态**判断是否开战
            if p._load(_Event()).data.get("battle") or "⚔️" in joined:
                started = True
                break
            # 步数会影响掷骰,推进一下
            t = p._load(_Event())
            t.data["steps"] = int(t.data.get("steps", 0)) + 7
            p._save(t)
        assert started, "多次探索后仍未触发战斗"

        # /对战 无参数 → 显示状态
        ev = _Event("/对战")
        run_cmd(p, ev, p.cmd_battle)
        assert any("对战" in x for x in ev.outputs)

        # /对战 <招式序号>:一直打到结束
        # 注意:序号 1 可能是"摇尾巴"这类 0 威力变化招 —— 一直用它打不死人,
        # 战斗会僵持到系统判"不了了之"(有 STALL_LIMIT 兜底)。这里挑**有威力**的招。
        def _damaging_slot(t):
            from pw.dex import get_dex

            dex = get_dex()
            mon = t.party[0] if t.party else {}
            for i, mv in enumerate(mon.get("moves") or [], 1):
                if int((dex.moves.get(mv) or {}).get("basePower") or 0) > 0:
                    return i
            return 1

        for _ in range(40):
            t = p._load(_Event())
            if not t.data.get("battle"):
                break
            ev = _Event(f"/对战 {_damaging_slot(t)}")
            run_cmd(p, ev, p.cmd_battle)
            joined = "".join(ev.outputs)
            if any(k in joined for k in ("战斗胜利", "战斗失败", "捕获成功",
                                        "脱离了战斗", "你认输了", "不了了之")):
                break
            if "必须换人" in joined or "全部失去战斗能力" in joined:
                break
        # 兜底:还没结束就认输(这条测试关心的是"能打完",不是必然获胜)
        if p._load(_Event()).data.get("battle"):
            run_cmd(p, _Event("/对战 forfeit"), p.cmd_battle)
        assert not p._load(_Event()).data.get("battle"), "战斗应当能正常结束"

        # /图鉴
        p.config = {"ui_image": False}
        ev = _Event("/图鉴 新叶喵")
        run_cmd(p, ev, p.cmd_dex)
        assert any("新叶喵" in x for x in ev.outputs)
        p.config = None

        # /学招 / 进化(无待学、无法进化时应给出提示而非崩溃)
        ev = _Event("/学招 1 飞叶快刀")
        run_cmd(p, ev, p.cmd_learn)
        ev = _Event("/进化")
        run_cmd(p, ev, p.cmd_evolve)
        assert ev.outputs

        # /重置世界
        ev = _Event("/重置世界")
        run_cmd(p, ev, p.cmd_reset)
        assert any("已删除" in x for x in ev.outputs)
        assert not p.trainers.exists(SCOPE, "u1")


def test_shop_buy_sell_at_town():
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)

        p.config = {"ui_image": False}   # 文本回退路径才能断言内容
        ev = _Event("/商店")
        run_cmd(p, ev, p.cmd_shop)
        assert any("商店" in x and "余额" in x for x in ev.outputs)
        p.config = None

        before = p._load(ev).money
        ev = _Event("/商店 买 伤药 2")
        run_cmd(p, ev, p.cmd_shop)
        assert any("买下" in x for x in ev.outputs)
        t = p._load(ev)
        assert t.count("potion") == 5  # 初始 3 + 2
        assert t.money < before

        ev = _Event("/商店 卖 伤药 1")
        run_cmd(p, ev, p.cmd_shop)
        assert any("卖出" in x for x in ev.outputs)
        assert p._load(ev).count("potion") == 4

        # 买不起
        ev = _Event("/商店 买 大师球 1")
        run_cmd(p, ev, p.cmd_shop)
        assert any(("商店没有" in x) or ("需要" in x) for x in ev.outputs)


def test_gym_and_league_flow():
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        world = WorldMap()

        # 不在道馆城市 → 提示下一个道馆
        ev = _Event("/道馆")
        run_cmd(p, ev, p.cmd_gym)
        assert any("没有道馆" in x or "下一个道馆" in x for x in ev.outputs)

        # 徽章不足时不能挑战联盟
        ev = _Event("/联盟")
        run_cmd(p, ev, p.cmd_league)
        assert any("徽章" in x for x in ev.outputs)

        # 传送到道馆城市并配置强队 → 挑战成功拿到徽章
        from pw.engine import create_pokemon

        t = p._load(ev)
        gym = world.gyms("kanto")[0]
        t.data["location"] = gym["location"]
        mon = create_pokemon("charizard", 70)
        d = mon.to_dict()
        d["id"] = "m9"
        t.data["party"] = [d]
        p._save(t)

        p.config = {"ui_image": False}
        ev = _Event("/道馆")
        run_cmd(p, ev, p.cmd_gym)
        assert any("小刚" in x for x in ev.outputs)
        p.config = None

        ev = _Event("/道馆 挑战")
        run_cmd(p, ev, p.cmd_gym)
        assert any("小刚" in x or "对战" in x for x in ev.outputs)

        for _ in range(60):
            t = p._load(ev)
            if t.party[0].get("cur_hp", 0) <= 0:
                t.heal_party()
                p._save(t)
            ev = _Event(f"/对战 move {t.party[0]['moves'][0]}")
            run_cmd(p, ev, p.cmd_battle)
            if any(k in "".join(ev.outputs) for k in ("战斗胜利", "战斗失败")):
                break
        t = p._load(ev)
        assert "kanto:1" in t.badges, t.badges
        assert not t.data.get("battle")


def test_story_legend_tournament_commands():
    """主线/神兽/世界大赛三个指令的面板与挑战入口。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)

        # /主线 面板(文本回退路径:图片路径不含章节文字)
        p.config = {"ui_image": False}
        ev = _Event("/主线")
        run_cmd(p, ev, p.cmd_story)
        joined = "".join(ev.outputs)
        assert ("主线" in joined and "敌对组织" not in joined) or "主线" in joined
        assert "当前目标" in joined
        p.config = None

        # /主线 挑战(不在正确地点 → 拒绝)
        t = p._load(ev)
        for i in (1, 2):
            t.add_badge("kanto", i)  # 满足首个 boss 的徽章门槛
        p._save(t)
        ev = _Event("/主线 挑战")
        run_cmd(p, ev, p.cmd_story)
        assert any(("先过去" in x) or ("不是战斗章节" in x) for x in ev.outputs)

        # 走到正确地点并挑战 → 开战
        from pw import story
        from pw.engine import create_pokemon

        t = p._load(ev)
        cur = story.current_stage(t)
        assert cur and cur["kind"] == "boss", cur
        t.data["location"] = cur["location"]
        # 必须在开战**之前**换强队:对战会把队伍快照写回存档
        strong = create_pokemon("charizard", 80).to_dict()
        strong["id"] = "m1"
        t.data["party"] = [strong]
        p._save(t)
        ev = _Event("/主线 挑战")
        run_cmd(p, ev, p.cmd_story)
        assert p._load(ev).data.get("battle"), ev.outputs
        done = False
        for _ in range(80):
            t = p._load(ev)
            if t.party[0].get("cur_hp", 0) <= 0:
                t.heal_party()
                p._save(t)
            ev2 = _Event(f"/对战 move {t.party[0]['moves'][0]}")
            run_cmd(p, ev2, p.cmd_battle)
            joined = "".join(ev2.outputs)
            if "主线推进" in joined or "战斗胜利" in joined:
                done = True
            if "战斗胜利" in joined or "战斗失败" in joined:
                break
        t = p._load(ev)
        assert done, "击败剧情敌人后应推进主线"
        assert cur["key"] in (t.flag("story:kanto", []) or [])

        # /神兽 面板
        p.config = {"ui_image": False}
        ev = _Event("/神兽")
        run_cmd(p, ev, p.cmd_legend)
        assert any("传说" in x for x in ev.outputs)
        p.config = None
        ev = _Event("/神兽 挑战 急冻鸟")
        run_cmd(p, ev, p.cmd_legend)
        assert any(("这里没有" in x) or ("此地没有" in x) or ("传说的宝可梦" in x) for x in ev.outputs)

        # 把玩家送到急冻鸟栖息地并给足条件 → 能开战
        from pw import legendary

        t = p._load(ev)
        site = next(s for s in legendary.sites_for("kanto") if s["species"] == "articuno")
        t.data["location"] = site["location"]
        for i in range(1, int(site["need"]) + 1):
            t.add_badge("kanto", i)
        p._save(t)
        p.config = {"ui_image": False, "battle_image": False}
        ev = _Event("/神兽 挑战 急冻鸟")
        run_cmd(p, ev, p.cmd_legend)
        assert p._load(ev).data.get("battle"), ev.outputs
        assert any("传说的宝可梦" in x for x in ev.outputs)
        p.config = None
        # 战斗还在进行中 —— 对战锁定会拦下后面的指令,先把这场收尾
        t = p._load(ev)
        t.data["battle"] = None
        p._save(t)

        # /大赛 未夺冠 → 拒绝
        ev = _Event("/大赛")
        run_cmd(p, ev, p.cmd_tournament)
        assert any("冠军" in x for x in ev.outputs)

        # 夺冠后 → 面板 + 挑战
        t = p._load(ev)
        t.set_flag("champion:kanto", True)
        p._save(t)
        p.config = {"ui_image": False}
        ev = _Event("/大赛")
        run_cmd(p, ev, p.cmd_tournament)
        assert any("世界大赛" in x for x in ev.outputs)
        p.config = None
        t = p._load(ev)
        t.data["battle"] = None
        p._save(t)
        ev = _Event("/大赛 挑战")
        run_cmd(p, ev, p.cmd_tournament)
        assert any(("对战" in x) or ("大赛" in x) for x in ev.outputs)


def test_menu_screens_emit_images_when_enabled():
    """队伍/状态/背包/图鉴 在 ui_image 开启时应输出图片卡片(而不是纯文本)。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        # 打开界面图片(默认也是开的,这里显式确认)
        p.config = {"ui_image": True, "battle_image_scale": 2}
        for cmd, fn in (
            ("/状态", p.cmd_status),
            ("/队伍", p.cmd_team),
            ("/背包", p.cmd_bag),
            ("/图鉴 新叶喵", p.cmd_dex),
        ):
            ev = _Event(cmd)
            run_cmd(p, ev, fn)
            joined = "".join(ev.outputs)
            assert "<chain:" in joined, f"{cmd} 没有输出图片:{ev.outputs}"
            # 图片路径绝不重发整段正文:要么纯图片(<chain:1>),要么只多一行短提示
            assert "<chain:2>" not in joined or len(joined) < 200, (
                f"{cmd} 附带的文本过长(疑似重复发正文):{joined[:120]}"
            )
            if "<chain:2>" in joined:
                assert any(
                    k in joined
                    for k in ("行动", "使用:", "移动:", "买卖:", "挑战:", "管理:",
                              "切换分类")
                ), f"{cmd} 附带的文本不是指令提示:{joined[:120]}"
        # 关闭开关 → 回退纯文本
        p.config = {"ui_image": False}
        ev = _Event("/队伍")
        run_cmd(p, ev, p.cmd_team)
        assert "<chain:" not in "".join(ev.outputs)
        assert any("队伍" in x or "招式" in x for x in ev.outputs)


def test_all_menu_screens_emit_images():
    """所有"看板类"指令在 ui_image 开启时都应输出图片卡片。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": True, "battle_image_scale": 2}
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        # 给点进度,让道馆/联盟/神兽/大赛都有内容可画
        t = p._load(ev)
        for i in range(1, 9):
            t.add_badge("kanto", i)   # 联盟看板需要 8 枚徽章
        t.set_flag("champion:kanto", True)
        t.set_flag("world_champion", True)
        p._save(t)   # ← 必须落盘:下面每个用例都会重新 load
        # 每个看板都要求身处合适的地点(例如道馆看板要在道馆城镇,联盟要在联盟)
        cases = [
            ("/地图", p.cmd_map, "pewter-city"),
            ("/商店", p.cmd_shop, "pewter-city"),
            ("/道馆", p.cmd_gym, "pewter-city"),
            ("/联盟", p.cmd_league, "kanto-pokemon-league"),
            ("/主线", p.cmd_story, "pewter-city"),
            ("/神兽", p.cmd_legend, "pewter-city"),
            ("/今日", p.cmd_today, "pewter-city"),
            ("/大赛", p.cmd_tournament, "kanto-pokemon-league"),
        ]
        for cmd, fn, loc in cases:
            t = p._load(_Event(""))
            t.data["location"] = loc
            p._save(t)
            ev = _Event(cmd)
            run_cmd(p, ev, fn)
            joined = "".join(ev.outputs)
            assert "<chain:" in joined, f"{cmd} 没有输出图片:{ev.outputs[:3]}"


def test_battle_end_emits_result_card():
    """战斗结束要额外给一张战报卡(而不只是对战画面)。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": True, "battle_image": True, "battle_image_scale": 2}
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        t = p._load(ev)
        t.data["location"] = "kanto-route-1"
        p._save(t)
        # 直接开一场碾压局
        from pw import battle as B
        from pw.engine import create_pokemon

        strong = create_pokemon("charizard", 80).to_dict()
        strong["id"] = "m1"
        t = p._load(ev)
        t.data["party"] = [strong]
        p._save(t)
        B.start(t, [{"species": "rattata", "level": 3}], kind="wild", wild=True,
                meta={"title": "野生的 小拉达"}, day=1)
        p._save(t)
        chains = 0
        for _ in range(12):
            t = p._load(ev)
            ev2 = _Event(f"/对战 move {t.party[0]['moves'][0]}")
            run_cmd(p, ev2, p.cmd_battle)
            chains += "".join(ev2.outputs).count("<chain:")
            if p._load(ev2).data.get("battle") is None:
                break
        # 至少:对战画面 + 结算卡(两张图片卡片)
        assert chains >= 2, f"战斗结束只输出了 {chains} 张图片"


def test_temp_images_are_unique_and_pruned():
    """临时图文件名必须唯一,且过期文件要被清理。

    旧实现用 `hash(text)%100000` / 图片字节长度命名:AstrBot 是在指令返回之后
    才去读图片文件,撞名会让先发的那张被覆盖(玩家看到别人的/上一回合的画面);
    而且只写不删,长时间运行会攒出成千上万个文件。
    """
    import glob
    import os
    import time

    import pw_plugin  # noqa: F401

    main_mod = sys.modules["pw_plugin.main"]

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "quest_enable": False}
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)

        # 同样内容连续写 3 次 → 必须是 3 个不同文件(否则会互相覆盖)
        paths = [p._temp_image(b"same-bytes", "pw_ui_probe") for _ in range(3)]
        assert len(set(paths)) == 3
        for path in paths:
            assert os.path.exists(path)

        # 过期文件会被清理,新鲜文件不会被误删
        stale = os.path.join(tempfile.gettempdir(), "pw_ui_stale_probe.png")
        with open(stale, "wb") as fh:
            fh.write(b"x")
        old = time.time() - 9999
        os.utime(stale, (old, old))
        main_mod._prune_temp_images(tempfile.gettempdir())
        assert not os.path.exists(stale)
        assert all(os.path.exists(x) for x in paths)
        # 不碰别人的文件
        other = os.path.join(tempfile.gettempdir(), "unrelated_probe.png")
        with open(other, "wb") as fh:
            fh.write(b"x")
        os.utime(other, (old, old))
        main_mod._prune_temp_images(tempfile.gettempdir())
        assert os.path.exists(other)

        for path in [*paths, other]:
            with contextlib.suppress(OSError):
                os.remove(path)
        assert glob


def test_clear_all_saves_requires_configured_admin():
    """`/重置世界 -all` 是破坏性操作:没配置管理员时必须拒绝。

    历史 bug:`if admins and uid not in admins` —— 默认 admin_uids 为空,
    条件永远为假,**任何玩家都能清空全群存档**。
    """
    def _run(admin_cfg):
        tmp = tempfile.mkdtemp()
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "quest_enable": False, "admin_uids": admin_cfg}
        run_cmd(p, _Event("/开始 小智 新叶喵"), p.cmd_start)
        ev = _Event("/重置世界 -all")
        run_cmd(p, ev, p.cmd_reset)
        return "".join(ev.outputs), p.trainers.exists("g10086", "u1")

    out, saved = _run("")                 # 默认:没配置管理员
    assert "禁用" in out or "管理员" in out, out
    assert saved, "没配置管理员时绝不能清空存档"

    out, saved = _run("999,888")          # 别人是管理员
    assert "只有管理员" in out
    assert saved

    out, saved = _run("u1")               # 自己是管理员(-all 合法)
    assert "已清空" in out
    assert not saved


def _fresh(tmp, starter="新叶喵"):
    p = _Cmd(tmp)
    p.config = {"ui_image": False, "quest_enable": False, "battle_image": False}
    ev = _Event(f"/开始 小智 {starter}")
    run_cmd(p, ev, p.cmd_start)
    return p, ev


def test_starter_must_come_from_the_pool():
    """初始宝可梦必须在候选池内 —— 曾经可以直接 /开始 小智 超梦 开局。"""
    for species in ("超梦", "阿尔宙斯", "烈空坐"):
        with tempfile.TemporaryDirectory() as tmp:
            p = _Cmd(tmp)
            p.config = {"ui_image": False, "quest_enable": False}
            ev = _Event(f"/开始 小智 {species}")
            run_cmd(p, ev, p.cmd_start)
            out = "".join(ev.outputs)
            assert "不在初始宝可梦候选里" in out, out
            assert not p.trainers.exists("g10086", "u1"), "越权初始宝可梦不能建号"

    # 候选池内的正常可选
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _fresh(tmp, "皮卡丘")
        assert p._load(ev).party[0]["species"] == "pikachu"


def test_challenge_commands_survive_all_fainted_team():
    """全队倒下后挑战道馆/训练家/神兽不能抛未捕获异常(玩家收不到任何回复)。"""
    from pw import battle as B

    for cmd, attr in (("/道馆 挑战", "cmd_gym"), ("/训练家战 1", "cmd_npc")):
        with tempfile.TemporaryDirectory() as tmp:
            p, ev = _fresh(tmp)
            t = p._load(ev)
            t.data["location"] = "pewter-city"
            t.data["party"][0]["cur_hp"] = 0
            p._save(t)
            ev2 = _Event(cmd)
            run_cmd(p, ev2, getattr(p, attr))          # 不能抛异常
            out = "".join(ev2.outputs)
            assert "失去战斗能力" in out, out
            assert not B.in_battle(p._load(ev2))


def test_shop_learn_evolve_blocked_in_battle():
    """对战中买卖/学招/进化会被战斗快照回滚 → 必须直接拒绝。"""
    from pw import battle as B

    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _fresh(tmp, "小火龙")
        t = p._load(ev)
        t.data["location"] = "pewter-city"
        p._save(t)
        t = p._load(ev)
        B.start(t, [{"species": "geodude", "level": 14}], kind="gym",
                meta={"kind": "gym", "title": "道馆战"}, day=1)
        p._save(t)
        t = p._load(ev)
        money0, potion0 = t.money, t.count("potion")
        moves0 = list(t.party[0]["moves"])

        for cmd, attr, hint in (
            ("/商店 买 伤药 1", "cmd_shop", "锁定"),
            ("/商店 卖 伤药 1", "cmd_shop", "锁定"),
            ("/学招 1 喷射火焰", "cmd_learn", "锁定"),
            ("/进化 1", "cmd_evolve", "锁定"),
        ):
            ev2 = _Event(cmd)
            run_cmd(p, ev2, getattr(p, attr))
            assert hint in "".join(ev2.outputs), f"{cmd} 应被拒绝"

        t2 = p._load(ev2)
        assert t2.money == money0 and t2.count("potion") == potion0, "财产不能变"
        assert list(t2.party[0]["moves"]) == moves0


def test_learn_replace_reports_failure():
    """新招已经会了时不能谎报"学会了"。"""
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _fresh(tmp, "小火龙")
        t = p._load(ev)
        mon = t.party[0]
        mon["moves"] = ["ember", "scratch", "growl", "firefang"]
        p._save(t)
        ev2 = _Event("/学招 1 ember 替换 scratch")
        run_cmd(p, ev2, p.cmd_learn)
        t2 = p._load(ev2)
        assert list(t2.party[0]["moves"]) == ["ember", "scratch", "growl", "firefang"]
        assert "已经会了" in "".join(ev2.outputs), "".join(ev2.outputs)


def test_trainer_card_day_count_increases():
    """训练家卡的"第几天"要按创建当天算,不能恒为 1(旧代码读了不存在的字段)。"""
    from pw.util import game_day

    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _fresh(tmp)
        t = p._load(ev)
        assert t.data.get("play_day"), "new_trainer 应写入 play_day"
        payload = p._card_payload(t)
        assert payload["play_day"] >= 1
        # 把创建日往前推 5 天 → 应当显示第 6 天
        t.data["play_day"] = game_day() - 5
        p._save(t)
        assert p._card_payload(p._load(ev))["play_day"] == 6


def test_help_lists_reset_world():
    from prompts import HELP_TEXT

    assert "重置世界" in HELP_TEXT


def test_trade_evolution_and_rare_candy_are_usable():
    """通信进化(30 个物种)与神奇糖果(大赛/委托奖励)必须能真的用掉。"""
    from pw.engine import create_pokemon

    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _fresh(tmp)
        t = p._load(ev)
        t.data["location"] = "pewter-city"
        t.data["party"] = [create_pokemon("kadabra", 30).to_dict()]
        t.data["party"][0]["id"] = "m1"
        t.add_item("rare-candy", 2)
        p._save(t)

        ev2 = _Event("/交换 1")
        run_cmd(p, ev2, p.cmd_trade)
        assert "胡地" in "".join(ev2.outputs), "勇基拉应通过连接交换进化"
        assert p._load(ev2).party[0]["species"] == "alakazam"

        ev3 = _Event("/使用 神奇糖果 1")
        run_cmd(p, ev3, p.cmd_use)
        t3 = p._load(ev3)
        assert t3.party[0]["level"] >= 31, "神奇糖果要真的升级"
        assert t3.count("rare-candy") == 1, "要消耗一颗"

        # 不在宝可梦中心时不能交换
        t3.data["location"] = "kanto-route-1"
        p._save(t3)
        ev4 = _Event("/交换 1")
        run_cmd(p, ev4, p.cmd_trade)
        assert "宝可梦中心" in "".join(ev4.outputs)


def test_learn_move_checks_learnset():
    """/学招 必须查可学表 —— 否则 Lv5 鲤鱼王都能学大字爆炎。"""
    from pw.dex import get_dex
    from pw.engine import create_pokemon

    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _fresh(tmp)
        t = p._load(ev)
        t.data["party"] = [create_pokemon("magikarp", 5).to_dict()]
        t.data["party"][0]["id"] = "m1"
        p._save(t)

        ev2 = _Event("/学招 1 大字爆炎")
        run_cmd(p, ev2, p.cmd_learn)
        assert "学不会" in "".join(ev2.outputs), "越级招式必须拒绝"
        assert "fireblast" not in p._load(ev2).party[0]["moves"]

        # 可学表里的招式应当能学(不能把功能本身封死)
        dex = get_dex()
        known = [
            str(x.get("move") or x.get("key") or "")
            for x in dex.learnable("magikarp", 30, include_tm=True, include_tutor=True)
        ]
        assert known, "鲤鱼王在 Lv30 应当有可学招式"
        # 找一个中文名能反查到的招式
        target = next(
            (m for m in known if (dex.moves.get(m) or {}).get("zh")), ""
        )
        if target:
            t = p._load(ev2)
            t.data["party"][0]["moves"] = ["splash", "tackle", "bounce", "flail"]
            p._save(t)
            zh = dex.moves[target]["zh"]
            ev3 = _Event(f"/学招 1 {zh} 替换 4")
            run_cmd(p, ev3, p.cmd_learn)
            assert "学不会" not in "".join(ev3.outputs), "".join(ev3.outputs)
            assert target in p._load(ev3).party[0]["moves"]


def test_item_evolution_respects_gender():
    """母奇鲁莉安不能用觉醒之石变艾路雷朵(也不能白扣石头)。"""
    from pw.engine import create_pokemon

    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _fresh(tmp)
        t = p._load(ev)
        mon = create_pokemon("kirlia", 20).to_dict()
        mon["gender"] = "F"
        mon["id"] = "m1"
        t.data["party"] = [mon]
        t.add_item("dawn-stone", 1)
        p._save(t)

        ev2 = _Event("/进化 1 觉醒之石")
        run_cmd(p, ev2, p.cmd_evolve)
        t2 = p._load(ev2)
        assert t2.party[0]["species"] == "kirlia", "性别不符不能进化"
        assert t2.count("dawn-stone") == 1, "失败不能消耗进化石"

        # 公的可以
        t2.party[0]["gender"] = "M"
        p._save(t2)
        ev3 = _Event("/进化 1 觉醒之石")
        run_cmd(p, ev3, p.cmd_evolve)
        assert p._load(ev3).party[0]["species"] == "gallade"


def test_auto_evolve_handles_multiple_steps():
    """一次大额经验要连续进化到最终形态(旧实现只进化一段,且 Lv100 后补不上)。"""
    from pw import growth
    from pw.engine import create_pokemon

    mon = create_pokemon("charmander", 5)
    growth.gain_exp(mon, 10 ** 7)
    assert mon.level == 100
    assert mon.species == "charizard", f"应直接进化到喷火龙,实际 {mon.species}"

    # 不能因为环状数据死循环
    mon2 = create_pokemon("eevee", 5)
    growth.gain_exp(mon2, 10 ** 6)
    assert mon2.species in {"eevee", "vaporeon", "jolteon", "flareon"}


def test_legendary_loss_marks_fled():
    """神兽逃跑/战败也要标记"今天惊动过",否则当天能无限重挑。"""
    from pw import legendary
    from pw.battle import TurnResult

    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _fresh(tmp)
        t = p._load(ev)
        site = legendary.sites_for("kanto")[0]
        t.data["location"] = site["location"]
        p._save(t)
        assert not legendary.fled_today(t, site["species"], 1)
        p._after_battle(
            t,
            {"kind": "legend", "title": "传说战", "legend": site},
            TurnResult(outcome="escaped", finished=True),
            1,
        )
        assert legendary.fled_today(t, site["species"], 1), "逃跑也要标记当天逃走"


def test_league_requires_story_progress():
    """主线必须按序:不能跳过章节直接通关联盟。"""
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _fresh(tmp)
        t = p._load(ev)
        t.data["location"] = "kanto-pokemon-league"
        for i in range(1, 9):
            t.add_badge("kanto", i)
        p._save(t)
        ev2 = _Event("/联盟 挑战")
        run_cmd(p, ev2, p.cmd_league)
        out = "".join(ev2.outputs)
        assert "主线还没推进到联盟" in out, out
        assert "当前章节" in out


def test_epilogue_stage_completes_on_legendary_catch():
    """尾声章节(第 6 章)必须真的能完成 —— 否则主线面板永远停在"当前目标"。"""
    from pw import legendary, story

    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _fresh(tmp)
        t = p._load(ev)
        epilogue = next(
            s for s in story.region_stages("kanto") if s.get("kind") == "epilogue"
        )
        t.data["location"] = epilogue["location"]
        # 把前面 5 章标为已完成 → 当前章节就是尾声
        for stage in story.region_stages("kanto"):
            if stage["key"] != epilogue["key"]:
                story.mark_stage(t, "kanto", stage["key"])
        p._save(t)
        assert story.current_stage(t)["key"] == epilogue["key"]

        site = next(
            s for s in legendary.sites_for("kanto") if s.get("location") == epilogue["location"]
        )
        text = p._after_battle(
            t,
            {"kind": "legend", "title": "传说战", "legend": site},
            turn_result_caught(),
            1,
        )
        assert "主线推进" in text, text
        assert epilogue["key"] in list(t.flag("story:kanto") or [])
        assert story.current_stage(t) is None, "尾声完成后本地区主线应全部完成"


def turn_result_caught():
    from pw.battle import TurnResult

    return TurnResult(outcome="caught", finished=True)


def test_alola_league_requirement_matches_actual_gate():
    """阿罗拉联盟章节写的门槛必须与 /联盟 的实际检查一致。"""
    from pw import story
    from pw.world import WorldMap

    world = WorldMap()
    stage = next(
        s for s in story.region_stages("alola") if s.get("kind") == "league"
    )
    assert int(stage["need"]) == len(world.gyms("alola")), (
        f"阿罗拉联盟需 {stage['need']} 枚,实际道馆/考验 {len(world.gyms('alola'))} 个"
    )
    assert str(len(world.gyms("alola"))) in stage["desc"]


def test_tournament_opponent_levels_use_real_levels():
    """大赛对手等级不该被强制拉成该轮上限。"""
    from pw import story

    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _fresh(tmp)
        t = p._load(ev)
        t.set_flag("champion:kanto", True)
        meta = story.tournament_meta(t, 0)
        lo, hi = story.TOURNAMENT_LEVELS[0]
        levels = [int(m["level"]) for m in meta["team"]]
        assert levels, "对手队伍不能为空"
        assert all(lo <= lv <= hi for lv in levels), levels
        # 关键:不能被**全部**拉成该轮上限(旧实现 clamp(hi, lo, hi) 恒等于 hi)
        assert not all(lv == hi for lv in levels), f"全被拉成上限 {hi}:{levels}"


def test_mantyke_needs_remoraid_in_party():
    """小球飞鱼必须有铁炮鱼才进化(旧实现无条件成立)。"""
    from pw import growth
    from pw.engine import create_pokemon

    alone = create_pokemon("mantyke", 50)
    growth.gain_exp(alone, 10 ** 6)
    assert alone.species == "mantyke"

    with_friend = create_pokemon("mantyke", 50)
    growth.gain_exp(with_friend, 10 ** 6, party=["remoraid"])
    assert with_friend.species == "mantine"


def test_level_100_still_allows_condition_evolutions():
    """满级不再涨经验,但仍应结算"条件进化",否则永久停在非最终形态。"""
    from pw import growth
    from pw.engine import create_pokemon

    mon = create_pokemon("mantyke", 100)
    growth.gain_exp(mon, 10 ** 7, party=["remoraid"])
    assert mon.species == "mantine"
    assert mon.level == 100


def test_second_gym_at_same_location_becomes_reachable():
    """P0 回归:同一地点的第二座道馆在拿到第一枚徽章后必须能挑战。

    否则该地区永远集不齐徽章 → 联盟打不了 → 冠军拿不到 → 下一个地区永久锁死。
    """
    with tempfile.TemporaryDirectory() as tmp:
        p, ev = _fresh(tmp)
        t = p._load(ev)
        t.data["location"] = "cianwood-city"
        t.data["region"] = "johto"    # region 是只读 property,要改 data
        p._save(t)

        # 没拿任何徽章 → 显示第一座(阿四 order 5)
        ev2 = _Event("/道馆")
        run_cmd(p, ev2, p.cmd_gym)
        assert "阿四" in "".join(ev2.outputs), "".join(ev2.outputs)

        # 拿到 order 5 的徽章后 → 应显示第二座(柳伯 order 7)
        t = p._load(ev2)
        t.add_badge("johto", 5)
        p._save(t)
        ev3 = _Event("/道馆")
        run_cmd(p, ev3, p.cmd_gym)
        out = "".join(ev3.outputs)
        assert "柳伯" in out, f"第二座道馆必须可达:{out}"

        # 两枚都拿到后 → 不再显示本地道馆
        t = p._load(ev3)
        t.add_badge("johto", 7)
        p._save(t)
        ev4 = _Event("/道馆")
        run_cmd(p, ev4, p.cmd_gym)
        assert "柳伯" not in "".join(ev4.outputs)


def test_image_mode_does_not_resend_full_text():
    """图片路径只带一行指令提示,不再把界面里的正文重发一遍。

    用户反馈:「/任务 这些在发送图片时还额外附加了相同内容的文本 有点多余」。
    """
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": True, "battle_image_scale": 2}
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        from pw import quests as Q

        t = p._load(ev)
        Q.roll_daily(t, 1)
        p._save(t)

        for cmd, fn in (
            ("/状态", p.cmd_status),
            ("/队伍", p.cmd_team),
            ("/背包", p.cmd_bag),
            ("/图鉴 新叶喵", p.cmd_dex),
            ("/任务", p.cmd_quest),
            ("/地图", p.cmd_map),
        ):
            ev2 = _Event(cmd)
            run_cmd(p, ev2, fn)
            assert len(ev2.outputs) == 1, f"{cmd} 输出了多条消息:{ev2.outputs}"
            joined = "".join(ev2.outputs)
            assert joined.startswith("<chain:"), f"{cmd} 没走图片路径:{joined[:60]}"
            # 图片 + 提示 ≤ 一行提示的量级;正文动辄几百字,一定超这个长度
            assert len(joined) < 200, f"{cmd} 疑似把正文也发了:{joined[:150]}"

        # 对照:关掉图片后必须是**完整正文**(说明内容没被删掉,只是不再重复发)
        p.config = {"ui_image": False}
        ev3 = _Event("/队伍")
        run_cmd(p, ev3, p.cmd_team)
        body = "".join(ev3.outputs)
        assert "<chain:" not in body and "招式:" in body and len(body) > 40


def test_battle_hint_shows_our_moves():
    """对战提示必须列出我方出战宝可梦的招式(用户:不然不知道技能)。"""
    from pw import battle as B
    from pw.engine import create_pokemon

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"battle_image": True, "battle_image_scale": 2, "quest_enable": False}
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        t = p._load(ev)
        t.data["region"] = "kanto"
        second = create_pokemon("pidgey", 3).to_dict()
        second["id"] = "m2"
        second["moves"] = ["tackle", "sandattack"]
        second["pp"] = {"tackle": 35, "sandattack": 15}
        t.data["party"].append(second)
        p._save(t)
        B.start(t, [{"species": "rattata", "level": 5}], kind="wild", wild=True,
                meta={"kind": "wild", "title": "野生宝可梦"}, day=1)
        p._save(t)

        ev2 = _Event("/对战 move 1")
        run_cmd(p, ev2, p.cmd_battle)
        hint = "".join(ev2.outputs)
        assert ev2.outputs[0].startswith("<chain:"), "应输出战斗图片"
        # 我方招式名 + 剩余 PP + 行动格式(招式数量随实际招式数走,
        # 修正 default_moveset 后低级宝可梦不再被硬补 TM 招)
        n_moves = len(p._load(ev2).party[0]["moves"])
        for token in ("招式:", "摇尾巴(", f"/对战 <1-{n_moves}>",
                      "switch", "item", "run"):
            assert token in hint, f"战斗提示缺少 {token}:{hint[:200]}"

        # 换人后提示要跟着换(出战第 2 只 → 波波的招式)
        ev3 = _Event("/对战 switch 2")
        run_cmd(p, ev3, p.cmd_battle)
        hint2 = "".join(ev3.outputs)
        assert "波波" in hint2 and "撞击(" in hint2, f"换人后提示没跟上:{hint2[:200]}"

        # 关掉图片 → 文本回退里既有血条也有招式,且不重复发正文
        p.config = {"battle_image": False, "quest_enable": False}
        ev4 = _Event("/对战 move 1")
        run_cmd(p, ev4, p.cmd_battle)
        assert len(ev4.outputs) == 1, f"文本回退不该发多条:{ev4.outputs}"
        body = "".join(ev4.outputs)
        assert "HP " in body and "招式:" in body


def test_battle_turn_also_sends_full_log_as_text():
    """战斗画面的对话框只放得下 4 行,完整战报要用文本补一遍。

    用户:"对战时的战报可以加回纯文本 避免过长导致省略一部分"。
    """
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": True, "battle_image": True, "battle_image_scale": 2,
                    "quest_enable": False}
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        t = p._load(ev)
        t.data["region"] = "kanto"
        p._save(t)
        from pw import battle as B
        from pw.util import game_day

        B.start(t, [{"species": "pidgey", "level": 6}], kind="wild", wild=True,
                meta={"kind": "wild", "title": "野生的波波"}, day=game_day())
        p._save(t)
        ev2 = _Event("/对战 move 2")      # 用有威力的招,保证有战报
        run_cmd(p, ev2, p.cmd_battle)
        text = "".join(ev2.outputs)
        assert text.startswith("<chain:"), "应输出战斗画面"
        # 战报行(· 开头)必须出现在文本里,而不是只画在图里
        log_lines = [x for x in text.split("\n") if x.strip().startswith("·")]
        assert log_lines, f"没有附带战报文本:{text[:200]}"
        assert "招式:" in text, "我方招式提示也要留着"


def test_image_hints_keep_info_the_image_does_not_draw():
    """图片路径的提示要保留"图里没画"的信息。

    用户反馈:"之前只是让你去掉图片里已有的重复纯文本内容,但你把额外的信息
    也给弄没了" —— 典型是 `/地图` 的相邻地点(带危险度)和城镇服务。
    """
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": True, "battle_image_scale": 2, "quest_enable": False}
        ev = _Event("/开始 小智 新叶喵")
        run_cmd(p, ev, p.cmd_start)
        t = p._load(ev)
        t.data["location"] = "pewter-city"      # 有宝可梦中心/商店/道馆的城镇
        t.data["visited"] = ["pallet-town", "viridian-city", "pewter-city"]
        p._save(t)

        # 地图:相邻地点 + 危险度 + 本地服务
        ev2 = _Event("/地图")
        run_cmd(p, ev2, p.cmd_map)
        hint = "".join(ev2.outputs)
        assert "相邻地点" in hint, hint[:200]
        assert "危险度" in hint, hint[:200]
        assert "本地服务" in hint, hint[:200]
        assert "宝可梦中心" in hint, hint[:200]
        # 飞行必须写成真实指令(以前写成了不存在的 `/飞行`)
        assert "/前往 飞行" in hint and "/飞行 <" not in hint, hint[:200]

        # 状态卡:图片没画危险度/道具件数/今日事件
        ev3 = _Event("/状态")
        run_cmd(p, ev3, p.cmd_status)
        card = "".join(ev3.outputs)
        assert "危险度" in card and "道具" in card, card[:200]
        assert "没有在战斗" in card, card[:200]

        # 图鉴:图片只写"野外分布:已收录",进化信息要留在文本里
        ev4 = _Event("/图鉴 新叶喵")
        run_cmd(p, ev4, p.cmd_dex)
        dex = "".join(ev4.outputs)
        assert "可进化为" in dex, dex[:200]
