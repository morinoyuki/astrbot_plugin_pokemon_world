"""宝可梦世界:城镇地图 / 商店 / 道馆 / 联盟 / 主线 五个菜单界面(仿 GBA)。

沿用 `ui_render.py` 的 `Screen`(240×160 逻辑画布 + 放大文字层),
配色、窗口框、徽章、道具图标、属性色一律复用共享工具箱,不重复实现。

所有函数都**不会抛异常**:内部整体 try/except,失败时 `logger.debug` 并返回 `b""`,
调用方回退纯文本 —— 渲染永远不该中断游戏。
"""

from __future__ import annotations

from itertools import pairwise

from astrbot.api import logger

from .ui_render import (
    BOX_EDGE,
    BOX_FILL,
    BOX_HI,
    SCALE_DEFAULT,
    TEXT,
    TEXT_DIM,
    Screen,
    type_color,
)

# ── 本模块补充的少量配色 ─────────────────────────────────────────
MAP_LAND = (234, 246, 226)
MAP_SEA = (178, 216, 230)
ROUTE_LINE = (198, 192, 152)
DOT_VISITED = (72, 152, 72)
DOT_EMPTY = (252, 251, 238)
PIN_RED = (224, 64, 56)
PIN_EDGE = (128, 34, 28)
DONE_GREEN = (60, 152, 72)
GOLD = (214, 160, 56)
DISCOUNT_RED = (208, 72, 64)
STATUS_OFF = (166, 162, 138)
STORY_KIND = {"boss": "首领", "event": "事件", "main": "主线", "gym": "道馆"}


def _to_int(v, default: int = 0) -> int:
    """宽松转 int:非法值返回默认值(数据字段可能缺失或为字符串)。"""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return default


def _fit(sc: Screen, s: str, max_w: float, size: float) -> str:
    """按宽度截断字符串,超出时以 ascii 省略号收尾(避免缺字字形)。"""
    s = str(s or "")
    if sc.tw(s, size) <= max_w:
        return s
    while s and sc.tw(s + "..", size) > max_w:
        s = s[:-1]
    return (s + "..") if s else ".."


def _lighten(c: tuple[int, int, int], d: int = 70) -> tuple[int, int, int]:
    return tuple(min(255, int(v) + d) for v in c)  # type: ignore[return-value]


def _type_chip(sc: Screen, x: float, y: float, t: str, *, size: float = 7.4) -> float:
    """属性色小标签,返回宽度。"""
    label = str(t or "?")
    w = sc.tw(label, size) + 8
    sc.d.rounded_rectangle([x, y, x + w, y + 10], radius=2,
                           fill=type_color(label), outline=BOX_EDGE)
    sc.text(x + 4, y + 1.3, label, size=size, fill=(255, 255, 250))
    return w


def _state_glyph(done: bool, current: bool) -> tuple[str, tuple[int, int, int]]:
    """✅/⬜ 在本内置字体里是豆腐块,统一用安全字形 ●/◆/○ 表达三态。"""
    if done:
        return "●", DONE_GREEN
    if current:
        return "◆", PIN_RED
    return "○", STATUS_OFF


def _chain_keys(nodes: list[dict]) -> tuple[list[str], dict[str, list[str]]]:
    """布局顺序:优先用数据自带的 `order`(即真实的地区推进序)。

    之前只按 `next` 做深度游走,会先钻进支线(viridian-forest、pattern-bush、
    ss-anne…),导致真正的城镇被挤到 20 名之外 —— 结果"你在这里"的红色图钉
    根本画不出来。`order` 是地图数据里严格的推进序号,按它排最贴近正作的
    城镇地图顺序。
    """
    keys = [str(n.get("key") or "") for n in nodes]
    keyset = {k for k in keys if k}
    nxt: dict[str, list[str]] = {}
    for n in nodes:
        k = str(n.get("key") or "")
        nxt[k] = [str(x) for x in (n.get("next") or []) if str(x) in keyset]
    orders = {}
    for n in nodes:
        k = str(n.get("key") or "")
        try:
            orders[k] = int(n.get("order"))
        except (TypeError, ValueError):
            orders[k] = None
    if orders and all(v is not None for v in orders.values()):
        ordered = sorted(keyset, key=lambda k: (orders[k], k))
        return ordered, nxt

    indeg = dict.fromkeys(keyset, 0)
    for outs in nxt.values():
        for o in outs:
            indeg[o] = indeg.get(o, 0) + 1
    start = next((k for k in keys if k in keyset and indeg.get(k, 0) == 0), "")
    order: list[str] = []
    seen: set[str] = set()
    cur = start
    while cur and cur not in seen:
        seen.add(cur)
        order.append(cur)
        outs = nxt.get(cur) or []
        cur = outs[0] if outs else ""
    order.extend(k for k in keys if k in keyset and k not in seen)
    return order, nxt


# ══════════════════════════════════════════════════════════════════
# 1. 镇地图(RSE/FRLG 风格)
# ══════════════════════════════════════════════════════════════════
def render_map(
    region_zh: str,
    nodes: list[dict],
    *,
    current: str,
    visited: list[str],
    gyms: list[dict] | None = None,
    next_goal: str = "",
    region_order: int = 0,
    scale: int = SCALE_DEFAULT,
) -> bytes:
    """城镇地图:左上是节点地图(按 next 邻接排布),左下城镇列表,右侧资料栏。"""
    try:
        sc = Screen(scale=scale)
        nodes = [dict(n) for n in (nodes or []) if isinstance(n, dict)]
        visited_set = {str(v) for v in (visited or [])}
        gym_locs = {str(g.get("location") or "") for g in (gyms or []) if isinstance(g, dict)}
        region = str(region_zh or "?")
        cur_key = str(current or "")

        sc.title_bar("镇地图", right=region)
        MAP = (5, 19, 150, 108)
        TOWN = (5, 111, 150, 144)
        INFO = (153, 19, 235, 144)
        sc.window(MAP, radius=2)
        sc.window(TOWN, radius=2)
        sc.window(INFO, radius=2)

        mx0, my0, mx1, my1 = MAP
        # 地图底色:陆地 + 两片海,做出"手绘地图"的观感
        sc.d.rounded_rectangle([mx0 + 3, my0 + 3, mx1 - 3, my1 - 3], radius=2,
                               fill=MAP_LAND)
        sc.d.ellipse([mx0 + 2, my0 + 2, mx0 + 34, my0 + 16], fill=MAP_SEA)
        sc.d.ellipse([mx1 - 34, my0 + 2, mx1 - 2, my0 + 16], fill=MAP_SEA)

        order, nxt = _chain_keys(nodes)
        pos: dict[str, tuple[float, float]] = {}
        cols = 5
        # 3 行 × 5 列:第 4 行的节点名会压到下面的"地点"框上,所以只画 3 行
        shown = list(order[: cols * 3])
        if cur_key and cur_key not in shown and nodes:
            shown[-1] = cur_key  # 当前位置必须可见,宁可不显示最后一个节点
        rows = max(1, (len(shown) + cols - 1) // cols)
        cw = (mx1 - mx0 - 8) / cols
        # 每个节点占「标记 + 下方标签」两块,共 14 逻辑像素;ch 必须按"标记块"分配,
        # 否则最后一行(y+6.5 起画的标签)会越过面板底边 —— 实测过 108.7 > 108。
        node_h = 14.0
        ch = (my1 - my0 - 7) / rows
        for i, key in enumerate(shown):
            row, col = divmod(i, cols)
            if row % 2 == 1:  # 蛇形换行:相邻节点接在正下方,路线不会横穿整张图
                col = cols - 1 - col
            gy = my0 + 4 + row * ch + max(0.0, (ch - node_h) / 2)
            pos[key] = (mx0 + 4 + (col + 0.5) * cw, gy)

        # 路线连线(沿主线顺序 + 显式 next 边)
        drawn: set[tuple[str, str]] = set()
        edges: list[tuple[str, str]] = []
        edges.extend(pairwise(order))
        for a, outs in nxt.items():
            edges.extend((a, b) for b in outs)
        for a, b in edges:
            if a not in pos or b not in pos or (a, b) in drawn or (b, a) in drawn:
                continue
            drawn.add((a, b))
            ax, ay = pos[a]
            bx, by = pos[b]
            sc.d.line([ax, ay, bx, by], fill=ROUTE_LINE, width=2)

        # 节点标记
        for key, (x, y) in pos.items():
            is_vis = key in visited_set
            is_gym = key in gym_locs
            if is_gym:
                sc.badge(x - 3.5, y - 3.5, 7, on=is_vis)
            elif is_vis:
                sc.d.ellipse([x - 2.6, y - 2.6, x + 2.6, y + 2.6], fill=DOT_VISITED,
                             outline=BOX_EDGE)
            else:
                sc.d.ellipse([x - 2.6, y - 2.6, x + 2.6, y + 2.6], fill=DOT_EMPTY,
                             outline=BOX_EDGE)

        # 当前节点:红白图钉 + 脉冲环(画在最上层)
        if cur_key in pos:
            x, y = pos[cur_key]
            sc.d.ellipse([x - 6, y - 6, x + 6, y + 6], outline=(248, 168, 158))
            sc.d.ellipse([x - 4.2, y - 4.2, x + 4.2, y + 4.2], outline=PIN_RED)
            sc.d.ellipse([x - 3, y - 12, x + 3, y - 6], fill=PIN_RED, outline=PIN_EDGE)
            sc.d.ellipse([x - 1.2, y - 10.6, x + 1.2, y - 8.2], fill=(250, 250, 245))
            sc.d.polygon([(x - 2.4, y - 7), (x + 2.4, y - 7), (x, y - 2.6)],
                         fill=PIN_RED, outline=PIN_EDGE)

        # 节点名(放在标记下方)
        for key, (x, y) in pos.items():
            node = next((n for n in nodes if str(n.get("key")) == key), {})
            label = _fit(sc, str(node.get("zh") or key), cw - 2, 6.4)
            color = PIN_RED if key == cur_key else (TEXT if key in visited_set else TEXT_DIM)
            # 硬钳制:标签墨迹(约 7px)必须留在面板内
            ly = min(y + 6.5, my1 - 8.5)
            sc.text_center(x, ly, label, size=6.4, fill=color)

        if not nodes:
            sc.text(14, 42, "暂无地图数据。", size=8.5, fill=TEXT_DIM)

        # 左下:城镇清单(最多 6 条,3 列 × 2 行)
        sc.text(9, 112.5, "地点", size=7, fill=TEXT_DIM)
        if len(order) > len(shown):
            sc.text_right(146, 112.5, f"图中前 {len(shown)}/{len(order)} 处", size=7,
                          fill=TEXT_DIM)
        disp: list[tuple[str, tuple[int, int, int]]] = []
        for key in order[:6]:
            node = next((n for n in nodes if str(n.get("key")) == key), {})
            label = _fit(sc, str(node.get("zh") or key), 44, 7)
            color = PIN_RED if key == cur_key else (TEXT if key in visited_set else TEXT_DIM)
            disp.append((label, color))
        if len(order) > 6:
            disp = [*disp[:5], (f"+{len(order) - 5}", TEXT_DIM)]
        for i, (label, color) in enumerate(disp):
            col, row = i % 3, i // 3
            sc.text(9 + col * 46, 123 + row * 9, label, size=7, fill=color)

        # 右侧:地区 / 排名 / 当前位置 / 下一目标 / 图例
        cx = (INFO[0] + INFO[2]) / 2
        sc.text_center(cx, 23, _fit(sc, region, 76, 10.5), size=10.5, fill=TEXT)
        sc.text_center(cx, 38, f"第{int(region_order or 1)}地区", size=7.6, fill=TEXT_DIM)
        sc.d.line([INFO[0] + 5, 47, INFO[2] - 5, 47], fill=BOX_HI)
        sc.text(158, 50, "当前位置", size=7, fill=TEXT_DIM)
        cur_zh = ""
        for n in nodes:
            if str(n.get("key")) == cur_key:
                cur_zh = str(n.get("zh") or cur_key)
        sc.text(158, 59, _fit(sc, cur_zh or "未知", 72, 8.8), size=8.8, fill=PIN_RED)
        sc.text(158, 72, "下一目标", size=7, fill=TEXT_DIM)
        for i, ln in enumerate(sc.wrap(next_goal or "自由探索", 72, size=7.8, limit=2)):
            sc.text(158, 81 + i * 9, ln, size=7.8, fill=TEXT)
        sc.d.line([INFO[0] + 5, 100, INFO[2] - 5, 100], fill=BOX_HI)
        legend = [("current", "当前位置"), ("visited", "已到过"),
                  ("empty", "未到过"), ("gym", "道馆城镇")]
        for i, (kind, label) in enumerate(legend):
            ly = 104 + i * 9.5
            if kind == "current":
                sc.d.ellipse([160, ly + 1, 166, ly + 7], outline=PIN_RED)
                sc.d.ellipse([162, ly + 3, 164, ly + 5], fill=PIN_RED)
            elif kind == "visited":
                sc.d.ellipse([160, ly + 1, 166, ly + 7], fill=DOT_VISITED, outline=BOX_EDGE)
            elif kind == "empty":
                sc.d.ellipse([160, ly + 1, 166, ly + 7], fill=DOT_EMPTY, outline=BOX_EDGE)
            else:
                sc.badge(159, ly, 8, on=True)
            sc.text(172, ly + 0.5, label, size=7.4, fill=TEXT)

        sc.footer("◆ 沿路线前进 · 道馆城镇用金色徽章标出")
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 城镇地图渲染失败: %s", e)
        return b""


# ══════════════════════════════════════════════════════════════════
# 2. 商店(FRLG 友好商店)
# ══════════════════════════════════════════════════════════════════
def render_shop(
    entries: list[dict],
    *,
    money: int,
    location_zh: str = "",
    discount: float = 1.0,
    selected: int = 0,
    scale: int = SCALE_DEFAULT,
) -> bytes:
    """商店:道具列表(图标 + 名称 + 价格)+ 选中行高亮 + 底部说明框。"""
    try:
        sc = Screen(scale=scale)
        entries = [dict(e) for e in (entries or []) if isinstance(e, dict)]
        try:
            disc = float(discount)
        except (TypeError, ValueError):
            disc = 1.0
        if not (0.0 < disc <= 1.0):
            disc = 1.0

        title = f"{location_zh}商店" if location_zh else "商店"
        sc.title_bar(title, right=f"{int(money or 0):,}₽")

        # 折扣横幅(有折扣时把列表整体下移一行)
        list_top = 19
        if disc < 0.999:
            sc.d.rounded_rectangle([5, 18, 235, 29], radius=2, fill=DISCOUNT_RED,
                                   outline=BOX_EDGE)
            sc.d.rectangle([7, 19, 233, 20], fill=(238, 132, 120))
            sc.text(11, 20, f"限时折扣 · 全场 {disc * 10:g} 折", size=8,
                    fill=(255, 248, 240))
            list_top = 30
        list_bottom = 111
        sc.window((5, list_top, 235, list_bottom), radius=2)

        shown = entries[:6]
        n = len(shown)
        sel = max(0, min(int(selected or 0), n - 1)) if n else 0
        row_h = max(12.0, (list_bottom - list_top - 2) / n) if n else 14.0
        for i, it in enumerate(shown):
            y0 = list_top + 1 + i * row_h
            if i == sel:
                sc.highlight((7, y0, 233, y0 + row_h - 1))
            kind = str(it.get("kind") or "")
            sc.item_icon(kind, 11, y0 + row_h / 2 - 4.5, 9)
            sc.text(24, y0 + row_h / 2 - 5, _fit(sc, str(it.get("zh") or it.get("key") or "?"),
                                                 96, 8.4), size=8.4, fill=TEXT)
            try:
                price = round(float(it.get("price") or 0) * disc)
            except (TypeError, ValueError):
                price = 0
            ptxt = f"{price:,}₽"
            pcolor = (48, 140, 64) if disc < 0.999 else TEXT
            sc.text_right(228, y0 + row_h / 2 - 5, ptxt, size=8.4, fill=pcolor)
            pw = sc.tw(ptxt, 8.4)
            count = int(it.get("count") or 0)
            if count:
                sc.text_right(226 - pw, y0 + row_h / 2 - 4, f"×{count}", size=7.4,
                              fill=TEXT_DIM)
        if not shown:
            sc.text(14, list_top + 6, "这里暂时没有商品。", size=8.5, fill=TEXT_DIM)

        # 底部说明框
        sc.window((5, 115, 235, 144), radius=2)
        cur = shown[sel] if n else {}
        head = f"{cur.get('zh') or cur.get('key') or '—'} · 持有 ×{int(cur.get('count') or 0)}"
        sc.text(10, 117, head, size=7.6, fill=TEXT_DIM)
        desc = str(cur.get("desc") or "—")
        for i, ln in enumerate(sc.wrap(desc, 220, size=8, limit=2)):
            sc.text(10, 126 + i * 9.5, ln, size=8, fill=TEXT)

        sc.footer("◆ /商店 买 <道具> <数量>")
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 商店界面渲染失败: %s", e)
        return b""


# ══════════════════════════════════════════════════════════════════
# 3. 道馆 / 馆主(资料 + 队伍 + VS 构图)
# ══════════════════════════════════════════════════════════════════
def render_gym(
    gym: dict,
    *,
    region_zh: str = "",
    location_zh: str = "",
    owned: bool = False,
    scale: int = SCALE_DEFAULT,
) -> bytes:
    """道馆板:左侧馆主资料与队伍,右侧「你 VS 馆主」的大构图。"""
    try:
        sc = Screen(scale=scale)
        gym = dict(gym or {})
        team = [dict(m) for m in (gym.get("team") or []) if isinstance(m, dict)]
        leader = str(gym.get("leader") or "馆主")
        gtype = str(gym.get("type") or "")
        badge_name = str(gym.get("badge") or "徽章")

        sc.title_bar(str(gym.get("title") or "道馆"), right=str(region_zh or ""))
        LEFT = (5, 19, 130, 144)
        RIGHT = (133, 19, 235, 144)
        sc.window(LEFT, radius=3)
        sc.window(RIGHT, radius=3)

        # ── 左:馆主资料 ──
        sc.text(11, 22, _fit(sc, leader, 110, 10.5), size=10.5, fill=TEXT)
        en = str(gym.get("leader_en") or "")
        if en:
            sc.text(11, 35, _fit(sc, en, 110, 7), size=7, fill=TEXT_DIM)
        _type_chip(sc, 11, 45, gtype or "?")
        if owned:
            sc.d.rounded_rectangle([60, 45, 96, 55], radius=2, fill=DONE_GREEN,
                                   outline=BOX_EDGE)
            sc.text(64, 46.6, "已获得", size=7, fill=(250, 255, 248))
        else:
            sc.d.rounded_rectangle([60, 45, 96, 55], radius=2, fill=(222, 218, 196),
                                   outline=BOX_EDGE)
            sc.text(64, 46.6, "未获得", size=7, fill=TEXT_DIM)
        sc.badge(11, 58, 13, on=owned)
        sc.text(28, 60, _fit(sc, badge_name, 96, 8), size=8, fill=TEXT)
        sc.d.line([9, 75, 126, 75], fill=BOX_HI)

        # 队伍列表(最多 4 行;超过 4 只时用前三只 + 王牌凑满,王牌始终带 ★)
        sc.text(11, 77, f"队伍 · {len(team)} 只", size=7, fill=TEXT_DIM)
        levels: list[int] = [_to_int(m.get("level")) for m in team]
        ace = levels.index(max(levels)) if levels else -1
        idx = list(range(min(4, len(team))))
        if len(team) > 4:
            idx = [0, 1, 2, len(team) - 1]
        for row_i, ti in enumerate(idx):
            mon = team[ti]
            y0 = 86 + row_i * 14
            lv = levels[ti]
            sc.sprite(str(mon.get("species") or ""), ground=(21, y0 + 13), factor=0.8,
                      bounds=(16, 13))
            name = _fit(sc, str(mon.get("zh") or mon.get("name") or mon.get("species") or "?"),
                        56, 7.8)
            sc.text(34, y0 + 2, name, size=7.8, fill=TEXT)
            lvtxt = f"Lv{lv}"
            sc.text_right(126, y0 + 2, lvtxt, size=7.6, fill=TEXT)
            if ti == ace:
                sc.text_right(126 - sc.tw(lvtxt, 7.6) - 3, y0 + 2, "★", size=7,
                              fill=GOLD)

        # ── 右:VS 构图 ──
        rx0, ry0, rx1, ry1 = RIGHT
        mid = (rx0 + rx1) / 2
        tc = type_color(gtype) if gtype else (208, 176, 120)
        sc.d.rounded_rectangle([rx0 + 3, ry0 + 3, mid + 3, ry1 - 3], radius=3,
                               fill=(212, 226, 248))
        sc.d.rounded_rectangle([mid - 3, ry0 + 3, rx1 - 3, ry1 - 3], radius=3,
                               fill=_lighten(tc, 80))
        if location_zh:
            sc.text_center(mid, 23, _fit(sc, location_zh, 96, 7.4), size=7.4, fill=TEXT_DIM)

        # 挑战者(左)与馆主(右)头像
        py, ly = 66, 66
        sc.d.ellipse([140, py - 16, 172, py + 16], fill=(96, 138, 232), outline=BOX_EDGE)
        sc.d.ellipse([148, py - 12, 164, py + 3], fill=(52, 52, 44))          # 头
        sc.d.polygon([(145, py + 15), (149, py + 1), (163, py + 1), (167, py + 15)],
                     fill=(52, 52, 44))                                        # 身体
        sc.d.ellipse([196, ly - 16, 228, ly + 16], fill=tc, outline=BOX_EDGE)
        sc.text_center(212, ly - 8, (leader[:1] or "?"), size=12, fill=(255, 255, 250))

        # 中间 VS 徽标 + 斜向射线
        sc.d.line([176, 40, 184, 52], fill=(250, 236, 170))
        sc.d.line([192, 40, 184, 52], fill=(250, 236, 170))
        sc.d.rounded_rectangle([168, 50, 200, 82], radius=5, fill=(232, 184, 64),
                               outline=BOX_EDGE, width=2)
        sc.d.rounded_rectangle([171, 53, 197, 66], radius=3, outline=(252, 228, 140))
        sc.text_center(184, 58, "VS", size=14, fill=(168, 48, 40), stroke=0.8,
                       sfill=(255, 250, 230))
        sc.text_center(156, 88, "你", size=8, fill=TEXT)
        sc.text_center(212, 88, _fit(sc, leader, 40, 8), size=8, fill=TEXT)

        # 徽章条
        sc.d.rounded_rectangle([139, 104, 229, 138], radius=3, fill=BOX_FILL,
                               outline=BOX_EDGE)
        sc.badge(146, 109, 14, on=owned)
        sc.text(166, 110, _fit(sc, badge_name, 60, 8), size=8, fill=TEXT)
        state = "已获得 · 可重复挑战" if owned else "战胜馆主即可获得"
        sc.text(146, 127, state, size=7, fill=DONE_GREEN if owned else TEXT_DIM)

        sc.footer("◆ /道馆 挑战 馆主 · 胜利后获得徽章")
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 道馆界面渲染失败: %s", e)
        return b""


# ══════════════════════════════════════════════════════════════════
# 4. 宝可梦联盟(四天王 + 冠军)
# ══════════════════════════════════════════════════════════════════
def render_league(
    region_zh: str,
    elite4: list[dict],
    champion: dict,
    *,
    done: list[str] | None = None,
    champion_done: bool = False,
    scale: int = SCALE_DEFAULT,
) -> bytes:
    """联盟:四天王 4 行 + 冠军 1 行,含属性头像、队伍数量与战胜状态。"""
    try:
        sc = Screen(scale=scale)
        elites = [dict(e) for e in (elite4 or []) if isinstance(e, dict)]
        champ = dict(champion or {})
        done_set = {str(d) for d in (done or [])}

        sc.title_bar("宝可梦联盟", right=str(region_zh or ""))
        sc.window((5, 19, 235, 141), radius=3)

        rows: list[tuple[dict, bool, bool]] = []  # (数据, 是否已战胜, 是否冠军)
        for i, e in enumerate(elites[:4]):
            key = str(e.get("order") if e.get("order") is not None else i + 1)
            rows.append((e, key in done_set or str(i + 1) in done_set, False))
        while len(rows) < 4:
            rows.append(({}, False, False))
        rows.append((champ, bool(champion_done), True))

        row_h = 23
        top = 21
        for i, (mon, is_done, is_champ) in enumerate(rows):
            y0 = top + i * row_h
            if is_champ:
                sc.d.line([9, y0 - 3, 231, y0 - 3], fill=BOX_HI)
                sc.d.rounded_rectangle([7, y0, 233, y0 + row_h - 3], radius=2,
                                       fill=(252, 240, 198))
            elif i % 2 == 1:
                sc.d.rounded_rectangle([7, y0, 233, y0 + row_h - 3], radius=2,
                                       fill=(244, 243, 224))
            name = str(mon.get("name") or ("???" if not is_champ else "冠军"))
            gtype = str(mon.get("type") or "")
            team = mon.get("team") or []
            tc = type_color(gtype) if gtype else (208, 184, 140)
            cy = y0 + 9
            sc.d.ellipse([12, cy - 8, 28, cy + 8], fill=tc, outline=BOX_EDGE)
            sc.text_center(20, cy - 5.5, (name[:1] or "?"), size=9, fill=(255, 255, 250))
            sc.text(34, y0 + 3, _fit(sc, name, 92, 8.8), size=8.8, fill=TEXT)
            if gtype:
                _type_chip(sc, 34, y0 + 13, gtype, size=6.6)
            else:
                sc.text(34, y0 + 13, "—", size=7, fill=TEXT_DIM)
            if team:
                sc.text(96, y0 + 4, f"队伍 {len(team)} 只", size=7.4, fill=TEXT_DIM)
            if is_champ:
                label = "冠军" if not is_done else "冠军 · 已战胜"
                sc.text(96, y0 + 13, label, size=7, fill=GOLD if is_champ else TEXT_DIM)
            glyph, gcolor = _state_glyph(is_done, False)
            sc.text_right(228, y0 + 4, f"{glyph} {'已战胜' if is_done else '未挑战'}",
                          size=7.8, fill=gcolor)

        sc.footer("◆ /联盟 挑战 · 击败四天王后挑战冠军")
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 联盟界面渲染失败: %s", e)
        return b""


# ══════════════════════════════════════════════════════════════════
# 5. 主线章节(对抗邪恶组织)
# ══════════════════════════════════════════════════════════════════
def render_story(
    region_zh: str,
    org: str,
    leader: str,
    stages: list[dict],
    *,
    done: list[str] | None = None,
    current_key: str = "",
    badges: int = 0,
    total_gyms: int = 8,
    scale: int = SCALE_DEFAULT,
) -> bytes:
    """主线:章节列表(已完成 / 当前 / 未开始)+ 底部当前章节说明。"""
    try:
        sc = Screen(scale=scale)
        stages = [dict(s) for s in (stages or []) if isinstance(s, dict)]
        done_set = {str(d) for d in (done or [])}
        org = str(org or "邪恶组织")
        leader = str(leader or "?")
        cur_key = str(current_key or "")

        left = f"对抗{org}(首领:{leader})"
        right = f"徽章 {int(badges or 0)}/{int(total_gyms or 8)}"
        size = 9.5
        if sc.tw(left, size) + sc.tw(right, size) > 208:
            left = f"对抗{org}"
        if sc.tw(left, size) + sc.tw(right, size) > 208:
            size = 8.2
        sc.title_bar(_fit(sc, left, 150, size), right=_fit(sc, right, 74, size), size=size)

        LIST = (5, 19, 235, 108)
        sc.window(LIST, radius=2)
        shown = stages[:6]
        default_cur = _first_open(shown, done_set, cur_key)
        row_h = 14.0
        top = 21
        for i, st in enumerate(shown):
            key = str(st.get("key") or "")
            y0 = top + i * row_h
            is_done = key in done_set
            is_cur = (key == cur_key) if cur_key else (i == default_cur)
            if is_cur:
                sc.highlight((7, y0, 233, y0 + row_h - 2))
            glyph, gcolor = _state_glyph(is_done, is_cur)
            sc.text(11, y0 + 1.5, glyph, size=8.6, fill=gcolor)
            title = _fit(sc, str(st.get("title") or key or "?"), 150, 8.4)
            sc.text(23, y0 + 1.5, title, size=8.4,
                    fill=TEXT if not is_done else TEXT_DIM)
            kind = STORY_KIND.get(str(st.get("kind") or ""), "")
            if kind:
                sc.text_right(228, y0 + 1.5, kind, size=7, fill=TEXT_DIM)
        if not shown:
            sc.text(14, top + 6, "还没有可进行的主线章节。", size=8.5, fill=TEXT_DIM)

        # 底部:当前章节说明
        sc.window((5, 111, 235, 144), radius=2)
        cur = _pick_stage(shown, done_set, cur_key)
        if cur:
            head = f"{'●' if str(cur.get('key')) in done_set else '◆'} {cur.get('title') or ''}"
            sc.text(10, 113, _fit(sc, head, 220, 7.8), size=7.8,
                    fill=DONE_GREEN if str(cur.get("key")) in done_set else PIN_RED)
            for i, ln in enumerate(sc.wrap(str(cur.get("desc") or "—"), 220,
                                           size=8, limit=2)):
                sc.text(10, 123 + i * 9.5, ln, size=8, fill=TEXT)
        else:
            sc.text(10, 118, "沿着地图前进,继续你的旅程。", size=8, fill=TEXT_DIM)

        sc.footer("◆ /主线 继续 · 与首领的决战在等着你")
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 主线界面渲染失败: %s", e)
        return b""


def _first_open(shown: list[dict], done: set[str], cur_key: str) -> int:
    """未显式给出当前章节时:取第一个未完成的章节作为当前。"""
    if cur_key:
        for i, st in enumerate(shown):
            if str(st.get("key")) == cur_key:
                return i
    for i, st in enumerate(shown):
        if str(st.get("key")) not in done:
            return i
    return len(shown) - 1 if shown else 0


def _pick_stage(shown: list[dict], done: set[str], cur_key: str) -> dict:
    for st in shown:
        if str(st.get("key")) == cur_key:
            return st
    for st in shown:
        if str(st.get("key")) not in done:
            return st
    return shown[-1] if shown else {}
