"""主线剧情:出发 → 道馆 → 击退敌对组织阴谋 → 联盟 → 冠军 → 世界大赛。

和真实游戏一样分章节推进,每章要么是"达到徽章数",要么是"在指定地点
击退敌对组织成员/干部"。剧情的推进只由内核判定(徽章 + 旗标 + 战斗胜负),
LLM 只负责把既成的章节结果写成叙事。
"""

from __future__ import annotations

from .dex import get_dex
from .util import clamp
from .world import WorldMap

# ── 各地区主线章节 ────────────────────────────────────────────────
# kind:
#   badge   —— 集齐 N 枚徽章
#   boss    —— 在 location 击败敌对组织(需先有前置徽章数)
#   league  —— 集齐徽章后挑战联盟(四天王 + 冠军)
#   epilogue—— 冠军之后的收尾(通常是神兽/世界大赛)
STORY: dict[str, dict] = {
    "kanto": {
        "org": "火箭队",
        "leader": "坂木",
        "stages": [
            {"key": "出发", "kind": "badge", "need": 0, "title": "真新镇出发",
             "desc": "从大木研究所领取御三家,踏上旅程。"},
            {"key": "首枚徽章", "kind": "badge", "need": 1, "title": "第一枚徽章",
             "desc": "挑战深灰市道馆,拿下灰色徽章。"},
            {"key": "月见山", "kind": "boss", "need": 2, "location": "mt-moon",
             "title": "月见山的火箭队", "org": "火箭队手下",
             "desc": "火箭队在月见山抢夺化石,去阻止他们。",
             "team": [("zubat", 11), ("ekans", 12), ("koffing", 13)]},
            {"key": "西尔佛公司", "kind": "boss", "need": 5, "location": "saffron-city",
             "title": "西尔佛公司事件", "org": "火箭队干部",
             "desc": "火箭队占据了西尔佛公司,击退他们。",
             "team": [("arbok", 34), ("weezing", 34), ("golbat", 35), ("marowak", 36)]},
            {"key": "关都联盟", "kind": "league", "need": 8,
             "title": "石英高原联盟",
             "desc": f"集齐 8 枚徽章,前往{WorldMap().node_zh('kanto-pokemon-league')}挑战四天王与冠军。"},
            {"key": "华蓝洞窟", "kind": "epilogue", "need": 8, "location": "cerulean-cave",
             "title": "洞窟深处的低鸣",
             "desc": "冠军之后,华蓝洞窟深处传来强大的气息 —— 去捕捉传说的宝可梦吧。"},
        ],
    },
    "johto": {
        "org": "火箭队残党",
        "leader": "坂木",
        "stages": [
            {"key": "出发", "kind": "badge", "need": 0, "title": "若叶镇出发",
             "desc": "从空木研究所领取御三家。"},
            {"key": "首枚徽章", "kind": "badge", "need": 1, "title": "第一枚徽章",
             "desc": "挑战桔梗市道馆。"},
            {"key": "呆呆兽之井", "kind": "boss", "need": 2, "location": "slowpoke-well",
             "title": "呆呆兽之井的骚动", "org": "火箭队残党",
             "desc": "火箭队残党在砍呆呆兽的尾巴,去制止。",
             "team": [("rattata", 9), ("zubat", 9), ("koffing", 11)]},
            {"key": "满金电台", "kind": "boss", "need": 7, "location": "goldenrod-city",
             "title": "满金市电台被占", "org": "火箭队干部",
             "desc": "火箭队占据了满金市电台,夺回它。",
             "team": [("houndour", 33), ("koffing", 32), ("weezing", 34), ("houndoom", 35)]},
            {"key": "城都联盟", "kind": "league", "need": 8, "title": "白银山联盟",
             "desc": "集齐 8 枚徽章,挑战城都四天王与冠军。"},
            {"key": "铃铛塔", "kind": "epilogue", "need": 8, "location": "bell-tower",
             "title": "铃铛塔的虹光",
             "desc": "冠军之后,铃铛塔顶传来清越的铃音 —— 去捕捉传说的宝可梦吧。"},
        ],
    },
    "hoenn": {
        "org": "水舰队与火岩队",
        "leader": "水梧桐 / 赤焰松",
        "stages": [
            {"key": "出发", "kind": "badge", "need": 0, "title": "未白镇出发",
             "desc": "从研究所领取御三家。"},
            {"key": "首枚徽章", "kind": "badge", "need": 1, "title": "第一枚徽章",
             "desc": "挑战卡那兹市道馆。"},
            {"key": "石之洞窟", "kind": "boss", "need": 2, "location": "granite-cave",
             "title": "石之洞窟的争执", "org": "敌方干部",
             "desc": "水舰队与火岩队在石之洞窟起了冲突。",
             "team": [("poochyena", 15), ("carvanha", 16), ("numel", 16)]},
            {"key": "海底洞窟", "kind": "boss", "need": 6, "location": "seafloor-cavern",
             "title": "海底洞窟的仪式", "org": "敌方首领",
             "desc": "敌方首领唤醒了超古代宝可梦 —— 阻止他们!",
             "team": [("mightyena", 41), ("crobat", 41), ("sharpedo", 43), ("camerupt", 43)]},
            {"key": "丰缘联盟", "kind": "league", "need": 8, "title": "彩幽联盟",
             "desc": "集齐 8 枚徽章,挑战丰缘四天王与冠军。"},
            {"key": "天空之柱", "kind": "epilogue", "need": 8, "location": "sky-pillar",
             "title": "天空之柱的天鸣",
             "desc": "冠军之后,天空之柱上盘旋着绿色的巨龙。"},
        ],
    },
    "sinnoh": {
        "org": "银河队",
        "leader": "赤日",
        "stages": [
            {"key": "出发", "kind": "badge", "need": 0, "title": "双叶镇出发",
             "desc": "从真砂镇研究所领取御三家。"},
            {"key": "首枚徽章", "kind": "badge", "need": 1, "title": "第一枚徽章",
             "desc": "挑战黑金市道馆。"},
            {"key": "百代市", "kind": "boss", "need": 2, "location": "eterna-city",
             "title": "银河队在百代市", "org": "银河队手下",
             "desc": "银河队的手下在百代市捣乱。",
             "team": [("zubat", 15), ("glameow", 16), ("stunky", 16)]},
            {"key": "天冠山", "kind": "boss", "need": 6, "location": "mt-coronet",
             "title": "天冠山上的阴谋", "org": "银河队干部",
             "desc": "银河队在天冠山顶举行仪式,去阻止他们。",
             "team": [("golbat", 40), ("skuntank", 41), ("bronzong", 42), ("toxicroak", 43)]},
            {"key": "神奥联盟", "kind": "league", "need": 8, "title": "铃兰岛联盟",
             "desc": "集齐 8 枚徽章,挑战神奥四天王与冠军。"},
            {"key": "枪之柱", "kind": "epilogue", "need": 8, "location": "spear-pillar",
             "title": "枪之柱的时空",
             "desc": "冠军之后,枪之柱上的时空开始扭曲。"},
        ],
    },
    "unova": {
        "org": "等离子队",
        "leader": "N / 魁奇思",
        "stages": [
            {"key": "出发", "kind": "badge", "need": 0, "title": "鹿子镇出发",
             "desc": "从红豆杉研究所领取御三家。"},
            {"key": "首枚徽章", "kind": "badge", "need": 1, "title": "第一枚徽章",
             "desc": "挑战三曜市道馆。"},
            {"key": "矢车森林", "kind": "boss", "need": 2, "location": "pinwheel-forest",
             "title": "等离子队的演说", "org": "等离子队手下",
             "desc": "等离子队宣称要从人类手中「解放」宝可梦,击退他们。",
             "team": [("sandile", 15), ("scraggy", 16), ("purrloin", 16)]},
            {"key": "古代之城", "kind": "boss", "need": 6, "location": "relic-castle",
             "title": "古代之城的野心", "org": "等离子队干部",
             "desc": "等离子队干部在古代之城寻找传说之龙。",
             "team": [("krokorok", 40), ("scrafty", 41), ("sigilyph", 42), ("cofagrigus", 43)]},
            {"key": "合众联盟", "kind": "league", "need": 8, "title": "合众联盟",
             "desc": "集齐 8 枚徽章,挑战合众四天王与冠军。"},
            {"key": "N的城堡", "kind": "epilogue", "need": 8, "location": "dragonspiral-tower",
             "title": "传说之龙的抉择",
             "desc": "冠军之后,龙螺旋之塔等待着你与传说之龙的对峙。"},
        ],
    },
    "kalos": {
        "org": "闪焰队",
        "leader": "弗拉达利",
        "stages": [
            {"key": "出发", "kind": "badge", "need": 0, "title": "朝香镇出发",
             "desc": "从名水镇领取御三家。"},
            {"key": "首枚徽章", "kind": "badge", "need": 1, "title": "第一枚徽章",
             "desc": "挑战白檀市道馆。"},
            {"key": "閃耀洞窟", "kind": "boss", "need": 2, "location": "glittering-cave",
             "title": "閃耀洞窟的闪焰队", "org": "闪焰队手下",
             "desc": "闪焰队占据了閃耀洞窟,夺回它。",
             "team": [("houndour", 20), ("scraggy", 21), ("croagunk", 21)]},
            {"key": "秘密基地", "kind": "boss", "need": 7, "location": "team-flare-secret-hq",
             "title": "闪焰队秘密基地", "org": "闪焰队干部",
             "desc": "直捣闪焰队秘密基地,阻止最终武器。",
             "team": [("mightyena", 46), ("toxicroak", 47), ("drapion", 48), ("honchkrow", 49)]},
            {"key": "卡洛斯联盟", "kind": "league", "need": 8, "title": "卡洛斯联盟",
             "desc": "集齐 8 枚徽章,挑战卡洛斯四天王与冠军。"},
            {"key": "终结洞窟", "kind": "epilogue", "need": 8, "location": "terminus-cave",
             "title": "终结洞窟的秩序",
             "desc": "冠军之后,终结洞窟的巨茧开始脉动。"},
        ],
    },
    "alola": {
        "org": "骷髅队",
        "leader": "古兹马",
        "stages": [
            {"key": "出发", "kind": "badge", "need": 0, "title": "利利小镇出发",
             "desc": "从库库伊博士处领取御三家。"},
            {"key": "首个考验", "kind": "badge", "need": 1, "title": "通过第一个考验",
             "desc": "完成葱郁洞窟的考验。"},
            {"key": "海滩洞穴", "kind": "boss", "need": 2, "location": "sandy-cave",
             "title": "骷髅队的挑衅", "org": "骷髅队手下",
             "desc": "骷髅队在海滩洞穴找麻烦。",
             "team": [("rattata", 15), ("drowzee", 16), ("salandit", 17)]},
            {"key": "以太乐园", "kind": "boss", "need": 6, "location": "aether-paradise",
             "title": "以太乐园的真相", "org": "以太基金会干部",
             "desc": "以太乐园里藏着究极之洞的秘密。",
             "team": [("bruxish", 45), ("mudsdale", 46), ("salazzle", 47)]},
            {"key": "阿罗拉联盟", "kind": "league", "need": 11, "title": "阿罗拉联盟",
             "desc": "集齐 11 枚考验印章,挑战阿罗拉四天王与冠军。"},
            {"key": "日轮祭坛", "kind": "epilogue", "need": 8, "location": "altar-of-the-sunne",
             "title": "日轮祭坛的光",
             "desc": "冠军之后,祭坛之上的时空洞开启。"},
        ],
    },
    "galar": {
        "org": "呐喊队",
        "leader": "马俐 / 聂凯",
        "stages": [
            {"key": "出发", "kind": "badge", "need": 0, "title": "化朗镇出发",
             "desc": "从丹帝处领取御三家。"},
            {"key": "首枚徽章", "kind": "badge", "need": 1, "title": "第一枚徽章",
             "desc": "挑战草路镇道馆。"},
            {"key": "草路镇", "kind": "boss", "need": 2, "location": "turffield",
             "title": "呐喊队的阻挠", "org": "呐喊队",
             "desc": "呐喊队一路尾随捣乱,给他们点教训。",
             "team": [("zigzagoon", 18), ("nickit", 19), ("scraggy", 20)]},
            {"key": "拳关市", "kind": "boss", "need": 7, "location": "hammerlocke",
             "title": "拳关市的骚乱", "org": "呐喊队干部",
             "desc": "呐喊队干部在拳关市闹事。",
             "team": [("linoone", 44), ("thievul", 45), ("scrafty", 46), ("toxicroak", 47)]},
            {"key": "伽勒尔联盟", "kind": "league", "need": 8, "title": "宫门市联盟",
             "desc": "集齐 8 枚徽章,挑战伽勒尔四天王与冠军。"},
            {"key": "微寐森林", "kind": "epilogue", "need": 8, "location": "slumbering-weald",
             "title": "微寐森林的剑与盾",
             "desc": "冠军之后,微寐森林的浓雾中浮现出两道身影。"},
        ],
    },
}


def region_stages(region: str) -> list[dict]:
    return list((STORY.get(region) or {}).get("stages") or [])


def progress(trainer, *, world: WorldMap | None = None, day: int = 0) -> list[dict]:
    """按当前状态自动推进"达标即完成"的章节,返回本次新完成的章节。"""
    world = world or WorldMap()
    region = trainer.region
    done: list[str] = list(trainer.flag(f"story:{region}", []) or [])
    newly: list[dict] = []
    for stage in region_stages(region):
        key = stage["key"]
        if key in done:
            continue
        earned = (
            stage["kind"] == "badge"
            and trainer.badge_count(region) >= int(stage["need"])
        ) or (stage["kind"] == "league" and bool(trainer.flag(f"champion:{region}")))
        if earned:
            done.append(key)
            newly.append(stage)
        # boss / epilogue 只能靠战斗或到达完成
    trainer.set_flag(f"story:{region}", done)
    _ = day
    return newly


def mark_stage(trainer, region: str, key: str) -> bool:
    done: list[str] = list(trainer.flag(f"story:{region}", []) or [])
    if key in done:
        return False
    done.append(key)
    trainer.set_flag(f"story:{region}", done)
    return True


def current_stage(trainer, *, world: WorldMap | None = None) -> dict | None:
    """返回当前待完成的章节(严格按章节顺序,不因徽章不足而跳章)。

    徽章不足时仍显示该章,由调用方提示"先拿徽章"——否则会出现
    "还没打月见山,主线却跳到联盟"这种错位。
    """
    world = world or WorldMap()
    region = trainer.region
    done: list[str] = list(trainer.flag(f"story:{region}", []) or [])
    for stage in region_stages(region):
        if stage["key"] not in done:
            return stage
    return None


def stage_locked(trainer, stage: dict) -> str:
    """返回该章节当前无法开战的原因("" 表示可以打)。"""
    if not stage:
        return "本地区主线已经完成"
    if stage["kind"] != "boss":
        return "当前章节不是战斗章节"
    region = trainer.region
    need = int(stage["need"])
    if trainer.badge_count(region) < need:
        return f"实力还不够 —— 需要 {need} 枚{WorldMap().region_zh(region)}徽章"
    if trainer.location != (stage.get("location") or ""):
        world = WorldMap()
        return f"敌方在 {world.node_zh(stage.get('location') or '')},先过去再说"
    return ""


def boss_meta(trainer, stage: dict) -> dict:
    """把剧情战斗打包成 battle.start 需要的 meta。"""
    specs = [
        {"species": sp, "level": int(lv)}
        for sp, lv in (stage.get("team") or [])
    ]
    return {
        "kind": "rocket",
        "title": f"{stage.get('title')} —— {stage.get('org') or '敌方'}",
        "story": stage,
        "event_id": f"story:{trainer.region}:{stage['key']}",
        "location": stage.get("location") or trainer.location,
        "region": trainer.region,
        "team": specs,
        "weather": "",
    }


def chapter_text(trainer, *, world: WorldMap | None = None) -> str:
    """主线进度面板。"""
    world = world or WorldMap()
    region = trainer.region
    info = STORY.get(region) or {}
    stages = region_stages(region)
    done: list[str] = list(trainer.flag(f"story:{region}", []) or [])
    lines = [
        f"📜 {world.region_zh(region)} 主线 —— 对抗{info.get('org', '敌对组织')}"
        f"(首领:{info.get('leader', '?')})",
        f"进度 {len(done)}/{len(stages)} · 徽章 {trainer.badge_count(region)}/{len(world.gyms(region)) or 8}",
    ]
    cur = current_stage(trainer, world=world)
    for stage in stages:
        mark = "✅" if stage["key"] in done else ("▶️" if cur and stage["key"] == cur["key"] else "⬜")
        lines.append(f"{mark} {stage['title']}")
    if cur:
        lines.append("")
        lines.append(f"▶️ 当前目标:{cur['title']}")
        lines.append(f"　{cur['desc']}")
        if cur["kind"] == "badge":
            lines.append(f"　→ 再获得 {int(cur['need']) - trainer.badge_count(region)} 枚徽章")
        elif cur["kind"] == "boss":
            loc = world.node_zh(cur.get("location") or "")
            if trainer.badge_count(region) < int(cur["need"]):
                lines.append(
                    f"　→ 先获得 {int(cur['need'])} 枚徽章"
                    f"(当前 {trainer.badge_count(region)} 枚)"
                )
            else:
                lines.append(f"　→ 前往 {world.region_zh(region)}·{loc}")
                here = trainer.location == (cur.get("location") or "")
                lines.append(
                    "　→ "
                    + (
                        "就在这里!输入 `/主线 挑战` 开战"
                        if here
                        else "到达后用 `/主线 挑战` 开战"
                    )
                )
        elif cur["kind"] == "league":
            lines.append("　→ `/联盟 挑战`")
        elif cur["kind"] == "epilogue":
            loc = world.node_zh(cur.get("location") or "")
            lines.append(f"　→ 前往 {loc},用 `/神兽` 寻找传说")
    else:
        lines.append("")
        lines.append("🎉 本地区主线已全部完成!可前往下一个地区,或参加 `/大赛`。")
    return "\n".join(lines)


def tournament_unlocked(trainer, *, world: WorldMap | None = None) -> bool:
    world = world or WorldMap()
    return any(
        trainer.flag(f"champion:{r}") for r in world.regions_with_data()
    )


# ── 世界大赛(冠军之后解锁) ───────────────────────────────────────
TOURNAMENT_ROUNDS = [
    ("八强赛", 1),
    ("四强赛", 2),
    ("决赛", 3),
]
TOURNAMENT_TITLES = ["地区的四天王", "别区冠军", "世界冠军"]
TOURNAMENT_LEVELS = [(70, 74), (76, 80), (82, 88)]


def tournament_meta(trainer, round_index: int, *, world: WorldMap | None = None, rng=None) -> dict:
    """世界大赛的一轮:对手从其它地区的冠军/四天王里挑。"""
    world = world or WorldMap()
    rng = rng or __import__("random")
    others = [
        r for r in world.regions_with_data()
        if r != trainer.region and (world.champion(r) or world.elite4(r))
    ]
    pool: list[dict] = []
    for r in others:
        ch = world.champion(r)
        if ch:
            pool.append({"name": ch.get("name"), "region": r, "team": ch.get("team")})
        pool.extend(
            {"name": e.get("name"), "region": r, "team": e.get("team")}
            for e in world.elite4(r)
        )
    if not pool:
        pool = [{"name": "世界冠军", "region": trainer.region, "team": []}]
    foe = rng.choice(pool)
    lo, hi = TOURNAMENT_LEVELS[min(round_index, len(TOURNAMENT_LEVELS) - 1)]
    dex = get_dex()
    specs: list[dict] = []
    for m in (foe.get("team") or [])[:6]:
        sp = m.get("species") if isinstance(m, dict) else None
        if not sp or sp not in dex.species:
            continue
        # 取对手的真实等级,只把它钳进该轮的区间 —— 旧写法 clamp(hi, lo, hi)
        # 恒等于 hi,所有对手都被拉到该轮上限。
        real = int(m.get("level") or hi) if isinstance(m, dict) else hi
        specs.append({"species": sp, "level": int(clamp(max(real, lo), lo, hi))})
    if not specs:
        specs = [{"species": "dragonite", "level": hi}]
    return {
        "kind": "tournament",
        "title": f"世界大赛 · {TOURNAMENT_ROUNDS[min(round_index, 2)][0]} —— {foe.get('name')}",
        "tournament_round": round_index,
        "team": specs,
        "location": trainer.location,
        "region": trainer.region,
    }
