"""宝可梦世界:信息类界面渲染(传说图鉴 / 早间新闻 / 大赛赛程 / 战斗结算 / 成长 / 捕获)。

沿用 `ui_render.py` 的两层做法:
  · **图形在 240×160 逻辑画布上画**,`NEAREST` 放大 → 像素质感;
  · **文字在放大层画**,坐标仍用逻辑像素 → 中文清晰。
叙事框沿用 `battle_render.py` 的"暗红框 + 青绿底"经典对话框。

任何异常都返回 `b""`,调用方回退纯文本 —— 渲染永远不该中断游戏。
"""

from __future__ import annotations

import math

from astrbot.api import logger

from .ui_render import (
    BG,
    BOX_EDGE,
    BOX_SHADOW,
    HP_LOW,
    HP_MID,
    HP_OK,
    LOGICAL_H,
    MSG_FILL,
    MSG_FRAME,
    MSG_SHADOW,
    MSG_TEXT,
    SCALE_DEFAULT,
    TEXT,
    TEXT_DIM,
    Screen,
    gender_symbol,
)

# ── 局部配色(在 ui_render 调色板上补几个)──────────────────────
MSG_FRAME_HI = (214, 100, 82)      # 暗红框内圈高光
CHIP_TIME = (92, 88, 152)          # 昼夜标签
CHIP_WEATHER = (232, 176, 64)      # 天气标签
CHIP_REGION = (96, 152, 104)       # 地区标签
CHIP_PLACE = (104, 144, 206)       # 地点标签
CHIP_LOCK = (176, 96, 88)          # 未解锁标签
STAMP_RED = (196, 56, 48)          # 「收服」印章
GOLD = (232, 184, 64)
GOLD_HI = (252, 228, 140)
SLOT_FILL = (226, 222, 194)        # 图鉴精灵槽底色
SPOT_1 = (252, 250, 232)           # 聚光灯内圈
SPOT_2 = (246, 240, 206)           # 聚光灯外圈
RAY = (250, 244, 190)              # 光芒

OUTCOME_STYLE = {
    "win": ("胜利!", (96, 168, 88)),
    "loss": ("失败…", (176, 72, 64)),
    "caught": ("捕获成功!", (224, 168, 56)),
    "escaped": ("逃脱了…", (104, 144, 208)),
    "stalled": ("不了了之", (150, 140, 110)),
    "forfeit": ("认输了", (140, 138, 120)),
}
OUTCOME_FOOT = {
    "win": "◆ 继续前进,下一场一定也能赢!",
    "loss": "○ 先回宝可梦中心回复,明天再来。",
    "caught": "◆ 图鉴又添了一笔新的记录。",
    "escaped": "○ 它跑掉了……下次准备充分些。",
    "stalled": "○ 双方僵持太久,各自收兵。",
    "forfeit": "○ 撤退也是战术,养好伤再来。",
}


# ══════════════════════════════════════════════════════════════════
# 小工具
# ══════════════════════════════════════════════════════════════════
def _int(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _str(v, default: str = "") -> str:
    s = "" if v is None else str(v)
    return s or default


def _ratio(cur, mx) -> float:
    try:
        c, m = float(cur or 0), float(mx or 1)
    except (TypeError, ValueError):
        return 1.0
    return 0.0 if m <= 0 else max(0.0, min(1.0, c / m))


def _fit(sc: Screen, s: str, max_w: float, size: float) -> str:
    """按逻辑宽度截断,超出部分用「…」。"""
    text = _str(s)
    if sc.tw(text, size) <= max_w:
        return text
    out = ""
    for ch in text:
        if sc.tw(out + ch + "…", size) > max_w:
            break
        out += ch
    return (out + "…") if out else ""


def _dialog(sc: Screen, box, *, radius: int = 3) -> None:
    """经典对话框:暗红外框 + 青绿底(和战斗界面的对话框一致)。"""
    x0, y0, x1, y1 = box
    d = sc.d
    d.rounded_rectangle(list(box), radius=radius, fill=MSG_FRAME)
    d.rounded_rectangle([x0 + 2, y0 + 2, x1 - 2, y1 - 2], radius=max(1, radius - 1),
                        outline=MSG_FRAME_HI)
    d.rounded_rectangle([x0 + 4, y0 + 4, x1 - 4, y1 - 4], radius=max(1, radius - 1),
                        fill=MSG_FILL)


def _dialog_text(sc: Screen, box, title: str, lines: list[str], *,
                 size: float = 8, limit: int = 3) -> None:
    """在对话框里画标题 + 正文(或灰色占位)。"""
    x0, y0, x1, _y1 = box
    if title:
        sc.text(x0 + 7, y0 + 5.2, title, size=8.4, fill=MSG_TEXT, stroke=0.5,
                sfill=MSG_SHADOW)
    body = [ln for ln in (lines or []) if _str(ln)][:limit]
    if not body:
        body = ["—"]
    maxw = x1 - x0 - 18
    wrapped: list[str] = []
    is_last = False
    for idx, ln in enumerate(body):
        is_last = idx == len(body) - 1
        wrapped.extend(_wrap_fit(sc, ln, maxw, size, max(1, limit - len(wrapped))))
        if len(wrapped) >= limit:
            break
    if len(wrapped) >= limit and not is_last:
        # 还有后续条目没画出来 → 末行提示省略
        last = wrapped[-1]
        wrapped[-1] = (last[:-1] + "…") if len(last) > 1 else "…"
    for row, ln in enumerate(wrapped[:limit]):
        sc.text(x0 + 7, y0 + 16.5 + row * 9.8, ln, size=size, fill=MSG_TEXT)


def _wrap_rows(sc, text, avail: float, *, size: float = 8) -> list[str]:
    """按实际字宽折行(每条占用多行,不再用省略号截断)。"""
    out: list[str] = []
    for raw in ([text] if isinstance(text, str) else list(text or ())):
        cur = ""
        for ch in str(raw):
            probe = cur + ch
            try:
                w = sc.measure(probe, size)
            except Exception:
                w = len(probe) * size * 0.9
            if cur and w > avail:
                out.append(cur)
                cur = ch
            else:
                cur = probe
        if cur:
            out.append(cur)
    return out

def _lines(seq, *, limit: int = 8) -> list[str]:
    return [_str(x) for x in (seq or ()) if _str(x)][:limit]


def _species_key(zh: str) -> str:
    """中文名 → 物种 key(失败返回空串),用于把「进化前」也画对。"""
    if not _str(zh):
        return ""
    try:
        from .dex import get_dex

        r = get_dex().resolve_species(str(zh))
        return r[0] if r else ""
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════════
# 1. 传说图鉴
# ══════════════════════════════════════════════════════════════════
def render_legendaries(region_zh: str, sites: list[dict], *, caught: list[str] = (),
                       ready: list[str] = (), locked: list[str] = (), badges: int = 0,
                       total_gyms: int = 8, champion: bool = False, day: int = 0,
                       scale: int = SCALE_DEFAULT) -> bytes:
    """传说图鉴:每行一只,含精灵槽 / 名字 / 等级 / 栖息地 / 徽章需求 / 状态。"""
    try:
        sc = Screen(scale=scale)
        caught_s = {_str(x) for x in (caught or ()) if _str(x)}
        ready_s = {_str(x) for x in (ready or ()) if _str(x)}
        locked_s = {_str(x) for x in (locked or ()) if _str(x)}
        sc.title_bar(f"{_str(region_zh, '地区')} 传说图鉴",
                     right=f"徽章 {badges}/{total_gyms} · 第 {day} 天")

        rows = list(sites or [])[:7]
        if not rows:
            sc.window((5, 19, 235, 140), radius=3)
            sc.text(15, 60, "这片地区还没有传说宝可梦的线索。", size=9, fill=TEXT_DIM)
            sc.text(15, 76, "多收集一些徽章,传闻就会出现了。", size=9, fill=TEXT_DIM)
            sc.footer("◆ 前往栖息地后用 /神兽 挑战")
            return sc.finish()

        top, rh = 19, 17
        for i, site in enumerate(rows):
            y0 = top + i * rh
            sp = _str(site.get("species") if isinstance(site, dict) else "", "")
            site = site if isinstance(site, dict) else {}
            is_locked = sp in locked_s
            is_ready = sp in ready_s and not is_locked
            is_caught = sp in caught_s and not is_locked
            need = _int(site.get("need"))
            need_champ = bool(site.get("champion"))

            if is_ready:
                sc.highlight((6, y0 + 1, 234, y0 + rh - 2))
            sc.window((5, y0, 235, y0 + rh - 1), radius=2, shadow=(i == 0))

            # 精灵槽
            sc.d.rounded_rectangle([8, y0 + 2, 30, y0 + rh - 3], radius=2,
                                   fill=SLOT_FILL, outline=BOX_EDGE)
            sc.sprite(sp, ground=(19, y0 + rh - 4), factor=0.5, bounds=(18, 12),
                      silhouette=not is_caught, hires=True)

            # 名字
            name = "???" if is_locked else _fit(sc, _str(site.get("zh"), "?"), 52, 8.4)
            sc.text(34, y0 + 3.4, name, size=8.4,
                    fill=TEXT if not is_locked else TEXT_DIM)

            # 等级
            lv = "???" if is_locked else f"Lv{_int(site.get('level'))}"
            sc.text(96, y0 + 3.6, lv, size=7.6, fill=TEXT)

            # 栖息地
            place = "???" if is_locked else _str(site.get("location_zh"), "未知")
            sc.text(126, y0 + 3.6, _fit(sc, place, 62, 7.4), size=7.4, fill=TEXT_DIM)

            # 状态标记
            if is_locked:
                mark, mcolor = "?", TEXT_DIM
            elif is_caught:
                mark, mcolor = "● 已收服", (72, 152, 80)
            elif is_ready:
                mark, mcolor = "◆ 可挑战", (40, 96, 48)
            elif need_champ and not champion:
                mark, mcolor = "○ 需冠军", TEXT_DIM
            elif badges < need:
                mark, mcolor = f"○ 需{need}徽章", TEXT_DIM
            else:
                mark, mcolor = "◆ 可挑战", (40, 96, 48)
            sc.text_right(231, y0 + 3.8, mark, size=7.4, fill=mcolor)

        sc.footer("◆ 前往栖息地后用 /神兽 挑战")
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 传说图鉴渲染失败: %s", e)
        return b""


# ══════════════════════════════════════════════════════════════════
# 2. 早间新闻
# ══════════════════════════════════════════════════════════════════
def render_news(day: int, *, world_events: list[str] = (), player_events: list[str] = (),
                weather_zh: str = "", region_zh: str = "", location_zh: str = "",
                daytime_zh: str = "", locks: list[str] = (),
                scale: int = SCALE_DEFAULT) -> bytes:
    """早间新闻:顶部信息条 + 世界 / 个人 两段播报(经典对话框)。"""
    try:
        # 事件多就把画布加高 —— 不再为了固定高度裁掉事件(用户要求)
        # 先按真实字宽把每条事件折行 → 用**实测行数**算画布高度(不裁事件)
        _meas = Screen(scale=1)
        _aw = max(60.0, 236 - 14)
        _world_rows: list[str] = []
        for _it in (list(_lines(world_events, limit=99)) or ["世界很平静。"]):
            _world_rows.extend(_wrap_rows(_meas, f"· {_it}", _aw, size=8))
        _mine_rows: list[str] = []
        for _it in (list(_lines(player_events, limit=99)) or ["今天你还没有特别的消息。"]):
            _mine_rows.extend(_wrap_rows(_meas, f"· {_it}", _aw, size=8))
        # 两个框的高度必须用同一套公式(16.5 + 行数*9.8 + 7);个人框原来多算
        # 了 2 行(早期“尚未解锁”行的遗留),底部就比“世界”框多出一大截空白。
        _nw, _np = max(1, len(_world_rows)), max(1, len(_mine_rows))
        _lh = 9.8
        _wb = 35 + 16.5 + _nw * _lh + 7
        _pbt = _wb + 4
        _pbb = _pbt + 16.5 + _np * _lh + 7
        sc = Screen(scale=scale, h=int(max(LOGICAL_H, _pbb + 20)))
        sc.title_bar("早间新闻", right=f"第 {day} 天")

        # 天气 / 地区 / 地点 信息条
        # 信息条整体加高 2px(19..32),药丸与文字都上移 —— 7 号字在 scale=1 时
        # 墨迹高度比 scale=3 时更大,原来会压出信息条下边
        sc.window((5, 19, 235, 32), radius=2, shadow=False)
        chips = [
            # 昼夜放最前:太阳伊布/月亮伊布、玛狃拉这类进化按它判定
            (_str(daytime_zh), CHIP_TIME),
            (_str(weather_zh), CHIP_WEATHER),
            (_str(region_zh), CHIP_REGION),
            (_str(location_zh), CHIP_PLACE),
        ]
        cx = 10
        for label, color in chips:
            if not label:
                continue
            w = sc.tw(label, 7.0) + 8
            if cx + w > 231:
                break
            # 药丸框与字号都要留余量:7.4 号字的墨迹到 y+8.3,原来画到 29.9,
            # 比药丸底(28.5)和信息条(30)都低 —— 肉眼就是"文字压出边框"
            sc.d.rounded_rectangle([cx, 20.5, cx + w, 30], radius=2, fill=color,
                                   outline=BOX_EDGE)
            sc.text(cx + 4, 21.0, label, size=7.0, fill=(255, 255, 250))
            cx += w + 4
        if cx == 10:
            sc.text(10, 21.2, "天气与行踪:暂无记录", size=7.0, fill=TEXT_DIM)

        # 世界播报
        world_box = (4, 35, 236, int(_wb))
        _dialog(sc, world_box)
        world = _world_rows
        _dialog_text(sc, world_box, "◆ 世界", world, limit=99)

        # 个人播报 + 未解锁提示
        player_box = (4, int(_pbt), 236, int(_pbb))
        _dialog(sc, player_box)
        mine = list(_mine_rows)
        if not mine:
            mine = ["今天你还没有特别的消息。"]
        for lock in _lines(locks, limit=2):
            mine.append(f"× 尚未解锁:{lock}")
        _dialog_text(sc, player_box, "◆ 个人", mine, limit=99)

        sc.footer("◆ /今日 查看完整世界动态")
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 早间新闻渲染失败: %s", e)
        return b""


# ══════════════════════════════════════════════════════════════════
# 3. 世界大赛赛程(左侧 → 右侧的赛程树)
# ══════════════════════════════════════════════════════════════════
def render_tournament(rounds: list[str], *, best: int = 0, current: int = 0,
                      titles: list[str] = (), last_foe: str = "",
                      is_champion: bool = False, scale: int = SCALE_DEFAULT) -> bytes:
    """大赛赛程:三轮从左到右的树状赛程 + 状态 + 称号。"""
    try:
        sc = Screen(scale=scale)
        reach = max(_int(current), _int(best))
        sc.title_bar("世界大赛赛程", right="★ 世界冠军" if is_champion else f"最高 {reach} 轮")

        sc.window((5, 19, 235, 30), radius=2, shadow=False)
        foe = _str(last_foe)
        sc.text(10, 21.6, f"上一战对手:{foe}" if foe else "还没有交手记录。",
                size=7.6, fill=TEXT if foe else TEXT_DIM)

        names = [_str(x, f"第{i + 1}轮") for i, x in enumerate(list(rounds or [])[:3])]
        cols = [(5, 79), (83, 157), (161, 235)]
        ys = [94, 66, 38]
        node_h = 26
        d = sc.d

        # ── 连接线(先画,节点覆盖端点)──
        for i in range(len(cols) - 1):
            x1a = cols[i][1]
            x0b = cols[i + 1][0]
            yca = ys[i] + node_h // 2
            ycb = ys[i + 1] + node_h // 2
            midx = (x1a + x0b) / 2
            for seg in ([(x1a, yca), (midx, yca)],
                        [(midx, yca), (midx, ycb)],
                        [(midx, ycb), (x0b, ycb)]):
                d.line([seg[0][0], seg[0][1], seg[1][0], seg[1][1]],
                       fill=BOX_EDGE, width=2)

        # ── 三轮节点 ──
        for i, (x0, x1) in enumerate(cols):
            y0 = ys[i]
            y1 = y0 + node_h
            done = is_champion or i < reach
            cur = (not is_champion) and i == reach
            if cur:
                sc.highlight((x0, y0, x1, y1))
            sc.window((x0, y0, x1, y1), radius=2, shadow=(cur or is_champion))
            label = names[i] if i < len(names) else "待定"
            sc.text(x0 + 6, y0 + 3.2, label, size=8.6, fill=TEXT)
            if is_champion and i == 2:
                mark, mcolor = "★", GOLD
                sub = "已夺冠!"
            elif done:
                mark, mcolor = "●", (72, 152, 80)
                sub = "已晋级"
            elif cur:
                mark, mcolor = "◆", (40, 96, 48)
                sub = f"对手:{_fit(sc, foe, 40, 7.2)}" if foe else "挑战中"
            else:
                mark, mcolor = "○", TEXT_DIM
                sub = "等待中"
            sc.text_right(x1 - 6, y0 + 2.6, mark, size=10, fill=mcolor)
            sc.text(x0 + 6, y0 + 14.5, _fit(sc, sub, 58, 7.2), size=7.2, fill=TEXT_DIM)

        # ── 称号栏 ──
        box = (5, 126, 235, sc.content_bottom)
        sc.window(box, radius=2, shadow=False)
        title_list = _lines(titles, limit=3)
        if title_list:
            sc.text(10, 130, "称号", size=7.8, fill=TEXT_DIM)
            sc.text(34, 130, _fit(sc, " · ".join(title_list), 196, 8), size=8, fill=TEXT)
        else:
            sc.text(10, 131, "还没有获得称号,先打进八强吧。", size=8, fill=TEXT_DIM)

        sc.footer("◆ /大赛 挑战 · 一路打上世界冠军")
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 大赛赛程渲染失败: %s", e)
        return b""


# ══════════════════════════════════════════════════════════════════
# 4. 战斗结算
# ══════════════════════════════════════════════════════════════════
def _wrap_fit(sc, text: str, width: float, size: float, limit: int) -> list[str]:
    """折行;内容被截断时在末行补省略号。

    直接 `wrap(..., limit=n)` 会把超出的字符默默丢掉,行尾常留下半个词
    (例如「…获得 神奇糖果 ×3」被切成「…获」),看起来很脏。
    """
    full = sc.wrap(text, width, size=size, limit=99)
    if len(full) <= limit:
        return full
    out = full[:limit]
    last = out[-1]
    out[-1] = (last[:-1] + "…") if len(last) > 1 else "…"
    return out


def _inline_block(sc, top: float, rx0: int, label: str, lcolor, text: str, tcolor,
                  bottom: float, limit: int = 2) -> float:
    """「◆ 标签 正文」单块:框高随折行数自适应,顶部内联标签(省掉一整行标题)。"""
    size, line_h = 7.6, 8.8
    lw = sc.tw(label + " ", size)
    avail_w = 235 - rx0 - 12 - lw
    lines = _wrap_fit(sc, text, avail_w, size, max(1, limit)) or [""]
    h = min(4.5 + len(lines) * line_h, max(line_h, bottom - top))
    sc.window((rx0, top, 235, top + h), radius=2, shadow=False)
    sc.text(rx0 + 5, top + 2.5, label, size=size, fill=lcolor)
    for i, ln in enumerate(lines):
        ty = top + 2.5 + i * line_h
        if ty + 8 > bottom:
            break
        sc.text(rx0 + 5 + lw, ty, ln, size=size, fill=tcolor)
    return top + h + 2


def _vcenter_y(sc, box, text_str: str, size: float) -> float:
    """把一行字在框里**垂直居中**的绘制 y(逻辑坐标)。

    以前胜负横幅是写死的 `24.5` —— 字号 13 时墨迹偏上,看着贴在横幅上半部分。
    这里用字体墨迹框(`fonts.bbox`)算真实高度,再把它摆到框的正中。
    """
    y0, y1 = box[1], box[3]
    try:
        from .fonts import bbox as _bbox
        _x0, by0, _x1, by1 = _bbox(str(text_str), round(size * sc.scale))
        ink = (by1 - by0) / sc.scale
        return y0 + (y1 - y0 - ink) / 2 - by0 / sc.scale
    except Exception:
        return (y0 + y1) / 2 - size / 2

def render_battle_result(*, outcome: str, title: str = "", lines: list[str] = (),
                         rewards: list[str] = (), growth: list[str] = (),
                         mon: dict | None = None, footer: str = "",
                         scale: int = SCALE_DEFAULT) -> bytes:
    """战斗结算:胜负横幅 + 结算台词 + 奖励 / 成长 + 我方精灵状态。"""
    try:
        sc = Screen(scale=scale)
        key = _str(outcome, "win").lower()
        label, color = OUTCOME_STYLE.get(key, ("结算", (140, 138, 120)))
        sc.title_bar(_str(title, "战斗结算"))

        # 胜负横幅
        banner = (5, 19, 235, 41)
        d = sc.d
        d.rounded_rectangle(list(banner), radius=3, fill=color, outline=BOX_EDGE)
        d.rounded_rectangle([banner[0] + 2, banner[1] + 2, banner[2] - 2, banner[3] - 2],
                            radius=2, outline=tuple(min(255, c + 50) for c in color))
        sc.text_center(120, _vcenter_y(sc, banner, label, 13), label, size=13,
                       fill=(255, 255, 250), stroke=0.7,
                       sfill=BOX_SHADOW)

        mon = mon if isinstance(mon, dict) else None
        rx0 = 5
        if mon:
            panel = (5, 44, 82, sc.content_bottom)
            sc.window(panel, radius=3)
            sp = _str(mon.get("species"))
            # 结算画面的立绘放大一档(实测反馈太小):几乎占满左侧面板,
            # 仍在放大层单次重采样,不会糊
            sc.sprite(sp, ground=(43, 111), factor=1.5, bounds=(66, 58), hires=True)
            name = _fit(sc, _str(mon.get("name"), "?"), 66, 9)
            sc.text(11, 110, name, size=9, fill=TEXT)
            gender, gcolor = gender_symbol(_str(mon.get("gender")))
            gw = sc.tw(name, 9)
            if gender:
                sc.text(11 + gw + 1, 110, gender, size=9, fill=gcolor)
            sc.text(11, 121, f"Lv{_int(mon.get('level'))}", size=8, fill=TEXT)
            ratio = _ratio(mon.get("cur_hp"), mon.get("max_hp"))
            hpcolor = HP_OK if ratio > 0.5 else (HP_MID if ratio > 0.2 else HP_LOW)
            sc.text_right(77, 122, f"{_int(mon.get('cur_hp'))}/{_int(mon.get('max_hp'))}",
                          size=7.4, fill=hpcolor)
            sc.hp_bar((11, 131, 66, 6), ratio)
            rx0 = 86

        # 结算台词(经典对话框)
        talk = (rx0, 44, 235, 96)
        _dialog(sc, talk)
        body = _lines(lines, limit=3) or [label]
        _dialog_text(sc, talk, "◆ 战报", body)

        # 奖励 / 成长:框高随文本自适应。
        # 原来的框是写死高度的(奖励 99~121 却按 110 起画 2 行 → 第 2 行到 126,
        # 直接压进下面的成长框),奖励文本一长就越界。
        # 战败/认输/逃跑时**不显示奖励栏** —— 输了却列出"没有获得奖励"很怪,
        # 而且旧实现还会把战斗里发放的少量奖励(如经验)一并列出来。
        show_reward = key in ("win", "caught")
        rlabel = "◆ 奖励"
        rtext = "\n".join(_lines(rewards, limit=4)) if show_reward else ""
        if not show_reward:
            # 战败/认输/逃跑**没有奖励**(引擎也确实不发),但引擎会把
            # "被送回宝可梦中心、队伍已恢复"这类结算信息放进 rewards ——
            # 这些要照常展示,只是不该挂在"奖励"标题下面。
            info = "\n".join(_lines(rewards, limit=2))
            if info:
                rlabel, rtext = "◆ 结果", info
        # 每条独立成行(`Screen.wrap` 认 \n):用 " · " 连成一段会把换行位置
        # 落在条目中间,导致成长信息错挂到别的宝可梦名下
        gtext = "\n".join(_lines(growth, limit=4)) or "这次没有新的感悟。"
        lw = sc.tw("◆ 奖励 ", 7.6)
        bw = 235 - rx0 - 12 - lw
        lim_r = lim_g = 2
        n_r = len(sc.wrap(rtext, bw, size=7.6, limit=lim_r)) if rtext else 0
        n_g = len(sc.wrap(gtext, bw, size=7.6, limit=lim_g))
        while n_r + n_g > 3:  # 44+43 的可用高度只放得下 3 行正文
            if n_r >= n_g and lim_r > 1:
                lim_r -= 1
                n_r = len(sc.wrap(rtext, bw, size=7.6, limit=lim_r))
            elif lim_g > 1:
                lim_g -= 1
                n_g = len(sc.wrap(gtext, bw, size=7.6, limit=lim_g))
            else:
                break
        y = 99
        if rtext:
            color = (56, 96, 60) if show_reward else (108, 96, 132)
            y = _inline_block(sc, y, rx0, rlabel, color, rtext,
                              TEXT, 140, limit=lim_r)
        _inline_block(sc, y, rx0, "◆ 成长", (120, 84, 48), gtext,
                      TEXT if growth else TEXT_DIM, 142, limit=lim_g)

        sc.footer(_str(footer, OUTCOME_FOOT.get(key, "◆ 继续冒险吧!")))
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 战斗结算渲染失败: %s", e)
        return b""


# ══════════════════════════════════════════════════════════════════
from .growth import move_brief  # noqa: E402  (成长/待学列表要显示招式效果)


# 5. 成长 / 进化
# ══════════════════════════════════════════════════════════════════
def render_growth(mon: dict, *, before_level: int = 0, after_level: int = 0,
                  learned: list[str] = (), pending: list[str] = (),
                  evolved_from_zh: str = "", evolved_to_zh: str = "",
                  scale: int = SCALE_DEFAULT) -> bytes:
    """升级画面:等级跳变 + 经验条 + 学到的招式;若发生进化则画进化瞬间。"""
    try:
        sc = Screen(scale=scale)
        mon = mon if isinstance(mon, dict) else {}
        name = _str(mon.get("name"), "宝可梦")
        sp = _str(mon.get("species"))
        before = _int(before_level) or _int(mon.get("level"))
        after = _int(after_level) or _int(mon.get("level"))
        evolved = _str(evolved_to_zh) or _str(mon.get("evolved_to_zh"))

        if evolved:
            return _growth_evolution(sc, mon, sp, name, evolved_from_zh, evolved,
                                     learned, pending)

        # ── 等级面板 ──
        sc.title_bar("成长", right=f"{name} 变强了")
        panel = (5, 19, 150, 66)
        sc.window(panel, radius=3)
        sc.text(12, 22, "◆ 等级提升", size=8, fill=(56, 96, 60))
        bar_w = 122
        # 没升级就**不要画箭头**(否则出现"Lv5 → Lv5"这种看不懂的画面 —— 调用方
        # 现在只在真升级时才出成长卡,这里再兜一层)
        sc.text(12, 32, f"Lv{before}", size=11, fill=TEXT_DIM)
        if after > before:
            sc.text(48, 32.6, "→", size=12, fill=(96, 94, 80))
            sc.text(62, 30, f"Lv{after}", size=14, fill=(40, 88, 44))
        else:
            sc.text(12, 30, f"Lv{before}", size=14, fill=(40, 88, 44))
        # 经验条:旧位置画箭头,新位置用青色填充,体现"跳了一截"
        ratio_before = _ratio(mon.get("exp_pct"), 100.0)
        jump = min(1.0, ratio_before + 0.25)
        sc.exp_bar((14, 54, bar_w, 6), jump * 100.0)
        sc.d.rectangle([14, 54, 14 + int(bar_w * ratio_before), 60],
                       fill=(120, 144, 216))
        sc.text(14, 44.5, "EXP", size=6.8, fill=TEXT_DIM)
        arrow_x = 14 + int(bar_w * jump) - 2
        sc.text(arrow_x, 42.5, "↑", size=8.4, fill=(48, 128, 76))

        # ── 精灵小图 ──
        side = (154, 19, 235, 66)
        sc.window(side, radius=3)
        sc.sprite(sp, ground=(194, 62), factor=1.18, bounds=(74, 42),
                  shiny=bool(mon.get("shiny")), hires=True)

        # ── 学会的招式 ──
        lb = (5, 69, 235, 105)
        sc.window(lb, radius=2, shadow=False)
        sc.text(10, 71.5, "◆ 学会了", size=7.8, fill=(56, 96, 60))
        # 写出招式**效果**(只给名字看不出这招做什么)
        learnt = "\n".join(move_brief(mv) for mv in list(learned)[:4]) \
            or "这次没有学会新招式。"
        for i, ln in enumerate(sc.wrap(learnt, 214, size=8, limit=3)):
            sc.text(12, 81 + i * 9, ln, size=8, fill=TEXT if learned else TEXT_DIM)

        # ── 待替换 ──
        pb = (5, 108, 235, sc.content_bottom)
        sc.window(pb, radius=2, shadow=False)
        sc.text(10, 110.5, "○ 待替换招式", size=7.8, fill=(140, 96, 56))
        pend = "\n".join(move_brief(mv) for mv in list(pending)[:4]) \
            or "招式栏还有空位,不用替换。"
        for i, ln in enumerate(sc.wrap(pend, 214, size=8, limit=3)):
            sc.text(12, 120 + i * 9, ln, size=8, fill=TEXT if pending else TEXT_DIM)

        sc.footer("◆ /学招 替换招式 · 继续冒险!")
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 成长界面渲染失败: %s", e)
        return b""


def _growth_evolution(sc: Screen, mon: dict, sp: str, name: str, from_zh: str,
                      to_zh: str, learned: list[str], pending: list[str]) -> bytes:
    """进化瞬间:前 → 后,放大后的新形态 + 「咦……?」旁白。"""
    sc.title_bar("进化!", right=f"{name} 的样子……")
    cap = (5, 19, 235, 42)
    _dialog(sc, cap)
    sc.text(12, 24.5, "咦……?", size=10.5, fill=MSG_TEXT, stroke=0.6, sfill=MSG_SHADOW)
    sc.text(52, 26.5, f"什么!{_str(from_zh, name)}的样子……", size=8.4, fill=MSG_TEXT)

    # 原有的招式信息留在下方
    sc.d.rectangle([4, 44, 236, 122], fill=BG)
    before_sp = _str(mon.get("pre_species")) or _species_key(from_zh) or sp
    sc.sprite(before_sp, ground=(58, 106), factor=1.05, bounds=(84, 70),
              silhouette=True, hires=True)
    # 闪光个体进化后仍然是闪光:进化瞬间也要用异色立绘
    sc.sprite(sp, ground=(182, 106), factor=1.2, bounds=(94, 70),
              shiny=bool(mon.get("shiny")), hires=True)
    sc.text_center(120, 78, "→", size=22, fill=(96, 94, 80))
    sc.text_center(56, 109, _str(from_zh, "之前"), size=8, fill=TEXT_DIM)
    sc.text_center(184, 109, _str(to_zh, name), size=9.4, fill=(40, 88, 44))

    box = (5, 124, 235, sc.content_bottom)
    sc.window(box, radius=2, shadow=False)
    extra = _lines(learned, limit=2)
    msg = f"恭喜!{_str(from_zh, name)} 进化成了 {to_zh}!"
    if extra:
        msg += " 学会了 " + "、".join(extra) + "!"
    for i, ln in enumerate(sc.wrap(msg, 214, size=8, limit=2)):
        sc.text(10, 127 + i * 9, ln, size=8, fill=TEXT)

    sc.footer("◆ 新的力量觉醒了。")
    return sc.finish()


# ══════════════════════════════════════════════════════════════════
# 6. 捕获成功
# ══════════════════════════════════════════════════════════════════
def render_gotcha(mon: dict, *, ball_zh: str = "精灵球", ball_key: str = "",
                  dex_line: str = "", scale: int = SCALE_DEFAULT) -> bytes:
    """捕获成功:聚光灯里的精灵 + 名字 / 等级 / 性别 + 图鉴进度 + 收服印章。"""
    try:
        sc = Screen(scale=scale)
        mon = mon if isinstance(mon, dict) else {}
        # 名字里可能已经带了 ✨(复用 _mon_view);这里有自己的闪光标识,去掉避免叠两个
        name = _str(mon.get("name"), "宝可梦").lstrip("✨ ") or "宝可梦"
        sp = _str(mon.get("species"))
        shiny = bool(mon.get("shiny"))

        # ── 聚光灯 + 光芒(先画背景,再让标题条盖住上缘)──
        d = sc.d
        cx, cy = 80, 84
        for i in range(12):
            a = i * math.pi / 6
            x_off = int(cx + 92 * math.cos(a))
            y_off = int(cy + 92 * math.sin(a))
            d.polygon([(cx, cy), (x_off, y_off),
                       (int(cx + 92 * math.cos(a + 0.26)),
                        int(cy + 92 * math.sin(a + 0.26)))], fill=RAY)
        d.ellipse([cx - 52, cy - 52, cx + 52, cy + 52], fill=SPOT_2, outline=BOX_EDGE)
        d.ellipse([cx - 44, cy - 44, cx + 44, cy + 44], fill=SPOT_1)
        sc.sprite(sp, ground=(cx, cy + 32), factor=1.05, bounds=(80, 78),
                  shiny=shiny, hires=True)

        if shiny:
            # 闪光捕获:聚光灯再加一圈金色,标题也好一眼看出与众不同
            d.ellipse([cx - 46, cy - 46, cx + 46, cy + 46], outline=(246, 206, 84),
                      width=2)
        title = "✨ 闪光·捕获成功!" if shiny else "捕获成功!"
        sc.title_bar(title, right=_str(ball_zh, "精灵球"))

        # ── 右侧资料卡 ──
        panel = (124, 19, 235, 120)
        sc.window(panel, radius=3)
        sc.text(130, 22.5,
                f"✨ 获得了闪光(异色){name}!" if shiny else f"获得了 {name}!",
                size=8.6, fill=(196, 156, 24) if shiny else TEXT)
        sc.text(130, 34, _fit(sc, name, 100, 13), size=13,
                fill=(196, 156, 24) if shiny else (40, 88, 44))
        gender, gcolor = gender_symbol(_str(mon.get("gender")))
        gw = sc.tw(_fit(sc, name, 100, 13), 13)
        if gender:
            sc.text(130 + gw + 2, 34, gender, size=13, fill=gcolor)
        sc.text(130, 52, f"Lv {_int(mon.get('level'))}", size=9, fill=TEXT)

        # 精灵球:按实际使用的球画(高级球/大师球在捕获画面里要能看出来)
        sc.item_icon(str(ball_key or "ball"), 130, 64, 11)
        sc.text(146, 66, _str(ball_zh, "精灵球"), size=8.4, fill=TEXT)

        dex = _str(dex_line, "图鉴已记录。")
        for i, ln in enumerate(sc.wrap(dex, 100, size=7.8, limit=2)):
            sc.text(130, 84 + i * 9.4, ln, size=7.8, fill=TEXT_DIM)

        # 收服印章
        stamp = (146, 124, 232, sc.content_bottom - 1)
        sc.d.rounded_rectangle(list(stamp), radius=3, fill=(250, 238, 226),
                               outline=STAMP_RED, width=2)
        sc.d.rectangle([stamp[0] + 3, stamp[1] + 3, stamp[2] - 3, stamp[3] - 3],
                       outline=STAMP_RED)
        sc.text_center(189, 127, "收服", size=10, fill=STAMP_RED, stroke=0.4,
                       sfill=(250, 238, 226))

        sc.footer("◆ 闪光个体稀有度极高,好好珍惜!" if shiny
                  else "◆ 图鉴又厚了一点,继续收集吧!")
        return sc.finish()
    except Exception as e:  # 渲染失败回退文本
        logger.debug("宝可梦世界: 捕获界面渲染失败: %s", e)
        return b""
