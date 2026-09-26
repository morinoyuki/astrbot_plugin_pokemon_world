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
        self.outputs.append(f"<chain:{len(comps)}>")
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
