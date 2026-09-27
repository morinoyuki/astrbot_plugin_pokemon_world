"""对战常用道具表(手工整理,中文名 + 说明 + 引擎效果参数)。

`effect` 的字段由 `engine.py` 解释;未实现的字段只作为说明展示给 LLM,
不会影响结算。数据量刻意控制在对战常用范围内。
"""

from __future__ import annotations

from .dex import _norm, get_dex

# effect 支持的键(engine 读取):
#   stat_mult: {stat: 倍率}              常驻数值倍率(如讲究系列 / 突击背心)
#   type_mult: {Type: 倍率}              指定属性招式威力倍率(如木炭)
#   category_mult: {Physical/Special: 倍率}
#   damage_mult: 倍率                    全招式威力倍率(生命宝珠)
#   super_effective_mult: 倍率           效果拔群时额外倍率(达人带)
#   end_turn_heal / end_turn_damage: 分数
#   on_low_hp: {threshold, heal}         低血量自动回复树果
#   cure_status: bool                    异常状态自动回复(木子果)
#   self_status: brn/tox                 回合结束自我施加异常(火珠/毒珠)
#   contact_recoil: 分数                 被接触打中反伤(凸凸头盔)
#   focus_sash: bool                     满血被秒时留 1 HP
#   no_hazards: bool                     无视入场钉子
#   ground_immune: bool                  免疫地面
#   no_secondary: bool                   无视对手附加效果
#   no_stat_drop: bool                   不会被降能力
#   no_weather_damage: bool              不吃天气伤害
#   screen_turns / weather_turns / terrain_turns: 8
#   weather: 对应天气
#   multi_hit_min: 连续技最低次数
#   choice_lock: bool
#   no_status_moves: bool
#   requires_nfe: bool                   仅未进化完全时生效(进化奇石)

_ITEMS: dict[str, dict] = {
    "leftovers": {"zh": "吃剩的东西", "desc": "每回合结束回复最大 HP 的 1/16。", "effect": {"end_turn_heal": 1 / 16}},
    "black-sludge": {"zh": "黑色污泥", "desc": "毒属性每回合回复 1/16;其他属性每回合损失 1/8。", "effect": {"end_turn_heal_poison": 1 / 16, "end_turn_damage_nonpoison": 1 / 8}},
    "sitrus-berry": {"zh": "文柚果", "desc": "HP 低于一半时回复最大 HP 的 1/4。", "effect": {"on_low_hp": {"threshold": 0.5, "heal": 0.25}}},
    "lum-berry": {"zh": "木子果", "desc": "陷入异常状态时自动治愈。", "effect": {"cure_status": True}},
    "life-orb": {"zh": "生命宝珠", "desc": "招式威力 ×1.3,攻击后损失最大 HP 的 1/10。", "effect": {"damage_mult": 1.3, "recoil": 0.1}},
    "choice-band": {"zh": "讲究头带", "desc": "攻击 ×1.5,但只能使用同一招式。", "effect": {"stat_mult": {"atk": 1.5}, "choice_lock": True}},
    "choice-specs": {"zh": "讲究眼镜", "desc": "特攻 ×1.5,但只能使用同一招式。", "effect": {"stat_mult": {"spa": 1.5}, "choice_lock": True}},
    "choice-scarf": {"zh": "讲究围巾", "desc": "速度 ×1.5,但只能使用同一招式。", "effect": {"stat_mult": {"spe": 1.5}, "choice_lock": True}},
    "assault-vest": {"zh": "突击背心", "desc": "特防 ×1.5,但无法使用变化招式。", "effect": {"stat_mult": {"spd": 1.5}, "no_status_moves": True}},
    "eviolite": {"zh": "进化奇石", "desc": "未进化完全时防御与特防 ×1.5。", "effect": {"stat_mult": {"def": 1.5, "spd": 1.5}, "requires_nfe": True}},
    "focus-sash": {"zh": "气势披带", "desc": "满 HP 被一击击倒时留下 1 HP(每场一次)。", "effect": {"focus_sash": True}},
    "rocky-helmet": {"zh": "凸凸头盔", "desc": "被接触类招式命中时,反弹攻击者最大 HP 的 1/6。", "effect": {"contact_recoil": 1 / 6}},
    "heavy-duty-boots": {"zh": "厚底靴", "desc": "不会受到入场陷阱的伤害与效果。", "effect": {"no_hazards": True}},
    "light-clay": {"zh": "光之黏土", "desc": "光墙 / 反射壁持续时间延长至 8 回合。", "effect": {"screen_turns": 8}},
    "heat-rock": {"zh": "炽热岩石", "desc": "大晴天持续时间延长至 8 回合。", "effect": {"weather_turns": 8, "weather": "sun"}},
    "damp-rock": {"zh": "潮湿岩石", "desc": "求雨持续时间延长至 8 回合。", "effect": {"weather_turns": 8, "weather": "rain"}},
    "smooth-rock": {"zh": "平滑岩石", "desc": "沙暴持续时间延长至 8 回合。", "effect": {"weather_turns": 8, "weather": "sand"}},
    "icy-rock": {"zh": "冰冷岩石", "desc": "下雪持续时间延长至 8 回合。", "effect": {"weather_turns": 8, "weather": "snow"}},
    "terrain-extender": {"zh": "大地膜", "desc": "场地持续时间延长至 8 回合。", "effect": {"terrain_turns": 8}},
    "booster-energy": {"zh": "驱劲能量", "desc": "悖谬宝可梦的古代/未来特性本场立即发动。", "effect": {"paradox_boost": True}},
    "loaded-dice": {"zh": "作弊骰子", "desc": "连续招式至少命中 4 次。", "effect": {"multi_hit_min": 4}},
    "covert-cloak": {"zh": "密探斗篷", "desc": "不会受到对手招式的追加效果影响。", "effect": {"no_secondary": True}},
    "clear-amulet": {"zh": "清净坠饰", "desc": "不会被对手降低能力。", "effect": {"no_stat_drop": True}},
    "weakness-policy": {"zh": "弱点保险", "desc": "被效果拔群招式命中时,攻击与特攻 +2。", "effect": {"on_super_effective": {"boosts": {"atk": 2, "spa": 2}}}},
    "throat-spray": {"zh": "爽喉喷雾", "desc": "使用声音招式后特攻 +1。", "effect": {"on_sound_move": {"boosts": {"spa": 1}}}},
    "air-balloon": {"zh": "气球", "desc": "漂浮,免疫地面招式;被击中后爆掉。", "effect": {"ground_immune": True}},
    "safety-goggles": {"zh": "防尘护目镜", "desc": "不受天气伤害与粉末招式影响。", "effect": {"no_weather_damage": True}},
    "expert-belt": {"zh": "达人带", "desc": "对效果拔群的目标招式威力 ×1.2。", "effect": {"super_effective_mult": 1.2}},
    "muscle-band": {"zh": "力量头带", "desc": "物理招式威力 ×1.1。", "effect": {"category_mult": {"Physical": 1.1}}},
    "wise-glasses": {"zh": "博识眼镜", "desc": "特殊招式威力 ×1.1。", "effect": {"category_mult": {"Special": 1.1}}},
    "toxic-orb": {"zh": "剧毒宝珠", "desc": "回合结束时自身陷入剧毒。", "effect": {"self_status": "tox"}},
    "flame-orb": {"zh": "火焰宝珠", "desc": "回合结束时自身陷入灼伤。", "effect": {"self_status": "brn"}},
    "silk-scarf": {"zh": "丝绸围巾", "desc": "一般属性招式威力 ×1.2。", "effect": {"type_mult": {"Normal": 1.2}}},
}

# 属性增强道具(1.2×)
_TYPE_ITEMS = {
    "charcoal": ("木炭", "Fire"),
    "mystic-water": ("神秘水滴", "Water"),
    "miracle-seed": ("奇迹种子", "Grass"),
    "magnet": ("磁铁", "Electric"),
    "never-melt-ice": ("不融冰", "Ice"),
    "black-belt": ("黑带", "Fighting"),
    "poison-barb": ("毒针", "Poison"),
    "soft-sand": ("软沙", "Ground"),
    "sharp-beak": ("锐利鸟嘴", "Flying"),
    "twisted-spoon": ("弯曲的汤匙", "Psychic"),
    "silver-powder": ("银粉", "Bug"),
    "hard-stone": ("硬石头", "Rock"),
    "spell-tag": ("诅咒之符", "Ghost"),
    "dragon-fang": ("龙之牙", "Dragon"),
    "black-glasses": ("黑色眼镜", "Dark"),
    "metal-coat": ("金属膜", "Steel"),
    "fairy-feather": ("妖精之羽", "Fairy"),
}
for _k, (_zh, _t) in _TYPE_ITEMS.items():
    _ITEMS[_k] = {
        "zh": _zh,
        "desc": f"{_t} 属性招式威力 ×1.2。",
        "effect": {"type_mult": {_t: 1.2}},
    }


ITEMS: dict[str, dict] = _ITEMS

# 进化相关道具(使用道具进化 / 携带升级进化 / 交换进化)。
# 同时作为可携带道具与背包道具收录;对战时无特殊效果。
_EVO_ITEMS: dict[str, tuple[str, str]] = {
    "fire-stone": ("火之石", "对特定宝可梦使用后进化。"),
    "water-stone": ("水之石", "对特定宝可梦使用后进化。"),
    "thunder-stone": ("雷之石", "对特定宝可梦使用后进化。"),
    "leaf-stone": ("叶之石", "对特定宝可梦使用后进化。"),
    "moon-stone": ("月之石", "对特定宝可梦使用后进化。"),
    "sun-stone": ("日之石", "对特定宝可梦使用后进化。"),
    "shiny-stone": ("光之石", "对特定宝可梦使用后进化。"),
    "dusk-stone": ("暗之石", "对特定宝可梦使用后进化。"),
    "dawn-stone": ("觉醒之石", "对特定宝可梦使用后进化。"),
    "ice-stone": ("冰之石", "对特定宝可梦使用后进化。"),
    "sweet-apple": ("甜甜苹果", "对特定宝可梦使用后进化。"),
    "tart-apple": ("酸酸苹果", "对特定宝可梦使用后进化。"),
    "syrupy-apple": ("糖浆苹果", "对特定宝可梦使用后进化。"),
    "cracked-pot": ("破裂的茶壶", "对特定宝可梦使用后进化。"),
    "unremarkable-teacup": ("平凡的茶杯", "对特定宝可梦使用后进化。"),
    "auspicious-armor": ("祝福之铠", "对特定宝可梦使用后进化。"),
    "malicious-armor": ("咒术之铠", "对特定宝可梦使用后进化。"),
    "metal-alloy": ("合金", "对特定宝可梦使用后进化。"),
    "oval-stone": ("浑圆之石", "携带并在白天升级后进化。"),
    "razor-claw": ("锋锐之爪", "携带并在夜晚升级后进化。"),
    "razor-fang": ("锐利之牙", "携带并在夜晚升级后进化。"),
    "metal-coat": ("金属膜", "携带后交换进化,或对特定宝可梦起作用。"),
    "dragon-scale": ("龙之鳞片", "携带后交换进化。"),
    "deep-sea-scale": ("深海之鳞", "携带后交换进化。"),
    "deep-sea-tooth": ("深海之牙", "携带后交换进化。"),
    "kings-rock": ("王者之证", "携带后交换进化。"),
    "electirizer": ("电力增幅器", "携带后交换进化。"),
    "magmarizer": ("岩浆增幅器", "携带后交换进化。"),
    "protector": ("护具", "携带后交换进化。"),
    "reaper-cloth": ("灵界之布", "携带后交换进化。"),
    "sachet": ("香袋", "携带后交换进化。"),
    "whipped-dream": ("泡沫奶油", "携带后交换进化。"),
    "prism-scale": ("美丽鳞片", "携带后交换进化。"),
    "dubious-disc": ("可疑补丁", "携带后交换进化(多边兽２型专用)。"),
    "up-grade": ("升级数据", "携带后交换进化。"),
}
for _ek, (_ezh, _edesc) in _EVO_ITEMS.items():
    _ITEMS.setdefault(_ek, {"zh": _ezh, "desc": _edesc, "effect": {}})

# 俗称/旧译名:玩家常按习惯叫法找道具(`/持有 剩饭` 这类)
_ALIASES: dict[str, str] = {
    "剩饭": "leftovers",
    "专爱头巾": "choice-band",
    "专爱围巾": "choice-scarf",
    "专爱眼镜": "choice-specs",
    "讲究头巾": "choice-band",
    "讲究眼镜": "choice-specs",
    "气势头带": "focus-band",
    "气势腰带": "focus-sash",
    "弱点保险": "weakness-policy",
    "讲究护目镜": "safety-goggles",
    "生命宝珠": "life-orb",
    "突击背心": "assault-vest",
    "进化奇石": "eviolite",
    "黑色污泥": "blacksludge",
}

_ITEM_IDX: dict[str, str] = {}
for _key, _v in ITEMS.items():
    for _alias in (_key, _v["zh"]):
        _ITEM_IDX.setdefault(_norm(_alias), _key)
for _alias, _key in _ALIASES.items():
    if _key in ITEMS:
        _ITEM_IDX.setdefault(_norm(_alias), _key)


def resolve_item(query: str) -> tuple[str, dict] | None:
    """把道具名(中/英/标识)解析成 (key, entry)。"""
    if not query:
        return None
    raw = str(query).strip()
    if not raw:
        return None
    if raw in ITEMS:
        return raw, ITEMS[raw]
    key = _ITEM_IDX.get(_norm(raw))
    if key:
        # 有些道具(化石、树果)只登记在 BAG_ITEMS 里,不在持有道具表 ——
        # 以前这里直接取 ITEMS[key] 会 KeyError
        entry = ITEMS.get(key) or BAG_ITEMS.get(key)
        if entry:
            return key, entry
    # 背包表里的道具(如树果/消耗品)也可作为携带道具解析
    bag_key = raw if raw in BAG_ITEMS else _BAG_IDX.get(_norm(raw), "")
    if bag_key:
        return bag_key, BAG_ITEMS[bag_key]
    return None


def item_label(key: str | None) -> str:
    if not key:
        return ""
    entry = ITEMS.get(key)
    return entry["zh"] if entry else key


# ════════════════════════════════════════════════════════════════
# 背包道具(可消耗):精灵球 / 伤药 / 状态回复 / 复活 / PP 回复 /
# 战斗强化 / 进化石 / 稀有用具。effect 由 engine 与 tools 解释。
# ════════════════════════════════════════════════════════════════

# effect 支持的键:
#   ball_bonus: 固定捕获加成; ball_master: 必中;
#   ball_net / ball_nest / ball_timer / ball_quick / ball_dusk /
#   ball_repeat / ball_beast / ball_heavy / ball_level: 条件加成(engine 计算)
#   heal_hp / heal_hp_frac / heal_full: 回复 HP
#   cure_status: True(全部) 或 ["brn",...]; revive / revive_full: 复活
#   pp_restore / pp_restore_all: PP 回复
#   stat_boost: {stat: stages} 仅战斗中使用

_BAG: dict[str, dict] = {
    # ── 精灵球 ──
    "poke-ball": {"zh": "精灵球", "kind": "ball", "desc": "捕获宝可梦的基本球。", "effect": {"ball_bonus": 1.0}},
    "great-ball": {"zh": "超级球", "kind": "ball", "desc": "比精灵球更容易捕获。", "effect": {"ball_bonus": 1.5}},
    "ultra-ball": {"zh": "高级球", "kind": "ball", "desc": "捕获性能很高的球。", "effect": {"ball_bonus": 2.0}},
    "master-ball": {"zh": "大师球", "kind": "ball", "desc": "必定捕获任何宝可梦。", "effect": {"ball_master": True}},
    "premier-ball": {"zh": "纪念球", "kind": "ball", "desc": "纪念用的稀有球,性能同精灵球。", "effect": {"ball_bonus": 1.0}},
    "heal-ball": {"zh": "治愈球", "kind": "ball", "desc": "捕获后回复宝可梦的 HP 与异常状态。", "effect": {"ball_bonus": 1.0, "ball_heal": True}},
    "net-ball": {"zh": "捕网球", "kind": "ball", "desc": "对水/虫属性宝可梦效果极佳。", "effect": {"ball_bonus": 1.0, "ball_net": 3.5}},
    "dusk-ball": {"zh": "黑暗球", "kind": "ball", "desc": "在洞窟/夜晚更容易捕获。", "effect": {"ball_bonus": 1.0, "ball_dusk": 3.0}},
    "quick-ball": {"zh": "先机球", "kind": "ball", "desc": "战斗刚开始时极易捕获。", "effect": {"ball_bonus": 1.0, "ball_quick": 5.0}},
    "timer-ball": {"zh": "计时球", "kind": "ball", "desc": "回合越多越容易捕获。", "effect": {"ball_bonus": 1.0, "ball_timer": True}},
    "repeat-ball": {"zh": "重复球", "kind": "ball", "desc": "对已捕获过的种类更容易捕获。", "effect": {"ball_bonus": 1.0, "ball_repeat": 3.5}},
    "nest-ball": {"zh": "巢穴球", "kind": "ball", "desc": "对手等级越低越容易捕获。", "effect": {"ball_bonus": 1.0, "ball_nest": True}},
    "level-ball": {"zh": "等级球", "kind": "ball", "desc": "对手等级越低越容易捕获。", "effect": {"ball_bonus": 1.0, "ball_level": True}},
    "heavy-ball": {"zh": "沉重球", "kind": "ball", "desc": "对体重大的宝可梦更容易捕获。", "effect": {"ball_bonus": 1.0, "ball_heavy": True}},
    "beast-ball": {"zh": "究极球", "kind": "ball", "desc": "对究极异兽效果极佳。", "effect": {"ball_bonus": 1.0, "ball_beast": 5.0}},
    # ── 伤药 / 回复 ──
    "potion": {"zh": "伤药", "kind": "medicine", "desc": "回复 20 HP。", "effect": {"heal_hp": 20}},
    "super-potion": {"zh": "好伤药", "kind": "medicine", "desc": "回复 60 HP。", "effect": {"heal_hp": 60}},
    "hyper-potion": {"zh": "厉害伤药", "kind": "medicine", "desc": "回复 120 HP。", "effect": {"heal_hp": 120}},
    "max-potion": {"zh": "全满药", "kind": "medicine", "desc": "完全回复 HP。", "effect": {"heal_full": True}},
    "full-restore": {"zh": "全复药", "kind": "medicine", "desc": "完全回复 HP 并治愈异常状态。", "effect": {"heal_full": True, "cure_status": True}},
    "fresh-water": {"zh": "美味之水", "kind": "medicine", "desc": "回复 30 HP。", "effect": {"heal_hp": 30}},
    "soda-pop": {"zh": "汽水", "kind": "medicine", "desc": "回复 50 HP。", "effect": {"heal_hp": 50}},
    "lemonade": {"zh": "柠檬汁", "kind": "medicine", "desc": "回复 80 HP。", "effect": {"heal_hp": 80}},
    "moomoo-milk": {"zh": "哞哞鲜奶", "kind": "medicine", "desc": "回复 100 HP。", "effect": {"heal_hp": 100}},
    "berry-juice": {"zh": "树果汁", "kind": "medicine", "desc": "回复 20 HP。", "effect": {"heal_hp": 20}},
    "sweet-heart": {"zh": "甜甜蜜", "kind": "medicine", "desc": "回复 20 HP。", "effect": {"heal_hp": 20}},
    # ── 状态回复 ──
    "antidote": {"zh": "解毒药", "kind": "status", "desc": "治愈中毒。", "effect": {"cure_status": ["psn", "tox"]}},
    "burn-heal": {"zh": "灼伤药", "kind": "status", "desc": "治愈灼伤。", "effect": {"cure_status": ["brn"]}},
    "ice-heal": {"zh": "解冻药", "kind": "status", "desc": "治愈冰冻。", "effect": {"cure_status": ["frz"]}},
    "awakening": {"zh": "解眠药", "kind": "status", "desc": "唤醒睡眠。", "effect": {"cure_status": ["slp"]}},
    "paralyze-heal": {"zh": "解麻药", "kind": "status", "desc": "治愈麻痹。", "effect": {"cure_status": ["par"]}},
    "full-heal": {"zh": "万灵药", "kind": "status", "desc": "治愈任何异常状态。", "effect": {"cure_status": True}},
    # ── 复活 ──
    "revive": {"zh": "活力碎片", "kind": "revive", "desc": "复活并回复一半 HP。", "effect": {"revive": 0.5}},
    "max-revive": {"zh": "活力块", "kind": "revive", "desc": "复活并完全回复 HP。", "effect": {"revive_full": True}},
    # ── PP 回复 ──
    "ether": {"zh": "PP 单项小补剂", "kind": "pp", "desc": "回复一个招式 10 点 PP。", "effect": {"pp_restore": 10}},
    "max-ether": {"zh": "PP 单项全补剂", "kind": "pp", "desc": "完全回复一个招式的 PP。", "effect": {"pp_restore_all": 1}},
    "elixir": {"zh": "PP 多项小补剂", "kind": "pp", "desc": "回复全部招式各 10 点 PP。", "effect": {"pp_restore": 10, "pp_all": True}},
    "max-elixir": {"zh": "PP 多项全补剂", "kind": "pp", "desc": "完全回复全部招式的 PP。", "effect": {"pp_restore_all": 1, "pp_all": True}},
    # ── 战斗强化道具(仅战斗中使用) ──
    "x-attack": {"zh": "力量强化", "kind": "battle", "desc": "战斗中提升攻击。", "effect": {"stat_boost": {"atk": 1}}},
    "x-defense": {"zh": "防御强化", "kind": "battle", "desc": "战斗中提升防御。", "effect": {"stat_boost": {"def": 1}}},
    "x-special": {"zh": "特攻强化", "kind": "battle", "desc": "战斗中提升特攻。", "effect": {"stat_boost": {"spa": 1}}},
    "x-sp-defense": {"zh": "特防强化", "kind": "battle", "desc": "战斗中提升特防。", "effect": {"stat_boost": {"spd": 1}}},
    "x-speed": {"zh": "速度强化", "kind": "battle", "desc": "战斗中提升速度。", "effect": {"stat_boost": {"spe": 1}}},
    "dire-hit": {"zh": "要害强化", "kind": "battle", "desc": "战斗中提升会心一击率。", "effect": {"focus_energy": True}},
    "guard-spec": {"zh": "要害防御", "kind": "battle", "desc": "防止对手要害一击。", "effect": {"guard_spec": True}},
    # ── 树果(可携带或使用) ──
    "oran-berry": {"zh": "橙橙果", "kind": "berry", "desc": "回复 10 HP。", "effect": {"heal_hp": 10}},
    "cheri-berry": {"zh": "蔓莓果", "kind": "berry", "desc": "治愈麻痹。", "effect": {"cure_status": ["par"]}},
    "chesto-berry": {"zh": "迷雾果", "kind": "berry", "desc": "唤醒睡眠。", "effect": {"cure_status": ["slp"]}},
    "pecha-berry": {"zh": "桃桃果", "kind": "berry", "desc": "治愈中毒。", "effect": {"cure_status": ["psn", "tox"]}},
    "rawst-berry": {"zh": "苦味果", "kind": "berry", "desc": "治愈灼伤。", "effect": {"cure_status": ["brn"]}},
    "aspear-berry": {"zh": "亚开果", "kind": "berry", "desc": "治愈冰冻。", "effect": {"cure_status": ["frz"]}},
    "lum-berry": {"zh": "木子果", "kind": "berry", "desc": "治愈任何异常状态。", "effect": {"cure_status": True}},
    "sitrus-berry": {"zh": "文柚果", "kind": "berry", "desc": "回复最大 HP 的 1/4。", "effect": {"heal_hp_frac": 0.25}},
    "leppa-berry": {"zh": "苹野果", "kind": "berry", "desc": "回复一个招式 10 点 PP。", "effect": {"pp_restore": 10}},
    # ── 稀有用具 ──
    "rare-candy": {"zh": "神奇糖果", "kind": "rare", "desc": "提升 1 级。", "effect": {"level_up": 1}},
    "pp-up": {"zh": "PP 提升剂", "kind": "rare", "desc": "提升一个招式的 PP 上限。", "effect": {"pp_up": 1}},
    "pp-max": {"zh": "PP 极限提升剂", "kind": "rare", "desc": "将招式的 PP 上限提到最大。", "effect": {"pp_up": 999}},
    "ability-capsule": {"zh": "特性胶囊", "kind": "rare", "desc": "切换到另一个普通特性。", "effect": {"ability_switch": True}},
    "ability-patch": {"zh": "特性膏药", "kind": "rare", "desc": "切换到隐藏特性。", "effect": {"ability_patch": True}},
}

# 进化道具并入背包(其中的进化石稍后会被 _STONES 覆写为 kind=stone)
for _ek, (_ezh, _edesc) in _EVO_ITEMS.items():
    _BAG.setdefault(
        _ek,
        {"zh": _ezh, "kind": "evo", "desc": _edesc, "effect": {"evolve_item": _ek}},
    )

# 进化石
_STONES = {
    "fire-stone": ("火之石", "Fire"),
    "water-stone": ("水之石", "Water"),
    "thunder-stone": ("雷之石", "Electric"),
    "leaf-stone": ("叶之石", "Grass"),
    "moon-stone": ("月之石", "Moon"),
    "sun-stone": ("日之石", "Sun"),
    "shiny-stone": ("光之石", "Shiny"),
    "dusk-stone": ("暗之石", "Dusk"),
    "dawn-stone": ("觉醒之石", "Dawn"),
    "ice-stone": ("冰之石", "Ice"),
}
for _k, (_zh, _tag) in _STONES.items():
    _BAG[_k] = {
        "zh": _zh,
        "kind": "stone",
        "desc": "特定的宝可梦使用后会进化。",
        "effect": {"evolve_stone": _tag},
    }

# ══════════════════════════════════════════════════════════════════
# 招式机(TM)
# ══════════════════════════════════════════════════════════════════
# 兼容性不做第二套数据:直接复用图鉴学习表里的 **"M" 码**(某世代可用招式机学会),
# 所以"这台机器能教谁"与 `/图鉴` 显示的学习途径永远一致。
#
# 获取途径(经典设计):
#   ① 首次打倒每个道馆馆主 → 送**该馆属性**的招牌招式机;
#   ② 商店按徽章档位出售常用招式机;
#   ③ 委托奖励池里也会出现。
TM_GYM_BY_TYPE: dict[str, str] = {
    "Normal": "bodyslam",        # 泰山压顶
    "Fire": "flamethrower",      # 喷射火焰
    "Water": "surf",             # 冲浪
    "Electric": "thunderbolt",   # 十万伏特
    "Grass": "energyball",       # 能量球
    "Ice": "icebeam",            # 冰冻光束
    "Fighting": "brickbreak",    # 劈瓦
    "Poison": "sludgebomb",      # 污泥炸弹
    "Ground": "earthquake",      # 地震
    "Flying": "aerialace",       # 燕返
    "Psychic": "psychic",        # 精神强念
    "Bug": "xscissor",           # 十字剪
    "Rock": "rockslide",         # 岩崩
    "Ghost": "shadowball",       # 影子球
    "Dragon": "dragonclaw",      # 龙爪
    "Dark": "darkpulse",         # 恶之波动
    "Steel": "flashcannon",      # 加农光炮
    "Fairy": "dazzlinggleam",    # 魔法闪耀
}

# 商店档位:徽章越多解锁越强的招式机(与 SHOP_TIERS 同一套档位语义)
TM_SHOP: list[tuple[int, list[str]]] = [
    (2, ["protect", "substitute", "aerialace", "swift", "rocktomb", "thief",
         "bulldoze", "lowsweep"]),
    (4, ["icebeam", "thunderbolt", "flamethrower", "energyball", "shadowball",
         "brickbreak", "sludgebomb", "dragonclaw", "psychic", "uturn"]),
    (6, ["earthquake", "surf", "hyperbeam", "closecombat", "flashcannon",
         "dazzlinggleam", "darkpulse", "xscissor", "rockslide", "airslash",
         "stoneedge", "thunderwave"]),
]

TM_MOVES: list[str] = sorted(
    {*TM_GYM_BY_TYPE.values(), *(m for _t, ks in TM_SHOP for m in ks)}
)


def tm_key(move: str) -> str:
    """招式 key → 招式机道具 key。"""
    return f"tm-{move}"


def tm_move(key: str | None) -> str:
    """招式机道具 key → 招式 key(不是招式机则返回空串)。"""
    k = str(key or "")
    return k[3:] if k.startswith("tm-") else ""


def is_tm(key: str | None) -> bool:
    return str(key or "").startswith("tm-") and tm_move(key) in TM_MOVES


# 招式 key 必须都在招式表里 —— 写错一个字母就会"少一台机器",很难发现。
# 这里在导入时对一遍,缺的记在 TM_MISSING 里(测试会断言它是空的)。
TM_MISSING: list[str] = []


def _build_tm_items() -> dict[str, dict]:
    """把 TM_MOVES 展开成背包条目(名字/说明都带上招式本身的信息)。"""
    dex = get_dex()
    out: dict[str, dict] = {}
    for mv in TM_MOVES:
        m = dex.moves.get(mv) or {}
        if not m:
            TM_MISSING.append(mv)          # 数据里没有这个招式:别发机器
            continue
        zh = m.get("zh") or mv
        tlabel = dex.type_label(str(m.get("type") or ""))
        cat = dex.move_category_zh(str(m.get("category") or ""))
        power = int(m.get("basePower") or 0)
        tag = f"威力{power}" if power else "变化招式"
        out[tm_key(mv)] = {
            "zh": f"招式机·{zh}",
            "kind": "tm",
            "desc": f"教兼容的宝可梦学会「{zh}」({tlabel}系/{cat}·{tag})。机器用掉即消失。",
            "effect": {"teaches": mv},
        }
    return out


BAG_ITEMS: dict[str, dict] = _BAG
# 持有道具(ITEMS)也要并进 BAG_ITEMS:
# 背包/商店/委托奖励/事件发奖这些流程一律走 BAG_ITEMS,原来的实现只给 ITEMS
# 里的条目补了个 kind 镜像,**没进 BAG_ITEMS** —— 结果 47 件纯携带道具
# (剩饭/讲究头带/进化奇石/气势披带…)一件都拿不到,`/持有` 等于空的。
# kind 维持原样(进化石/进化道具/树果在 BAG 里已有条目,不受影响)。
for _ik, _ientry in list(ITEMS.items()):
    if _ik not in BAG_ITEMS:
        BAG_ITEMS[_ik] = {**_ientry, "kind": _ientry.get("kind") or "held"}
# 招式机(必须在持有道具之后合并,顺序反过来会被覆盖掉)
BAG_ITEMS.update(_build_tm_items())
_BAG_IDX: dict[str, str] = {}
for _key, _v in BAG_ITEMS.items():
    for _alias in (_key, _v["zh"]):
        _BAG_IDX.setdefault(_norm(_alias), _key)

KIND_ZH = {
    "tm": "招式机",
    "ball": "精灵球",
    "medicine": "回复",
    "status": "状态回复",
    "revive": "复活",
    "pp": "PP 回复",
    "battle": "战斗强化",
    "berry": "树果",
    "rare": "稀有用具",
    "stone": "进化石",
    "evo": "进化道具",
    "held": "持有道具",
}
KIND_ORDER = [
    "ball", "medicine", "status", "revive", "pp", "battle", "berry", "stone", "evo",
    "rare", "held",
]



def resolve_bag_item(query: str) -> tuple[str, dict] | None:
    """把背包道具名解析成 (key, entry)。"""
    if not query:
        return None
    raw = str(query).strip()
    if raw in BAG_ITEMS:
        return raw, BAG_ITEMS[raw]
    key = _BAG_IDX.get(_norm(raw))
    if key:
        return key, BAG_ITEMS[key]
    # 兼容持有道具表里的名字(背包里也可能存)
    r = resolve_item(raw)
    if r and r[0] in BAG_ITEMS:
        return r[0], BAG_ITEMS[r[0]]
    return None


# 效果字段 → 人话。写在背包/商店说明框里,让玩家知道这道具**具体做什么**
_STATUS_NAME_ZH = {
    "psn": "中毒", "tox": "剧毒", "brn": "灼伤", "par": "麻痹", "slp": "睡眠",
    "frz": "冰冻", "confusion": "混乱", "all": "所有异常状态",
}
_BALL_CONDITION_ZH = {
    "ball_heal": "对 HP 低的宝可梦更有效",
    "ball_net": "对水属性/虫属性更有效",
    "ball_dusk": "在夜晚或洞窟里更有效",
    "ball_quick": "对刚出现的宝可梦更有效",
    "ball_timer": "回合拖得越久越有效",
    "ball_repeat": "对已捕获过的种类更有效",
    "ball_nest": "对等级低的宝可梦更有效",
    "ball_level": "对手等级越高越有效",
    "ball_heavy": "对体重重的宝可梦更有效",
    "ball_beast": "对究极异兽特别有效",
}
_STAT_NAME_ZH = {
    "atk": "攻击", "def": "防御", "spa": "特攻", "spd": "特防", "spe": "速度",
    "accuracy": "命中", "evasion": "闪避",
}
_TYPE_ZH = {
    "Normal": "一般", "Fire": "火", "Water": "水", "Electric": "电", "Grass": "草",
    "Ice": "冰", "Fighting": "格斗", "Poison": "毒", "Ground": "地面",
    "Flying": "飞行", "Psychic": "超能力", "Bug": "虫", "Rock": "岩石",
    "Ghost": "幽灵", "Dragon": "龙", "Dark": "恶", "Steel": "钢", "Fairy": "妖精",
}
# 这些效果目前**没有消费方**(引擎里没人读) —— 说明里要如实标出来,
# 免得玩家买了/用了却什么都没发生
# 效果 → 人话(这三个以前是"未实现",现在 `/使用` 里真的能用了)
_IMPLEMENTED_SPECIAL = {
    "pp_up": "提升一个招式的 PP 上限(每招最多 +3)",
    "ability_switch": "切换成另一个普通特性",
    "ability_patch": "切换成隐藏特性",
}


# PP 提升剂对单个招式的最大加成(原版:最多 3 次,每次 +1/5 基数;这里简化为 +3 点)
PP_UP_MAX = 3


def max_pp(mon, move: str) -> int:
    """招式 PP 上限(基础值 + `/使用 PP提升剂` 的加成)。"""
    base = int((get_dex().moves.get(str(move)) or {}).get("pp", 10) or 10)
    bonus = int((getattr(mon, "pp_bonus", None) or {}).get(str(move), 0) or 0)
    return max(1, base + bonus)


def item_needed(mon, eff: dict) -> bool:
    """这只宝可梦"用得上"这件道具吗(战斗外判定的依据)。"""
    if eff.get("revive") or eff.get("revive_full"):
        return mon.cur_hp <= 0
    if mon.cur_hp <= 0:
        return False                       # 濒死的只能靠复活类
    if eff.get("heal_hp") or eff.get("heal_hp_frac") or eff.get("heal_full"):
        return mon.cur_hp < mon.max_hp
    cure = eff.get("cure_status")
    if cure:
        if not mon.status:
            return False
        if cure is True:
            return True
        if isinstance(cure, (list, tuple)):
            return str(mon.status) in {str(x) for x in cure}
        return str(mon.status) == str(cure)
    if eff.get("pp_restore") or eff.get("pp_restore_all"):
        return any(
            int(mon.pp.get(m, 0)) < max_pp(mon, m) for m in (mon.moves or [])
        )
    return False


def apply_out_of_battle(mon, key: str, *, move: str | None = None) -> tuple[bool, str]:
    """战斗外使用回复/状态/复活/PP 类道具,返回 `(是否生效, 给玩家看的一行)`。

    优先用能被消费掉的字段:`pp_up`/`ability_*`/`level_up` 这些在 main.py 里另走
    专用分支,这里只处理"回复类"。
    """
    entry = BAG_ITEMS.get(str(key or "")) or ITEMS.get(str(key or "")) or {}
    eff = entry.get("effect") if isinstance(entry.get("effect"), dict) else {}
    if not eff or not item_needed(mon, eff):
        return False, ""
    name = mon.display
    if eff.get("revive") or eff.get("revive_full"):
        frac = 1.0 if eff.get("revive_full") else float(eff.get("revive") or 0.5)
        mon.cur_hp = max(1, int(mon.max_hp * frac))
        mon.status = ""
        return True, f"💗 {name} 复活了(HP {mon.cur_hp}/{mon.max_hp})!"
    if eff.get("heal_full"):
        mon.cur_hp = mon.max_hp
        return True, f"💚 {name} 的 HP 完全回复了!"
    if eff.get("heal_hp") or eff.get("heal_hp_frac"):
        before = mon.cur_hp
        heal = int(eff.get("heal_hp") or 0)
        if eff.get("heal_hp_frac"):
            heal += int(mon.max_hp * float(eff["heal_hp_frac"]))
        mon.cur_hp = min(mon.max_hp, mon.cur_hp + max(1, heal))
        return True, f"💚 {name} 回复了 {mon.cur_hp - before} HP(HP {mon.cur_hp}/{mon.max_hp})。"
    cure = eff.get("cure_status")
    if cure:
        mon.status = ""
        return True, f"💊 {name} 的异常状态治愈了。"
    if eff.get("pp_restore") or eff.get("pp_restore_all"):
        full = bool(eff.get("pp_restore_all"))
        all_moves = bool(eff.get("pp_all")) or full
        amount = 0 if full else int(eff.get("pp_restore") or 0)
        if move and move in (mon.moves or []) and not all_moves:
            targets = [move]
        else:
            targets = list(mon.moves or [])
        fixed = 0
        for mv in targets:
            mx = max_pp(mon, mv)
            cur = int(mon.pp.get(mv, mx))
            if cur >= mx:
                continue
            mon.pp[mv] = mx if full else min(mx, cur + amount)
            fixed += 1
        if not fixed:
            return False, ""
        return True, f"⚙️ {name} 回复了招式的 PP({fixed} 个招式)。"
    return False, ""


# ── 化石道具:在「研究所」(宝可梦中心)用 `/复活 <化石>` 换回古代宝可梦 ──
_FOSSILS = {
    "helix-fossil": ("菊石兽的化石", "omanyte", "菊石兽"),
    "dome-fossil": ("甲壳化石", "kabuto", "化石盔"),
    "old-amber": ("琥珀", "aerodactyl", "化石翼龙"),
    "root-fossil": ("根之化石", "lileep", "触手百合"),
    "claw-fossil": ("爪之化石", "anorith", "太古羽虫"),
    "skull-fossil": ("头盖化石", "cranidos", "头盖龙"),
    "armor-fossil": ("盾甲化石", "shieldon", "盾甲龙"),
    "cover-fossil": ("甲壳化石", "tirtouga", "原盖海龟"),
    "plume-fossil": ("羽毛化石", "archen", "始祖小鸟"),
    "jaw-fossil": ("颚之化石", "tyrunt", "宝宝暴龙"),
    "sail-fossil": ("鳍之化石", "amaura", "冰雪龙"),
}
for _k, (_zh, _sp, _sp_zh) in _FOSSILS.items():
    BAG_ITEMS.setdefault(_k, {
        "zh": _zh,
        "kind": "rare",
        "desc": f"古代宝可梦的化石,可以复活成{_sp_zh}。",
        "effect": {"revive_species": _sp},
    })
    for _alias in (_zh, f"{_sp_zh}的化石", _sp_zh):
        _ITEM_IDX.setdefault(_norm(_alias), _k)


# 伽勒尔那 4 只**要两件化石拼**(原作里就是"拼错骨头"的设定,所以长得四不像)
_FOSSIL_PARTS = {
    "fossilized-bird": ("化石鸟", "Bird"),
    "fossilized-fish": ("化石鱼", "Fish"),
    "fossilized-drake": ("化石龙", "Drake"),
    "fossilized-dino": ("化石兽", "Dino"),
}
for _k, (_zh, _en) in _FOSSIL_PARTS.items():
    BAG_ITEMS.setdefault(_k, {
        "zh": _zh,
        "kind": "rare",
        "desc": f"伽勒尔地区发现的不完整化石({_en}) —— 要**两件拼在一起**才能复活。",
        "effect": {"fossil_part": _en},
    })
    for _alias in (_zh, f"{_zh}化石"):
        _ITEM_IDX.setdefault(_norm(_alias), _k)

# 两件化石 → 复活的宝可梦(frozenset 做键,顺序无关)
FOSSIL_COMBOS: dict[frozenset[str], str] = {
    frozenset({"fossilized-bird", "fossilized-drake"}): "dracozolt",    # 雷鸟龙
    frozenset({"fossilized-bird", "fossilized-dino"}): "arctozolt",     # 雷鸟海兽
    frozenset({"fossilized-fish", "fossilized-drake"}): "dracovish",    # 鳃鱼龙
    frozenset({"fossilized-fish", "fossilized-dino"}): "arctovish",     # 鳃鱼海兽
}


def fossil_species(key: str) -> str:
    """化石道具 → 能复活的宝可梦 key(单件化石;拼合化石不在此列)。"""
    eff = (BAG_ITEMS.get(str(key or "")) or {}).get("effect") or {}
    return str(eff.get("revive_species") or "")


def fossil_part(key: str) -> str:
    """拼合化石的部件名(Bird/Fish/Drake/Dino);不是拼合化石就返回空串。"""
    eff = (BAG_ITEMS.get(str(key or "")) or {}).get("effect") or {}
    return str(eff.get("fossil_part") or "")


def fossil_combo(keys) -> str:
    """两件拼合化石 → 宝可梦 key(顺序无关;拼不出来返回空串)。"""
    parts = {str(k) for k in (keys or []) if str(k)}
    for combo, sp in FOSSIL_COMBOS.items():
        if combo == parts:
            return sp
    return ""


def fossil_revivable() -> set[str]:
    """所有能靠化石复活的宝可梦(11 只单件 + 4 只拼合)。"""
    out = {fossil_species(k) for k in BAG_ITEMS if fossil_species(k)}
    out.update(FOSSIL_COMBOS.values())
    return {x for x in out if x}


def effect_text(key: str | None) -> str:
    """把道具的 `effect` 结构化字段翻成一句中文效果(没有就返回空串)。"""
    entry = BAG_ITEMS.get(str(key or "")) or ITEMS.get(str(key or "")) or {}
    eff = entry.get("effect")
    if not isinstance(eff, dict) or not eff:
        return ""
    bits: list[str] = []
    for field, val in eff.items():
        if field in _IMPLEMENTED_SPECIAL:
            bits.append(_IMPLEMENTED_SPECIAL[field])
        elif field == "heal_hp":
            bits.append(f"回复 {int(val)} HP")
        elif field == "heal_hp_frac":
            bits.append(f"回复最大 HP 的 {float(val) * 100:.0f}%")
        elif field == "heal_full":
            bits.append("完全回复 HP")
        elif field == "cure_status":
            if val is True:
                bits.append("治愈所有异常状态")
            else:
                names = "、".join(
                    _STATUS_NAME_ZH.get(str(x), str(x))
                    for x in (val if isinstance(val, (list, tuple)) else [val])
                )
                bits.append(f"治愈{names}")
        elif field == "revive":
            bits.append(f"复活并回复 {float(val) * 100:.0f}% HP")
        elif field == "revive_full":
            bits.append("完全复活并回满 HP")
        elif field == "pp_restore":
            where = "全部招式各" if eff.get("pp_all") else "一个招式"
            bits.append(f"{where}回复 {int(val)} 点 PP")
        elif field == "pp_restore_all":
            where = "全部招式" if eff.get("pp_all") else "一个招式"
            bits.append(f"完全回复{where}的 PP")
        elif field == "pp_all":
            if "pp_restore" not in eff and "pp_restore_all" not in eff:
                bits.append("回复所有招式的 PP")
        elif field == "ball_master":
            bits.append("必定捕获")
        elif field == "ball_bonus":
            # ×1 是基础捕获率,不是加成 —— 别当效果写出来
            if float(val) > 1.0:
                bits.append(f"捕获率 ×{float(val):g}")
        elif field in _BALL_CONDITION_ZH:
            bits.append(_BALL_CONDITION_ZH[field])
        elif field == "evolve_stone":
            zh = _TYPE_ZH.get(str(val), str(val))
            bits.append(f"让对应的宝可梦进化({zh}属性系)")
        elif field == "evolve_item":
            bits.append("让对应的宝可梦进化")
        elif field == "stat_boost":
            if isinstance(val, dict):
                ups = "、".join(
                    f"{_STAT_NAME_ZH.get(str(k), str(k))} {'+' if int(v) > 0 else ''}{int(v)}"
                    for k, v in val.items()
                )
                bits.append(f"战斗中提升 {ups}")
        elif field == "focus_energy":
            bits.append("战斗中提升会心率")
        elif field == "guard_spec":
            bits.append("战斗中提升特防")
        elif field == "teaches":
            zh = (get_dex().moves.get(str(val)) or {}).get("zh") or val
            bits.append(f"教会「{zh}」")
        elif field == "fossil_part":
            bits.append(f"拼合化石部件({val})—— 需要两件一起复活")
        elif field == "revive_species":
            bits.append(f"可复活成{(get_dex().species.get(str(val)) or {}).get('zh', val)}")
        elif field == "level_up":
            bits.append(f"提升 {int(val)} 级" if isinstance(val, (int, float)) and val
                        else "提升等级")
        # ── 持有道具的生效字段(引擎里真的有消费方)──
        elif field == "type_mult" and isinstance(val, dict):
            ups = "、".join(
                f"{_TYPE_ZH.get(str(k), str(k))} ×{float(v):g}" for k, v in val.items()
            )
            bits.append(f"强化 {ups} 属性招式")
        elif field == "stat_mult" and isinstance(val, dict):
            ups = "、".join(
                f"{_STAT_NAME_ZH.get(str(k), str(k))} ×{float(v):g}" for k, v in val.items()
            )
            bits.append(f"能力 {ups}")
        elif field == "category_mult" and isinstance(val, dict):
            ups = "、".join(
                f"{'物理' if str(k) == 'Physical' else '特殊'} ×{float(v):g}"
                for k, v in val.items()
            )
            bits.append(f"{ups} 招式")
        elif field == "damage_mult":
            bits.append(f"招式威力 ×{float(val):g}")
        elif field == "end_turn_heal":
            bits.append(f"每回合回复最大 HP 的 {float(val) * 100:g}%")
        elif field == "end_turn_heal_poison":
            bits.append("毒属性宝可梦每回合回复 HP")
        elif field == "end_turn_damage_nonpoison":
            bits.append("非毒属性宝可梦每回合受伤")
        elif field == "weather":
            wzh = {"sun": "大晴天", "rain": "下雨", "sand": "沙暴", "snow": "下雪"}
            bits.append(f"出场时布下{wzh.get(str(val), val)}")
        elif field == "weather_turns":
            bits.append(f"天气持续 {int(val)} 回合")
        elif field == "choice_lock":
            bits.append("锁定一个招式,但威力提高")
        elif field == "on_low_hp":
            bits.append("HP 低时威力提高")
        elif field == "focus_sash":
            bits.append("满 HP 时受到致命伤害会留下 1 HP")
        elif field == "recoil":
            bits.append("攻击后承受反作用伤害")
        elif field == "contact_recoil":
            bits.append("被接触类招式打中时反伤对手")
        elif field == "no_status_moves":
            bits.append("不能使用变化招式")
        elif field == "requires_nfe":
            # 与 stat_mult 同时存在时(进化奇石)上面已经说明了,别再重复一遍
            if "stat_mult" not in eff:
                bits.append("未进化完全时防御与特防提高")
        elif field == "no_hazards":
            bits.append("不受入场陷阱影响")
        elif field == "screen_turns":
            bits.append(f"壁类招式持续 {int(val)} 回合")
        elif field == "self_status":
            bits.append(f"出场时获得{_STATUS_NAME_ZH.get(str(val), str(val))}状态")
        else:
            # 别把英文字段名丢给玩家看
            bits.append("特殊效果(见下方说明)")
        bits = list(bits)
    # 球类的 ×1 是噪音(基础捕获率,不是加成)
    if len(bits) > 1:
        bits = [b for b in bits if b != "捕获率 ×1"]
    return "、".join(bits)


def bag_item_label(key: str | None) -> str:
    if not key:
        return ""
    entry = BAG_ITEMS.get(key)
    return entry["zh"] if entry else key
