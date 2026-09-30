"""地区通关审计 + 世界地图:每次重建 maps.json 都要保证 8 个地区走得通。

主线上最容易悄悄坏掉的东西不是数值,而是**地图连接**:
`tools/build_map_graph.py` 重排节点、或手改 maps.json 的邻接/危险度,
都可能让"第 N 个道馆走不到""冠军之路进不去",而玩家要到那一关才会发现。
自检 ⑦ 会在管理页报警,这里把它固化成回归测试。
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections import deque

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from test_commands import _Cmd, _Event, run_cmd  # noqa: E402

from pw.world import REGION_ORDER, WorldMap  # noqa: E402


def _reachable(world: WorldMap, start: str, dest: str, cap: int) -> bool:
    """在"危险度不超过 cap"的前提下从 start 走到 dest(BFS)。"""
    if not start or not dest or start not in world._index or dest not in world._index:
        return False
    seen = {start}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == dest:
            return True
        for nxt in world.neighbors(node):
            if nxt not in seen and world.tier(nxt) <= cap:
                seen.add(nxt)
                queue.append(nxt)
    return dest in seen


def test_every_region_can_beat_all_gyms_and_reach_the_league():
    """每个地区:按顺序拿满徽章 → 走到联盟。任何一环断了都算没有通关。"""
    world = WorldMap()
    failures: list[str] = []
    cleared = 0
    for region in REGION_ORDER:
        nodes = world.nodes(region)
        gyms = world.gyms(region)
        if not nodes or not gyms:
            continue          # 占位地区(帕底亚)不要求
        cur = world.start_location(region)
        assert cur in nodes, f"{region} 起点 {cur!r} 不在图中"
        badges: list[str] = []
        for order in range(1, len(gyms) + 1):
            gym = next((g for g in gyms if int(g.get("order", 0)) == order), None)
            assert gym, f"{region} 缺第 {order} 个道馆"
            dest = str(gym.get("location") or "")
            cap = max(world._route_cap(region, cur, badges), world.tier(dest))
            if not _reachable(world, cur, dest, cap):
                failures.append(f"{region} 第 {order} 馆走不到({cur}→{dest})")
                break
            badges = [*badges, f"{region}:{order}"]
            cur = dest
        else:
            gateway = world.gateway(region)
            cap = max(world._route_cap(region, cur, badges), world.tier(gateway))
            if not _reachable(world, cur, gateway, cap):
                failures.append(f"{region} 联盟走不到({cur}→{gateway})")
            else:
                cleared += 1
    assert not failures, "; ".join(failures)
    # 帕底亚这类"有野生分布、还没排道馆"的地区不算通关门(有馆的地区才要求全通)
    with_gyms = [r for r in world.regions_with_data() if world.gyms(r)]
    assert cleared == len(with_gyms) >= 8, f"只有 {cleared}/{len(with_gyms)} 个地区可通关"


def test_region_chain_starts_and_gateways_exist():
    """地区链完整:起点/联盟节点都在图里,顺序与 REGION_ORDER 一致。"""
    world = WorldMap()
    regions = world.regions_with_data()
    assert regions[0] == "kanto"
    assert regions == [r for r in REGION_ORDER if r in regions]
    for region in regions:
        start = world.start_location(region)
        assert start and start in world.nodes(region), f"{region} 起点缺失"
        gateway = world.gateway(region)
        assert gateway and gateway in world.nodes(region), f"{region} 联盟节点缺失"
        assert world.node_zh(start) and world.node_zh(gateway)
        # 冠军旗标是解锁下一地区的钥匙,旗标名必须和地区 slug 一致
        assert region == region.lower()


def test_kanto_progression_order_is_canonical():
    """关都主线顺序必须贴近正作,且七之岛这类支线在主线城镇之后。

    重建 maps.json 时最容易犯的错:把"遭遇等级低"的海岛支线插到
    深灰市前面 —— 玩家会看到主线里冒出一串七之岛。
    """
    world = WorldMap()
    order = {k: int(v.get("order", 0)) for k, v in world.nodes("kanto").items()}
    seq = ["pallet-town", "viridian-city", "pewter-city", "cerulean-city",
           "vermilion-city", "celadon-city", "saffron-city", "fuchsia-city",
           "cinnabar-island"]
    present = [k for k in seq if k in order]
    ranks = [order[k] for k in present]
    assert ranks == sorted(ranks), f"城镇顺序错乱:{list(zip(present, ranks, strict=True))}"
    last_town = max(ranks)
    for side in ("one-island", "four-island", "five-island", "three-isle-port",
                 "water-labyrinth", "kanto-altering-cave", "memorial-pillar",
                 "tanoby-ruins", "navel-rock"):
        if side in order:
            assert order[side] > last_town, f"支线 {side} 插进了主线中间"
    # 主线必须收在联盟上(联盟是最后节点)
    assert order["kanto-pokemon-league"] == max(order.values())


def test_world_map_command_lists_region_progress():
    """/地图 世界 要能报出全部地区的开放/通关状态。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        p.config = {"ui_image": False, "quest_enable": False}
        run_cmd(p, _Event("/开始 小智 新叶喵"), p.cmd_start)
        ev = _Event("/地图 世界")
        run_cmd(p, ev, p.cmd_map)
        out = "\n".join(ev.outputs)
        assert "世界地图" in out
        assert "关都" in out and "城都" in out and "伽勒尔" in out
        assert "已开放" in out and "未开放" in out
        assert "需先成为关都冠军" in out      # 城都的解锁条件
        assert "你在这里" in out              # 当前地区标记


def test_render_world_map_handles_states_and_empty():
    from pw import ui_menu as M

    entries = [
        {"key": "kanto", "zh": "关都", "order": 1, "unlocked": True,
         "champion": True, "badges": 8, "gyms": 8, "current": False,
         "next_zh": "联盟(四天王/冠军)", "prev_zh": ""},
        {"key": "johto", "zh": "城都", "order": 2, "unlocked": True,
         "champion": False, "badges": 2, "gyms": 8, "current": True,
         "next_zh": "枯叶市", "prev_zh": "关都"},
        {"key": "hoenn", "zh": "丰缘", "order": 3, "unlocked": False,
         "champion": False, "badges": 0, "gyms": 8, "current": False,
         "next_zh": "卡那兹市", "prev_zh": "城都"},
    ]
    data = M.render_world_map(entries, scale=2)
    assert data.startswith(b"\x89PNG")
    assert len(data) > 2000
    # 空输入不能炸(渲染层约定:失败返回 b"")
    empty = M.render_world_map([], scale=2)
    assert empty == b"" or empty.startswith(b"\x89PNG")


def test_weather_change_and_expiry_are_announced():
    """天气变化/结束必须有提示:开始时的由来、结束时的“风停了”。"""
    import tempfile

    from test_commands import _Cmd, _Event, run_cmd

    from pw.engine import Battle, Side, create_pokemon

    # ① 开场带异常天气 → 战报第一行说明由来
    tmp = tempfile.TemporaryDirectory()
    with tmp:
        p = _Cmd(tmp.name)
        p.config = {"ui_image": False}
        run_cmd(p, _Event("/开始 小智 杰尼龟"), p.cmd_start)
        t = p._load(_Event())
        st = p._state(t.scope)
        st.add_event({
            "id": "w1", "kind": "weather", "region": t.region, "location": t.location,
            "effects": {"battle_weather": "sand"},
            "created_day": st.day, "until_day": st.day + 1,
        })
        try:                        # 事件生效要重算一次修正表
            st._recompute_modifiers()
        except TypeError:
            st._recompute_modifiers(st.day)
        p.worlds.save(st.scope, st.data)
        # 事件里的 battle_weather 会进 modifiers(插件开战时就是读它)
        assert p._state(t.scope).modifiers.get("battle_weather") == "sand"
        weather = "sand"
        from pw import battle as B

        fresh = p._load(_Event())
        lines = B.start(fresh, [{"species": "pikachu", "level": 10}],
                        kind="wild", weather=weather, day=1)
        assert any("沙暴" in line for line in lines), lines
    # ② 永久天气(开场/招式改的)不会自己停;非永久天气到期才报“风停了”
    bt = Battle(player=Side("player", [create_pokemon("snorlax", 30)]),
                enemy=Side("enemy", [create_pokemon("snorlax", 30)]), weather="sand")
    bt.start()
    logs: list[str] = []
    for _ in range(8):
        logs += bt.step({"type": "move", "move": "tackle"},
                        {"type": "move", "move": "tackle"})
    assert bt.weather == "sand", "开场天气应该一直维持到战斗结束"
    assert bt.weather_permanent and bt.weather_turns >= 1
    assert not any("风停了" in x for x in logs), logs[-6:]

    # 招式改天气同样是永久的(用户要求)
    bt2 = Battle(player=Side("player", [create_pokemon("hippowdon", 30)]),
                 enemy=Side("enemy", [create_pokemon("pikachu", 30)]))
    bt2.start()
    turns = [{"type": "move", "move": "sandstorm"}, {"type": "move", "move": "tackle"}]
    for _ in range(6):
        if bt2.finished:
            break
        bt2.step(turns[0], turns[1]) if (bt2.player.mon.pp.get("sandstorm") or 0) > 0 \
            else bt2.step(turns[1], turns[1])
    assert bt2.weather == "sand" and bt2.weather_permanent, (bt2.weather, bt2.weather_turns)

    # 非永久天气(道具/技能给的临时天气)到期仍有提示
    bt3 = Battle(player=Side("player", [create_pokemon("snorlax", 30)]),
                 enemy=Side("enemy", [create_pokemon("snorlax", 30)]))
    bt3.start()
    bt3.weather, bt3.weather_turns, bt3.weather_permanent = "rain", 1, False
    out = bt3.step({"type": "move", "move": "tackle"},
                   {"type": "move", "move": "tackle"})
    assert not bt3.weather and any("风停了" in x for x in out), out

def test_consecutive_same_weather_events_are_rotated():
    """连续两天同一种天气异常 → 自动轮换(不再老是沙暴)。"""
    from pw.worldstate import WorldState

    st = WorldState({}, "g1")
    ev1 = {"kind": "weather", "region": "kanto", "location": "kanto-route-1",
           "title": "沙暴来袭", "desc": "沙暴笼罩了这一带。",
           "effects": {"battle_weather": "sand"}}
    st.add_event(ev1)
    ev2 = {"kind": "weather", "region": "kanto", "location": "kanto-route-2",
           "title": "沙暴来袭", "desc": "沙暴笼罩了这一带。",
           "effects": {"battle_weather": "sand"}}
    st.add_event(ev2)
    got = (st.data["events"][-1]["effects"] or {}).get("battle_weather")
    assert got != "sand", "连着两天沙暴应该被轮换成别的"
    assert "沙暴" not in st.data["events"][-1]["desc"], st.data["events"][-1]
    assert st.data["events"][0]["effects"]["battle_weather"] == "sand"


def test_weather_move_overrides_event_weather_without_speed_priority():
    """改天气的招式:直接**覆盖**事件/主场天气,但不抢先手(用户澄清)。"""
    from pw.engine import Battle, Side, create_pokemon

    # ① 覆盖:开场是事件带来的沙暴,雨舞之后变下雨,且之后一直维持
    me = create_pokemon("blastoise", 50)
    me.moves = ["raindance", "surf"]
    me.pp = {"raindance": 10, "surf": 15}
    foe = create_pokemon("charizard", 50)
    foe.moves = ["tackle"]
    foe.pp = {"tackle": 30}
    foe.stats["spe"] = 200              # 对手更快,先出手
    bt = Battle(player=Side("player", [me]), enemy=Side("enemy", [foe]), weather="sand")
    bt.start()
    out = bt.step({"type": "move", "move": "raindance"},
                  {"type": "move", "move": "tackle"})
    assert bt.weather == "rain", (bt.weather, out)
    assert any("覆盖了原来的沙暴" in x for x in out), out
    # 先手顺序不变:对手的攻击在前,改天气在后(说明没有速度优先)
    i_hit = next(i for i, x in enumerate(out) if "击中" in x)
    i_weather = next(i for i, x in enumerate(out) if "天气变成了" in x)
    assert i_hit < i_weather, out
    # ② 维持:再打 6 回合天气还在
    for _ in range(6):
        bt.step({"type": "move", "move": "surf"}, {"type": "move", "move": "tackle"})
        if bt.finished:
            break
    assert bt.weather == "rain", bt.weather


def test_weather_event_rotates_across_days_even_after_expiry():
    """连着几天都写同一种天气异常 → 必须跨天轮换(过期事件不在 active 里,旧逻辑漏判)。

    实测反馈:「一直是沙暴 已经两天」—— 每天一条、当天过期,旧比较对象就找不到了,
    所以要把上一种记进存档再比。
    """
    from pw.worldstate import WEATHER_ZH_LITE, WorldState

    zh2key = {v: k for k, v in WEATHER_ZH_LITE.items()}
    st = WorldState({"day": 1}, "test:GroupMessage:g1")

    def _ev(zh: str, day: int) -> dict:
        return {"id": f"w{day}", "kind": "weather",
                "title": f"【关都·1号道路】{zh}来袭",
                "desc": f"{zh}笼罩了这一带。",
                "effects": {"battle_weather": zh2key[zh]},
                "created_day": day, "until_day": day}

    seen = []
    for day, zh in enumerate(["沙暴", "沙暴", "沙暴"], 1):
        st.data["day"] = day
        st.expire(day)
        st.add_event(_ev(zh, day))
        got = next(x for x in st.data["events"] if x["id"] == f"w{day}")
        seen.append(got["effects"]["battle_weather"])
    assert seen[1] != seen[0], f"第二天应该换成别的天气:{seen}"
    assert seen[2] != seen[1], f"第三天也要换:{seen}"
    # 标题里的天气名要跟着换,不能还写着“沙暴来袭”而实际是下雪
    last = next(x for x in st.data["events"] if x["id"] == "w3")
    assert WEATHER_ZH_LITE[last["effects"]["battle_weather"]] in last["title"], last["title"]


def test_no_two_locations_share_the_same_display_name():
    """同地区内不允许重名节点 —— 重名会让 `/前往 <名字>` 指到另一个点,玩家会卡关。

    实测反馈:「存在同名节点 导致卡关」(冠军之路 / 阿斯卡纳石室 / 吹寄洞穴…)。
    """
    import collections
    import json
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "pw", "static", "locations.json"), encoding="utf-8") as f:
        raw = json.load(f)
    nodes = raw["locations"] if isinstance(raw, dict) and "locations" in raw else raw
    items = list(nodes.items()) if isinstance(nodes, dict) else [(v.get("id"), v) for v in nodes]

    by_name = collections.defaultdict(list)
    for key, node in items:
        if isinstance(node, dict) and node.get("zh"):
            by_name[(str(node.get("region")), str(node["zh"]))].append(key)
    dup = {f"{r}·{zh}": ks for (r, zh), ks in by_name.items() if len(ks) > 1}
    assert not dup, f"同地区存在同名地点:{dup}"

    # 名字解析必须**限定在当前地区** —— 跨地区同名是合法的(关都和丰缘都有「变幻洞窟」),
    # 但 `/前往 <名字>` 必须解析到**当前地区**的那个节点,否则会“指到别的地区”而卡关。
    world = WorldMap()
    for region in world.regions_with_data():
        for key in world.nodes(region):
            zh = world.node_zh(key)
            got = world.find_location(zh, region)
            assert got == key or world.node_zh(got) == zh, (
                f"[{region}] {zh} 解析到了别的节点:{got}")
