"""宝可梦世界:城镇地图 / 商店 / 道馆 / 联盟 / 主线 五个菜单界面(仿 GBA)。

沿用 `ui_render.py` 的 `Screen`(240×160 逻辑画布 + 放大文字层),
配色、窗口框、徽章、道具图标、属性色一律复用共享工具箱,不重复实现。

所有函数都**不会抛异常**:内部整体 try/except,失败时 `logger.debug` 并返回 `b""`,
调用方回退纯文本 —— 渲染永远不该中断游戏。
"""

from __future__ import annotations

from itertools import pairwise

from astrbot.api import logger

from .dex import get_dex
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


def _goal_lines(sc, text: str, width: float, size: float, limit: int = 2,
                min_size: float = 6.4) -> tuple[list[str], float]:
    """排版「下一目标」:优先略微缩小字号压成一行,否则折行且不留孤儿字。

    例如「挑战枯叶市道馆:马志士」按 7.8 号字折成 2 行时,第 2 行只剩一个
    「士」,看着很像排版事故。
    """
    t = str(text or "自由探索")
    sz = float(size)
    while sz > min_size and sc.tw(t, sz) > width:
        sz -= 0.2
    if sc.tw(t, sz) <= width:
        return [t], sz
    lines = sc.wrap(t, width, size=size, limit=99)
    if len(lines) > limit:
        out = lines[:limit]
        last = out[-1]
        out[-1] = (last[:-1] + "…") if len(last) > 1 else "…"
        return out, size
    if len(lines) > 1 and len(lines[-1]) <= 2:
        # 末行只有一两个字 → 合并成一行并省略结尾,不单独占一行
        head = lines[-2]
        lines = [*lines[:-2], (head[:-1] + "…") if len(head) > 1 else "…"]
    return lines, size


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


def type_name(t: str) -> str:
    """属性英文 key → 中文(拿不到就原样返回)。"""
    return get_dex().type_label(str(t))


def _type_chip(sc: Screen, x: float, y: float, t: str, *, size: float = 7.4) -> float:
    """属性色小标签,返回宽度(传入的应为中文属性名)。"""
    label = type_name(t)
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


def _chain_window(order: list[str], current: str, limit: int) -> tuple[int, list[str]]:
    """地图一次只画得下 limit 个节点:以当前位置为锚取窗口。

    之前固定取 `order[:15]`,再把当前位置硬塞进最后一格 —— 于是**无论玩家走到哪,
    地图永远只画地区开头那一段**。关都走到 7 号道路时,8 号道路之后的节点全被
    裁掉,看起来像“关都就这么大、道馆只有两个”(用户实测反馈)。
    现在左边只留 4 个刚走过的节点,其余空位全部给前方,地图随进度向前滚动。
    """
    total = len(order)
    if limit <= 0 or total <= limit:
        return 0, list(order)
    try:
        idx = order.index(current)
    except ValueError:
        idx = 0  # 查看其它地区 / 位置不在链上:从开头画
    start = max(0, min(idx - 4, total - limit))
    return start, order[start:start + limit]


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
# 地图界面几何(测试据此推导"面板之间的缝隙",避免写死数字)
MAP_BOX = (5, 19, 150, 102)
TOWN_TOP = 105


def render_world_map(entries: list[dict], *, scale: int = SCALE_DEFAULT) -> bytes:
    """世界地图:8 个地区一屏看完 —— 开放/通关状态、徽章进度、下一站。

    `entries` 每项(由 main.py 组装):
        {zh, order, unlocked, champion, badges, gyms, current, next_zh, prev_zh}
    """
    try:
        rows = [dict(e) for e in (entries or []) if isinstance(e, dict)]
        # 地区多了(第九地区帕底亚)就按行数把画布加高 —— 与战报/早间新闻一样的自适应
        row_h = 14.6
        y0 = 19.0
        need_h = y0 + len(rows) * row_h + 4 + 20      # 内容 + 底部提示条
        canvas_h = max(160, int(need_h))
        sc = Screen(scale=scale, h=canvas_h)
        done = sum(1 for e in rows if e.get("champion"))
        sc.title_bar("世界地图", right=f"通关 {done}/{len(rows)} 地区")
        for i, e in enumerate(rows):
            top = y0 + i * row_h
            bot = top + row_h - 1.6
            current = bool(e.get("current"))
            unlocked = bool(e.get("unlocked"))
            champion = bool(e.get("champion"))
            sc.window((5, top, 235, bot), radius=2,
                      edge=PIN_RED if current else BOX_EDGE,
                      shadow=(i == 0), hi=False)
            order = _to_int(e.get("order"), i + 1)
            sc.text(10, top + 4.2, f"第{order}地区", size=6.4, fill=TEXT_DIM)
            if current:
                sc.d.ellipse([40, top + 4.2, 45, top + 9.2], fill=PIN_RED)
            sc.text(47, top + 2.4, str(e.get("zh") or e.get("key") or "?"),
                    size=8.4, fill=PIN_RED if current else (TEXT if unlocked else STATUS_OFF))
            if champion:
                status, color = f"已通关 {_to_int(e.get('gyms'), 8)}/{_to_int(e.get('gyms'), 8)}", GOLD
            elif unlocked:
                badges = _to_int(e.get("badges"))
                gyms = _to_int(e.get("gyms"), 8)
                nxt = str(e.get("next_zh") or "联盟")
                status, color = f"徽章 {badges}/{gyms} · 下一站 {nxt}", DONE_GREEN
            else:
                status, color = f"未开放 · 需{_str_prev(e)}冠军", STATUS_OFF
            sc.text_right(230, top + 4.4, _fit(sc, status, 110, 6.8), size=6.8,
                          fill=color)
        sc.footer("◆ 红框 = 你在这里 · 通关一个地区解锁下一个")
        return sc.finish()
    except Exception as e:  # 渲染永远不能把游戏搞崩
        logger.debug("宝可梦世界: 世界地图渲染失败: %s", e)
        return b""


def _str_prev(entry: dict) -> str:
    return str(entry.get("prev_zh") or "上一地区")


def render_map(
    region_zh: str,
    nodes: list[dict],
    *,
    current: str,
    visited: list[str],
    gyms: list[dict] | None = None,
    next_goal: str = "",
    region_order: int = 0,
    badge_count: int = -1,
    scale: int = SCALE_DEFAULT,
) -> bytes:
    """城镇地图:左上是节点地图窗口(随当前位置滚动),左下地点列表,右侧资料栏。

    `badge_count` 是该地区的徽章数(本地区):标题栏会显示“徽章 2/8”,
    避免只看得到一两个道馆城镇就以为地区只有这么多道馆。
    """
    try:
        sc = Screen(scale=scale)
        nodes = [dict(n) for n in (nodes or []) if isinstance(n, dict)]
        visited_set = {str(v) for v in (visited or [])}
        gym_locs = {str(g.get("location") or "") for g in (gyms or []) if isinstance(g, dict)}
        region = str(region_zh or "?")
        cur_key = str(current or "")

        gym_total = len(gym_locs) or 8
        title_right = (f"{region} · 徽章 {int(badge_count)}/{gym_total}"
                       if badge_count is not None and int(badge_count) >= 0 else region)
        sc.title_bar("镇地图", right=title_right)
        # 宽度保持原样(左列到 150、右栏 153..235);溢出是**纵向**的:
        # 左下清单最后一行、右栏图例最后一行原本都贴着面板下边框。
        # 做法是把上面地图压矮 4px,把左下清单**加高** —— 而不是去动宽度。
        MAP = MAP_BOX
        TOWN = (5, TOWN_TOP, 150, sc.content_bottom)
        INFO = (153, 19, 235, sc.content_bottom)
        sc.window(MAP, radius=2)
        sc.window(TOWN, radius=2)
        sc.window(INFO, radius=2)

        mx0, my0, mx1, my1 = MAP
        # 地图底色:陆地。早期版本还在这里随手画了两团淡蓝椭圆当"海",但它们
        # 不对应任何真实地理(节点是网格排布,没有坐标可言),只会让人误以为是
        # 某种标记,所以去掉。—— 地图上的图形必须承载信息。
        sc.d.rounded_rectangle([mx0 + 3, my0 + 3, mx1 - 3, my1 - 3], radius=2,
                               fill=MAP_LAND)

        order, nxt = _chain_keys(nodes)
        pos: dict[str, tuple[float, float]] = {}
        cols = 5
        # 3 行 × 5 列:第 4 行的节点名会压到下面的"地点"框上,所以只画 3 行
        shown_start, shown = _chain_window(order, cur_key, cols * 3)
        rows = max(1, (len(shown) + cols - 1) // cols)
        cw = (mx1 - mx0 - 8) / cols   # 左列收窄后 cw 自动跟着变
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

        # 左下:地点清单(最多 6 条,3 列 × 2 行)—— 也跟着玩家走,
        # 显示“你在这里”前后几处,而不是地区最前面几个。
        sc.text(9, 107.5, "地点", size=7, fill=TEXT_DIM)
        if len(order) > len(shown):
            end = shown_start + len(shown)
            sc.text_right(146, 107.5, f"{shown_start + 1}-{end}/{len(order)} 处", size=7,
                          fill=TEXT_DIM)
        try:
            idx_cur = order.index(cur_key) if cur_key else 0
        except ValueError:
            idx_cur = 0
        list_start = max(0, min(idx_cur - 2, max(0, len(order) - 6)))
        slots = 5 if len(order) - list_start > 6 else 6
        near = order[list_start:list_start + slots]
        disp: list[tuple[str, tuple[int, int, int]]] = []
        for key in near:
            node = next((n for n in nodes if str(n.get("key")) == key), {})
            label = _fit(sc, str(node.get("zh") or key), 44, 7)
            color = PIN_RED if key == cur_key else (TEXT if key in visited_set else TEXT_DIM)
            disp.append((label, color))
        if slots == 5:
            disp.append((f"+{len(order) - (list_start + 5)}", TEXT_DIM))
        for i, (label, color) in enumerate(disp):
            col, row = i % 3, i // 3
            sc.text(9 + col * 46, 119 + row * 9.4, label, size=7, fill=color)

        # 右侧:地区 / 排名 / 当前位置 / 下一目标 / 图例
        cx = (INFO[0] + INFO[2]) / 2
        sc.text_center(cx, 23, _fit(sc, region, 82, 10.5), size=10.5, fill=TEXT)
        sc.text_center(cx, 38, f"第{int(region_order or 1)}地区", size=7.6, fill=TEXT_DIM)
        sc.d.line([INFO[0] + 5, 47, INFO[2] - 5, 47], fill=BOX_HI)
        sc.text(158, 50, "当前位置", size=7, fill=TEXT_DIM)
        cur_zh = ""
        for n in nodes:
            if str(n.get("key")) == cur_key:
                cur_zh = str(n.get("zh") or cur_key)
        sc.text(158, 59, _fit(sc, cur_zh or "未知", 72, 8.8), size=8.8, fill=PIN_RED)
        sc.text(158, 72, "下一目标", size=7, fill=TEXT_DIM)
        goal_lines, goal_size = _goal_lines(sc, next_goal, 72, 7.8, 2)
        for i, ln in enumerate(goal_lines):
            sc.text(158, 81 + i * 9, ln, size=goal_size, fill=TEXT)
        sc.d.line([INFO[0] + 5, 100, INFO[2] - 5, 100], fill=BOX_HI)
        legend = [("current", "当前位置"), ("visited", "已到过"),
                  ("empty", "未到过"), ("gym", "道馆城镇")]
        for i, (kind, label) in enumerate(legend):
            ly = 101.5 + i * 9.2
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
# 商店每页条目数(满徽章时货架有 80+ 种,必须分页)
SHOP_PER_PAGE = 4


def render_shop(
    entries: list[dict],
    *,
    money: int,
    location_zh: str = "",
    discount: float = 1.0,
    selected: int = 0,
    per_page: int = SHOP_PER_PAGE,
    scale: int = SCALE_DEFAULT,
) -> bytes:
    """商店:道具列表(分页 + 选中高亮)+ 底部说明框(价格/持有/效果/说明)。

    `selected` 是**整个货架上的下标**(0 起),会自动翻到它所在那一页并高亮 ——
    以前只看得到前 6 件、说明框永远只写第 0 件,而满徽章时货架有 80+ 种商品。
    """
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
        list_top = 19.0
        if disc < 0.999:
            sc.d.rounded_rectangle([5, 18, 235, 29], radius=2, fill=DISCOUNT_RED,
                                   outline=BOX_EDGE)
            sc.d.rectangle([7, 19, 233, 20], fill=(238, 132, 120))
            sc.text(11, 20, f"限时折扣 · 全场 {disc * 10:g} 折", size=8,
                    fill=(255, 248, 240))
            list_top = 30.0
        list_bottom = 104.0

        total = len(entries)
        per = max(1, int(per_page))
        pages = max(1, (total + per - 1) // per)
        sel = max(0, min(int(selected or 0), total - 1)) if total else 0
        page = max(0, min(sel // per, pages - 1)) if total else 0
        shown = entries[page * per:(page + 1) * per]
        sc.window((5, list_top, 235, list_bottom), radius=2)
        n = len(shown)
        # 行高按"每页槽位数"算(否则最后半页的行会被拉开)
        row_h = max(13.0, (list_bottom - list_top - 2) / per)
        for i, it in enumerate(shown):
            idx = page * per + i
            y0 = list_top + 1 + i * row_h
            if idx == sel:
                sc.highlight((7, y0, 233, y0 + row_h - 1))
            # 传 key 而不是大类:否则 15 种球、9 种药在货架上长得一模一样
            icon = str(it.get("key") or it.get("kind") or "")
            sc.item_icon(icon, 11, y0 + 1.5, 9)
            # 行首序号:玩家要能指着"第 N 件"看详情
            sc.text(23, y0 + 2.2, f"{idx + 1}.", size=6.6, fill=TEXT_DIM)
            sc.text(35, y0 + 1.5,
                    _fit(sc, str(it.get("zh") or it.get("key") or "?"), 88, 8.4),
                    size=8.4, fill=TEXT)
            try:
                price = round(float(it.get("price") or 0) * disc)
            except (TypeError, ValueError):
                price = 0
            ptxt = f"{price:,}₽"
            pcolor = (48, 140, 64) if disc < 0.999 else TEXT
            sc.text_right(228, y0 + 1.5, ptxt, size=8.4, fill=pcolor)
            pw = sc.tw(ptxt, 8.4)
            count = int(it.get("count") or 0)
            if count:
                sc.text_right(226 - pw, y0 + 2.5, f"×{count}", size=7.4,
                              fill=TEXT_DIM)
            # 每行都写效果 —— 否则玩家只看得到"选中那一件"的说明
            eff = _fit(sc, str(it.get("effect") or it.get("desc") or ""), 198, 6.4)
            if eff:
                sc.text(35, y0 + row_h - 7.6, eff, size=6.4, fill=TEXT_DIM)
        if not shown:
            sc.text(14, list_top + 6, "这里暂时没有商品。", size=8.5, fill=TEXT_DIM)

        # 底部说明框:选中那一件的价格/持有/效果/说明
        sc.window((5, 106, 235, sc.content_bottom), radius=2)
        cur = shown[sel - page * per] if n else {}
        if cur:
            try:
                price = round(float(cur.get("price") or 0) * disc)
            except (TypeError, ValueError):
                price = 0
            head = (f"第 {sel + 1}/{max(1, total)} 件 · "
                    f"{cur.get('zh') or cur.get('key') or '—'}"
                    f" · 持有 ×{int(cur.get('count') or 0)} · {price:,}₽")
        else:
            head = "—"
        sc.text(10, 108.5, _fit(sc, head, 150, 7.6), size=7.6, fill=TEXT_DIM)
        if pages > 1:
            sc.text_right(231, 108.5,
                          f"{page + 1}/{pages} 页 · {page * per + 1}-"
                          f"{min(total, (page + 1) * per)}/共 {total}",
                          size=6.6, fill=TEXT_DIM)
        # 分块绘制(理由同背包:wrap 不认换行符)
        y = 117.5
        room = max(1, int((sc.content_bottom - y - 0.5) // 8.6))
        eff = str(cur.get("effect") or "")
        if eff:
            for ln in sc.wrap(f"⚙️ {eff}", 220, size=7.8, limit=1):
                sc.text(10, y, ln, size=7.8, fill=(40, 96, 56))
                y += 8.6
                room -= 1
        for ln in sc.wrap(str(cur.get("desc") or "—"), 220, size=7.8, limit=max(1, room)):
            sc.text(10, y, ln, size=7.8, fill=TEXT)
            y += 8.6

        # 底部只放最常用的(长了会被截断);买/卖的完整写法在消息文本的提示行
        tail = '◆ 详情:"/商店 12"'
        if pages > 1:
            tail += ' · 翻页:"/商店 页 2"'
        sc.footer(_fit(sc, tail, 226, 7.0), size=7.0)
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
        LEFT = (5, 19, 130, sc.content_bottom)
        RIGHT = (133, 19, 235, sc.content_bottom)
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
        sc.badge(11, 58, 13, on=owned, kind=str(gym.get("type") or ""))
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
        sc.badge(146, 109, 14, on=owned, kind=str(gym.get("type") or ""))
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
        sc.window((5, 19, 235, sc.content_bottom), radius=3)

        rows: list[tuple[dict, bool, bool]] = []  # (数据, 是否已战胜, 是否冠军)
        for i, e in enumerate(elites[:4]):
            key = str(e.get("order") if e.get("order") is not None else i + 1)
            rows.append((e, key in done_set or str(i + 1) in done_set, False))
        while len(rows) < 4:
            rows.append(({}, False, False))
        rows.append((champ, bool(champion_done), True))

        # 布局:每行两行文字 —— 第一行「名字 + 属性标签」,第二行「队伍 / 状态」。
        # 旧布局把属性标签放在第二行(y0+13,标签框高 10),而行高只有 23、
        # 行底在 y0+20 → 标签压住下一行的底色;冠军行的分隔线还画在 y0-3,
        # 正好从上一行的标签中间穿过(用户报告的"重叠/溢出")。
        row_h = 24
        top = 21
        for i, (mon, is_done, is_champ) in enumerate(rows):
            y0 = top + i * row_h
            if is_champ:
                # 分隔线画在行**上方的空隙**里(y0-1),不再穿进上一行
                sc.d.line([9, y0 - 1, 231, y0 - 1], fill=BOX_HI)
                sc.d.rounded_rectangle([7, y0, 233, y0 + row_h - 4], radius=2,
                                       fill=(252, 240, 198))
            elif i % 2 == 1:
                sc.d.rounded_rectangle([7, y0, 233, y0 + row_h - 4], radius=2,
                                       fill=(244, 243, 224))
            name = str(mon.get("name") or ("???" if not is_champ else "冠军"))
            gtype = str(mon.get("type") or "")
            team = mon.get("team") or []
            tc = type_color(gtype) if gtype else (208, 184, 140)
            cy = y0 + 8
            sc.d.ellipse([12, cy - 7, 26, cy + 7], fill=tc, outline=BOX_EDGE)
            sc.text_center(19, cy - 5, (name[:1] or "?"), size=8.6, fill=(255, 255, 250))
            # 第一行:名字 + 属性标签(标签紧跟名字,属性用中文)
            label = _fit(sc, name, 66, 8.8)
            sc.text(34, y0 + 3, label, size=8.8, fill=TEXT)
            chip_x = 34 + sc.tw(label, 8.8) + 4
            if gtype:
                _type_chip(sc, chip_x, y0 + 2.5, type_name(gtype), size=6.6)
            # 第二行:队伍数量 / 冠军标记 / 战胜状态
            if team:
                sc.text(34, y0 + 13, f"队伍 {len(team)} 只", size=7.4, fill=TEXT_DIM)
            if is_champ:
                tag = "冠军 · 已战胜" if is_done else "冠军"
                sc.text(96, y0 + 13, tag, size=7, fill=GOLD)
            glyph, gcolor = _state_glyph(is_done, False)
            sc.text_right(228, y0 + 13, f"{glyph} {'已战胜' if is_done else '未挑战'}",
                          size=7.6, fill=gcolor)

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
        sc.window((5, 111, 235, sc.content_bottom), radius=2)
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

        sc.footer("◆ /主线 挑战 · 与首领的决战在等着你")
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
