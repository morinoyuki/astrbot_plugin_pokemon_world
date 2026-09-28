"""角色台词:开战前的挑衅与被打败后的收场(按原著人设写,避免千篇一律)。

用法:
    banter.pre_battle(kind, meta)   # 开战前(敌人先说话)
    banter.defeat(kind, meta)       # 玩家打赢后(对方认输/撤退)

`kind` 就是 `BATTLE_KINDS` 里的键(wild / trainer / gym / trial / elite /
champion / rocket / legend / tournament)。台词按“具体到人到事”的顺序挑:
    道馆馆主 > 组织首领 > 联盟(四天王/冠军) > 大赛 > 普通训练家 > 野生
找不到人设就退回该类型的通用台词,绝不返回空字符串以外的东西。
"""

from __future__ import annotations

import random

# ── 火箭队三人组(武藏/小次郎/喵喵)────────────────────────────
TRIO_NAMES = "武藏、小次郎和喵喵"
TRIO_MOTTO = [
    "既然你诚心诚意地发问了,我们就大发慈悲地告诉你!",
    "为了防止世界被破坏,为了保护世界的和平!",
    "贯彻爱与真实的邪恶,可爱又迷人的反派角色!",
    "武藏!小次郎!我们是穿梭在银河的火箭队!",
    "白洞,白色的明天在等着我们!就是这样,喵!",
]
TRIO_BATTLE = (
    "「{motto}」\n"
    "—— {names} 跳了出来,盯上了你的宝可梦!\n"
    "(一句台词都不许抢,老老实实先挨完这段开场白)"
)
TRIO_LOSE = (
    "「好讨厌的感觉啊 —— !」\n"
    "{names} 又一次被你们打飞到了天边,化作一颗流星消失了。\n"
    "喵喵在半空中还不忘补一句:「下次我们还会回来的,喵!」"
)

# ── 各地区反派组织:登场宣言 + 撤退台词 ─────────────────────
ORGS = {
    "rocket": (
        "火箭队", "「妨碍火箭队的人,统统给我消失!」",
        "「可恶……组织不会放过你们的!」对方扔下烟雾弹撤退了。",
    ),
    "aqua": (
        "水舰队", "「海洋才是最棒的家园 —— 挡路的都沉下去吧!」",
        "「海洋的意志不会就此结束……」对方收起了宝可梦。",
    ),
    "magma": (
        "火岩队", "「大陆才是人类该走的路,别来碍事!」",
        "「大地会记住这一战的……」对方不甘心地退走了。",
    ),
    "galactic": (
        "银河队", "「感情是多余的,让我们把它从世界上抹掉。」",
        "「计划……还不会结束。」对方冷冷地退下了。",
    ),
    "plasma": (
        "等离子队", "「宝可梦应该从人类手中解放出来!」",
        "「理想……还没破灭。」对方带着宝可梦转身离开。",
    ),
    "flare": (
        "闪焰队", "「为了美丽的新世界,请你们退场吧。」",
        "「这个世界的美丽……我会记住的。」对方优雅地行礼退场。",
    ),
    "skull": (
        "骷髅队", "「这里是我们骷髅队的地盘,识相点交出来!」",
        "「呜哇——老大不会放过你们的!」对方一哄而散。",
    ),
    "yell": (
        "呐喊队", "「为了玛俐,谁都不能挡在前面!」",
        "「我们会一直喊下去的——!」对方喊着口号跑远了。",
    ),
    "star": (
        "天星队", "「别挡着我们的自由,来打一场吧!」",
        "「实力……确实是我们输了。」对方摘下了头巾。",
    ),
}
REGION_ORG = {
    "kanto": "rocket", "johto": "rocket", "hoenn": "aqua", "sinnoh": "galactic",
    "unova": "plasma", "kalos": "flare", "alola": "skull", "galar": "yell",
    "paldea": "star",
}

# ── 道馆馆主:开战前的个性台词(按人物名匹配)──────────────────
GYM_LEADERS = {
    "小刚": ("我的宝可梦是用岩石锻炼出来的,硬得很!让你见识一下什么叫坚不可摧!"),
    "小霞": ("水可柔可刚 —— 别以为带只草系就能轻松过我这一关!"),
    "马志士": ("我的电击可不是闹着玩的,Baby!让你尝尝真正的电压!"),
    "莉佳": ("草属性的宝可梦看似温柔,缠上你的时候可就麻烦了。"),
    "阿桔": ("忍者的战斗讲究先手。毒素,会慢慢替你收尸。"),
    "娜姿": ("我已经看见结局了 —— 你的宝可梦会输在我的超能力之下。"),
    "夏伯": ("火属性就是热情!让你感受一下我这座火山的温度!"),
    "坂木": ("……小看你的话会吃亏。来吧,让你看看真正的实力。"),
    "阿速": ("飞得最快的鸟才能看到最远的风景 —— 跟得上我的速度吗?"),
    "阿笔": ("虫宝可梦的进化值得细细品味,我让你也感受一下。"),
    "小茜": ("大奶罐可是很可爱的哦 —— 但它可一点都不弱!"),
    "松叶": ("幽灵宝可梦的恐怖,光用眼睛是看不出来的。"),
    "阿四": ("用身体去感受格斗吧!堂堂正正地来一场!"),
    "阿蜜": ("钢属性是最坚硬的盾,你的攻击能打破它吗?"),
    "柳伯": ("冰是很安静的属性 —— 但安静的东西往往最致命。"),
    "小椿": ("龙使的骄傲,可不是随便谁都能挑衅的。"),
    "娜琪": ("飞翔的心不会被束缚 —— 让你也体会一下天空的广阔。"),
    "阿李": ("以柔克刚!这就是我武道的全部。"),
    "小菘": ("冰之宝可梦的美丽与冷酷,你马上就能同时体会到。"),
    "菊老大": ("地面就是我的主场,站稳了别被震倒!"),
    "可尔妮": ("来吧,让我看看你和宝可梦之间的羁绊!"),
    "葛吉花": ("我的目光能看穿人内心的力量 —— 你的能看多远?"),
}
GYM_PRE_GENERIC = ("「想拿到这枚徽章?先问问我这身本事答不答应!」{who} 站在道馆中央等着你。")
GYM_LOSE = "「输得心服口服 —— 这枚{what}徽章归你了。」{who} 把徽章抛了过来。"
GYM_LOSE_WHAT = {"default": "道馆"}

ELITE_PRE = (
    "「四天王的门槛可不是谁都能迈过去的。」{who} 抱臂站在你面前,"
    "「让我看看,你能逼我使出几成力。」"
)
ELITE_LOSE = "「……了不起。接下来的那位,可比我更难缠。」{who} 侧身让开了通往下一战的路。"
CHAMPION_PRE = (
    "「走到这里,说明你已经是这个地区的顶点之一了。」{who} 收起笑容,"
    "「但冠军的位置,我还不想让出去 —— 拿出你的全力来吧!」"
)
CHAMPION_LOSE = (
    "「……是我输了。」{who} 收起最后一只宝可梦,郑重地伸出手,"
    "「这个地区的冠军,从现在起是你了。」"
)

TRAINER_PRE = [
    "「哟,看你的宝可梦挺精神的 —— 那就陪我打一场吧!」{who} 挡在了路中间。",
    "「路过的人都得先过我这关才行!」{who} 掏出了精灵球。",
    "「刚才那一下练得正好,来试试成果!」{who} 向你发起了挑战。",
    "「你的宝可梦看起来很强,我可不会手下留情!」{who} 摆出了架势。",
]
TRAINER_LOSE = [
    "「输了输了 —— 你的宝可梦调教得真好。」{who} 收起了精灵球,让开了路。",
    "「下次我一定赢回来!」{who} 一边嘟囔一边给你让路。",
    "「这一战学到了不少,谢谢指教!」{who} 对你竖起了大拇指。",
]
TRIAL_PRE = "「考验可不是走过场,用实力证明你能驾驭这股力量吧!」{who} 摆出了考验的架势。"
TRIAL_LOSE = "「合格了 —— 你已经配得上更强大的对手。」{who} 点了点头,让开了路。"
LEGEND_PRE = "「{who} 苏醒了 —— 它古老的目光扫过你,空气开始震动!」"
LEGEND_LOSE = "「{who} 低吼了一声,像是在认可你 —— 战斗结束了。」"
TOURNAMENT_PRE = "「大会的舞台没有第二次机会,输的人只能回家。」{who} 站在场地的另一头。"
TOURNAMENT_LOSE = "「好身手……祝你走到最后。」{who} 摘下帽子向你致意。"

WILD_PRE = ""          # 野生宝可梦不讲话


def _pick(rng: random.Random | None, items):
    r = rng or random
    return items[int(r.random() * len(items))] if items else ""


def pre_battle(kind: str, meta: dict | None = None, *, rng=None, region: str = "") -> str:
    """开战前的台词。拿不到人设就返回通用台词,拿不到类型就返回空串。"""
    meta = dict(meta or {})
    who = str(meta.get("name") or meta.get("leader") or "").strip()
    kind = str(kind or "")

    if kind == "gym":
        gym = meta.get("gym") or {}
        leader = str(gym.get("leader") or who or "馆主")
        line = GYM_LEADERS.get(leader)
        if line:
            return f"{leader}:{line}"
        return GYM_PRE_GENERIC.format(who=leader)
    if kind in ("rocket",):
        org = REGION_ORG.get(str(meta.get("region") or region or "kanto"), "rocket")
        if meta.get("trio"):
            return TRIO_BATTLE.format(motto=" ".join(TRIO_MOTTO), names=TRIO_NAMES)
        name, pre, _lose = ORGS.get(org, ORGS["rocket"])
        return f"{name}:{pre}"
    if kind == "elite":
        return ELITE_PRE.format(who=who or "四天王")
    if kind == "champion":
        return CHAMPION_PRE.format(who=who or "冠军")
    if kind == "trial":
        return TRIAL_PRE.format(who=who or "队长")
    if kind == "legend":
        return LEGEND_PRE.format(who=meta.get("species_zh") or who or "传说宝可梦")
    if kind == "tournament":
        return TOURNAMENT_PRE.format(who=who or "对手")
    if kind == "trainer":
        return _pick(rng, TRAINER_PRE).format(who=who or "训练家")
    return WILD_PRE


def defeat(kind: str, meta: dict | None = None, *, rng=None, region: str = "") -> str:
    """打赢之后的收场台词(不含战报)。"""
    meta = dict(meta or {})
    who = str(meta.get("name") or meta.get("leader") or "").strip()
    kind = str(kind or "")

    if kind == "gym":
        gym = meta.get("gym") or {}
        leader = str(gym.get("leader") or who or "馆主")
        return GYM_LOSE.format(what=GYM_LOSE_WHAT["default"], who=leader)
    if kind == "rocket":
        org = REGION_ORG.get(str(meta.get("region") or region or "kanto"), "rocket")
        if meta.get("trio"):
            return TRIO_LOSE.format(names=TRIO_NAMES)
        name, _pre, lose = ORGS.get(org, ORGS["rocket"])
        return f"{name}:{lose}"
    if kind == "elite":
        return ELITE_LOSE.format(who=who or "四天王")
    if kind == "champion":
        return CHAMPION_LOSE.format(who=who or "冠军")
    if kind == "trial":
        return TRIAL_LOSE.format(who=who or "队长")
    if kind == "legend":
        return LEGEND_LOSE.format(who=meta.get("species_zh") or who or "传说宝可梦")
    if kind == "tournament":
        return TOURNAMENT_LOSE.format(who=who or "对手")
    if kind == "trainer":
        return _pick(rng, TRAINER_LOSE).format(who=who or "训练家")
    return ""


def trio_event(trainer, day: int) -> dict | None:
    """火箭队三人组时不时出来刷存在感(确定性:同一天同一训练家结果一致)。

    `trainer` 需要提供 `uid`;按 (uid, day) 判定,一天最多一次,
    约 1/6 概率在 `/探索` 时撞见。
    """
    uid = str(getattr(trainer, "uid", "") or "")
    if not uid:
        return None
    rng = random.Random(f"trio:{uid}:{int(day)}")
    if rng.random() > 0.16:
        return None
    return {
        "title": "火箭队三人组出现了!",
        "kind": "rocket",
        "trio": True,
        "pre": TRIO_BATTLE.format(motto=" ".join(TRIO_MOTTO), names=TRIO_NAMES),
        "lose": TRIO_LOSE.format(names=TRIO_NAMES),
    }
