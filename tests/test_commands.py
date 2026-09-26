"""指令层集成测试:把 PokemonWorldPlugin 的所有方法绑到一个假对象上,
用假 event 跑完整指令流(不启动 AstrBot,不联网,不调用 LLM)。

这是唯一能一次性验证「指令 → 内核 → 存档」整条链路的测试。
"""

import asyncio
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
from pw.world import WorldMap  # noqa: E402
from pw.worldstate import WorldStore  # noqa: E402


class _Cmd:
    """只绑定方法、不执行 __init__ 的宿主对象。"""

    def __init__(self, tmp):
        self.trainers = TrainerStore(tmp)
        self.worlds = WorldStore(tmp)
        self.config = None  # → _cfg 全部走默认值(provider_id 为空 = 不调用 LLM)
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

        # /状态
        ev = _Event("/状态")
        run_cmd(p, ev, p.cmd_status)
        assert any("训练家档案" in x for x in ev.outputs)
        assert any("真新镇" in x or "出发" in x or "危险度" in x for x in ev.outputs)

        # /队伍
        ev = _Event("/队伍")
        run_cmd(p, ev, p.cmd_team)
        assert any("新叶喵" in x for x in ev.outputs)
        assert any(x.startswith("<chain") or "队伍" in x for x in ev.outputs)

        # /地图
        ev = _Event("/地图")
        run_cmd(p, ev, p.cmd_map)
        assert any("真新镇" in x for x in ev.outputs)

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
        ev = _Event("/今日")
        run_cmd(p, ev, p.cmd_today)
        joined = "".join(ev.outputs)
        assert "游戏日" in joined or "第" in joined

        # /探索:反复探索直到开战或拿到道具
        started = False
        for _ in range(12):
            ev = _Event("/探索")
            run_cmd(p, ev, p.cmd_explore)
            joined = "".join(ev.outputs)
            if "⚔️" in joined:
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

        # /对战 <招式序号>:一直打到结束(输了就自动换人/治疗,循环结束)
        finished = False
        for _ in range(30):
            t = p._load(_Event())
            if not t.party:
                break
            ev = _Event("/对战 1")
            run_cmd(p, ev, p.cmd_battle)
            joined = "".join(ev.outputs)
            if any(k in joined for k in ("战斗胜利", "战斗失败", "捕获成功", "脱离了战斗", "你认输了")):
                finished = True
                break
            if "必须换人" in joined or "全部失去战斗能力" in joined:
                break
        assert finished or not p._load(_Event()).data.get("battle")

        # /图鉴
        ev = _Event("/图鉴 新叶喵")
        run_cmd(p, ev, p.cmd_dex)
        assert any("新叶喵" in x for x in ev.outputs)

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

        ev = _Event("/商店")
        run_cmd(p, ev, p.cmd_shop)
        assert any("商店" in x and "余额" in x for x in ev.outputs)

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

        ev = _Event("/道馆")
        run_cmd(p, ev, p.cmd_gym)
        assert any("小刚" in x for x in ev.outputs)

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

        # /主线 面板
        ev = _Event("/主线")
        run_cmd(p, ev, p.cmd_story)
        joined = "".join(ev.outputs)
        assert ("主线" in joined and "敌对组织" not in joined) or "主线" in joined
        assert "当前目标" in joined

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
        ev = _Event("/神兽")
        run_cmd(p, ev, p.cmd_legend)
        assert any("传说" in x for x in ev.outputs)
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
        ev = _Event("/神兽 挑战 急冻鸟")
        run_cmd(p, ev, p.cmd_legend)
        assert p._load(ev).data.get("battle"), ev.outputs
        assert any("传说的宝可梦" in x for x in ev.outputs)

        # /大赛 未夺冠 → 拒绝
        ev = _Event("/大赛")
        run_cmd(p, ev, p.cmd_tournament)
        assert any("冠军" in x for x in ev.outputs)

        # 夺冠后 → 面板 + 挑战
        t = p._load(ev)
        t.set_flag("champion:kanto", True)
        p._save(t)
        ev = _Event("/大赛")
        run_cmd(p, ev, p.cmd_tournament)
        assert any("世界大赛" in x for x in ev.outputs)
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
            # 图片卡片里也应夹带原来的文字说明
            assert len(joined) > 10
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
