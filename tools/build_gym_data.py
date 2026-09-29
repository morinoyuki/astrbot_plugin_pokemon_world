#!/usr/bin/env python
"""Build `pw/static/gyms.json` (hand-curated real game data).

Gym leaders, Elite Four and Champions are hardcoded from the actual games
(FRLG / HGSS / ORAS / Platinum / B2W2 / XY / SwSh and Alola trials+kahunas).
Locations are resolved against `maps.json` so every emitted `location` is a real
travel node; species are validated against `species.json`.

Run:  python tools/build_gym_data.py   (after build_map_graph.py)
"""

from __future__ import annotations

import itertools
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAPS = ROOT / "pw" / "static" / "maps.json"
SPECIES = ROOT / "pw" / "static" / "species.json"
META = ROOT / "pw" / "static" / "meta.json"
OUT = ROOT / "pw" / "static" / "gyms.json"

# ── region metadata ──────────────────────────────────────────────────────────
VERSIONS = {
    "kanto": "FRLG",
    "johto": "HGSS",
    "hoenn": "ORAS",
    "sinnoh": "Platinum",
    "unova": "B2W2",
    "kalos": "XY",
    "galar": "SwSh",
    "alola": "USUM",
    "paldea": "SV",
}
INCLUDED = ["kanto", "johto", "hoenn", "sinnoh", "unova", "kalos", "galar", "alola", "paldea"]


_MEGA_STONE_CACHE: dict[str, str] | None = None


def _mega_stones() -> dict[str, str]:
    """原种 → 对应 Mega 石 key(冠军/四天王的招牌位带石头用)。

    只挑一份:同一原种有多块石头(X/Y/Z)时优先没有后缀的那块。
    """
    global _MEGA_STONE_CACHE
    if _MEGA_STONE_CACHE is not None:
        return _MEGA_STONE_CACHE
    data = json.loads(SPECIES.read_text(encoding="utf-8"))
    grouped: dict[str, list[str]] = {}
    for entry in data.values():
        if "mega" not in str(entry.get("forme") or "").lower():
            continue
        base = str(entry.get("baseSpecies") or "")
        item = str(entry.get("requiredItem") or "")
        if not base or not item:
            continue
        key = re.sub(r"[^0-9a-z]+", "-", item.lower()).strip("-")
        grouped.setdefault(base, []).append(key)
    picked: dict[str, str] = {}
    for base, keys in grouped.items():
        plain = sorted(k for k in keys if not re.search(r"-[xyz]$", k))
        picked[base] = (plain or sorted(keys))[0]
    _MEGA_STONE_CACHE = picked
    return picked


# 馆主配 Mega 的最低招牌等级:太早的馆主(≤25 级)不给 —— 玩家那时
# 多半还没拿到钥石(3 徽章解锁),被 Mega 馆主打爆会很挫败。
# 30 级起的馆主大致对应各地第三至第八关(含原作可尔妮的 Mega 路卡利欧)。
GYM_MEGA_MIN_LV = 30


def attach_ace_mega(team: list[dict], *, min_level: int = 0) -> None:
    """给「招牌位」(队伍最后一只)带上 Mega 石 —— 馆主/四天王/冠军会用 Mega。"""
    if not team:
        return
    ace = team[-1]
    if int(ace.get("level") or 0) < int(min_level):
        return
    stone = _mega_stones().get(str(ace.get("species") or ""))
    if stone:
        ace["item"] = stone


def g(location, leader, leader_en, title, type_, badge, badge_en, team):
    entry = {
        "location": location,
        "leader": leader,
        "leader_en": leader_en,
        "title": title,
        "type": type_,
        "badge": badge,
        "badge_en": badge_en,
        "team": [{"species": s, "level": lv} for s, lv in team],
    }
    attach_ace_mega(entry["team"], min_level=GYM_MEGA_MIN_LV)
    return entry


def e(name, name_en, type_, team, title="四天王"):
    return {
        "name": name,
        "name_en": name_en,
        "title": title,
        "type": type_,
        "team": [{"species": s, "level": lv} for s, lv in team],
    }


DATA = {
    "kanto": {
        "gyms": [
            g("pewter-city", "小刚", "Brock", "深灰道馆", "Rock", "灰色徽章",
              "Boulder Badge", [("geodude", 12), ("onix", 14)]),
            g("cerulean-city", "小霞", "Misty", "华蓝道馆", "Water", "蓝色徽章",
              "Cascade Badge", [("staryu", 18), ("starmie", 21)]),
            g("vermilion-city", "马志士", "Lt. Surge", "枯叶道馆", "Electric",
              "橙色徽章", "Thunder Badge",
              [("voltorb", 21), ("pikachu", 18), ("raichu", 24)]),
            g("celadon-city", "莉佳", "Erika", "彩虹道馆", "Grass", "彩虹徽章",
              "Rainbow Badge",
              [("tangela", 24), ("victreebel", 29), ("vileplume", 29)]),
            g("fuchsia-city", "阿桔", "Koga", "浅红道馆", "Poison", "粉红徽章",
              "Soul Badge",
              [("koffing", 37), ("muk", 39), ("koffing", 37), ("weezing", 43)]),
            g("saffron-city", "娜姿", "Sabrina", "金黄道馆", "Psychic", "金色徽章",
              "Marsh Badge",
              [("kadabra", 38), ("mr-mime", 37), ("venomoth", 38),
               ("alakazam", 43)]),
            g("cinnabar-island", "夏伯", "Blaine", "红莲道馆", "Fire", "深红徽章",
              "Volcano Badge",
              [("growlithe", 42), ("ponyta", 40), ("rapidash", 42),
               ("arcanine", 47)]),
            g("viridian-city", "坂木", "Giovanni", "常磐道馆", "Ground", "绿色徽章",
              "Earth Badge",
              [("rhyhorn", 45), ("dugtrio", 42), ("nidoqueen", 44),
               ("nidoking", 45), ("rhydon", 50)]),
        ],
        "elite4": [
            e("科拿", "Lorelei", "Ice",
              [("dewgong", 52), ("cloyster", 51), ("slowbro", 52), ("jynx", 54),
               ("lapras", 54)]),
            e("希巴", "Bruno", "Fighting",
              [("onix", 51), ("hitmonchan", 53), ("hitmonlee", 53),
               ("machamp", 56)]),
            e("菊子", "Agatha", "Ghost",
              [("gengar", 54), ("golbat", 54), ("haunter", 53), ("arbok", 56),
               ("gengar", 58)]),
            e("阿渡", "Lance", "Dragon",
              [("gyarados", 56), ("dragonair", 54), ("dragonair", 54),
               ("aerodactyl", 58), ("dragonite", 60)]),
        ],
        "champion": {
            "name": "青绿", "name_en": "Blue", "title": "冠军",
            "team": [{"species": s, "level": lv} for s, lv in [
                ("pidgeot", 59), ("alakazam", 57), ("rhydon", 59),
                ("exeggutor", 59), ("arcanine", 61), ("blastoise", 63)]],
        },
    },
    "johto": {
        "gyms": [
            g("violet-city", "阿速", "Falkner", "桔梗道馆", "Flying", "飞翼徽章",
              "Zephyr Badge", [("pidgey", 9), ("pidgeotto", 13)]),
            g("azalea-town", "阿笔", "Bugsy", "桧皮道馆", "Bug", "昆虫徽章",
              "Hive Badge",
              [("metapod", 15), ("kakuna", 15), ("scyther", 17)]),
            g("goldenrod-city", "小茜", "Whitney", "满金道馆", "Normal", "一般徽章",
              "Plain Badge", [("clefairy", 17), ("miltank", 19)]),
            g("ecruteak-city", "松叶", "Morty", "圆朱道馆", "Ghost", "魅影徽章",
              "Fog Badge",
              [("gastly", 21), ("haunter", 21), ("haunter", 23),
               ("gengar", 25)]),
            g("cianwood-city", "阿四", "Chuck", "湛蓝道馆", "Fighting", "打击徽章",
              "Storm Badge", [("primeape", 29), ("poliwrath", 31)]),
            g("olivine-city", "阿蜜", "Jasmine", "浅葱道馆", "Steel", "钢铁徽章",
              "Mineral Badge",
              [("magnemite", 30), ("magnemite", 30), ("steelix", 35)]),
            g("mahogany-town", "柳伯", "Pryce", "卡吉道馆", "Ice", "冰河徽章",
              "Glacier Badge",
              [("seel", 30), ("dewgong", 32), ("piloswine", 36)]),
            g("blackthorn-city", "小椿", "Clair", "烟墨道馆", "Dragon", "升龙徽章",
              "Rising Badge",
              [("gyarados", 38), ("dragonair", 38), ("dragonair", 38),
               ("kingdra", 41)]),
        ],
        "elite4": [
            e("一树", "Will", "Psychic",
              [("xatu", 40), ("jynx", 41), ("exeggutor", 41), ("slowbro", 41),
               ("xatu", 42)]),
            e("阿桔", "Koga", "Poison",
              [("ariados", 40), ("venomoth", 41), ("forretress", 43),
               ("muk", 42), ("crobat", 44)]),
            e("希巴", "Bruno", "Fighting",
              [("hitmontop", 42), ("hitmonlee", 42), ("hitmonchan", 42),
               ("onix", 43), ("machamp", 46)]),
            e("卡琳", "Karen", "Dark",
              [("umbreon", 42), ("vileplume", 42), ("gengar", 45),
               ("murkrow", 44), ("houndoom", 47)]),
        ],
        "champion": {
            "name": "阿渡", "name_en": "Lance", "title": "冠军",
            "team": [{"species": s, "level": lv} for s, lv in [
                ("gyarados", 46), ("dragonite", 49), ("dragonite", 49),
                ("aerodactyl", 48), ("charizard", 50), ("dragonite", 50)]],
        },
    },
    "hoenn": {
        "gyms": [
            g("rustboro-city", "杜娟", "Roxanne", "卡那兹道馆", "Rock", "岩石徽章",
              "Stone Badge", [("geodude", 14), ("nosepass", 15)]),
            g("dewford-town", "藤树", "Brawly", "武斗道馆", "Fighting", "拳击徽章",
              "Knuckle Badge", [("machop", 16), ("makuhita", 19)]),
            g("mauville-city", "铁旋", "Wattson", "紫堇道馆", "Electric",
              "电力徽章", "Dynamo Badge",
              [("magnemite", 21), ("voltorb", 21), ("magneton", 23),
               ("manectric", 24)]),
            g("lavaridge-town", "亚莎", "Flannery", "釜炎道馆", "Fire", "烈焰徽章",
              "Heat Badge",
              [("numel", 26), ("slugma", 26), ("camerupt", 28), ("torkoal", 30)]),
            g("petalburg-city", "千里", "Norman", "橙华道馆", "Normal", "平衡徽章",
              "Balance Badge",
              [("slaking", 28), ("vigoroth", 28), ("slaking", 30)]),
            g("fortree-city", "娜琪", "Winona", "茵郁道馆", "Flying", "羽毛徽章",
              "Feather Badge",
              [("swellow", 33), ("pelipper", 33), ("altaria", 35),
               ("skarmory", 36)]),
            g("mossdeep-city", "枫与南", "Tate & Liza", "绿岭道馆", "Psychic",
              "心灵徽章", "Mind Badge",
              [("claydol", 45), ("xatu", 45), ("solrock", 47), ("lunatone", 47)]),
            g("sootopolis-city", "亚当", "Juan", "琉璃道馆", "Water", "雨滴徽章",
              "Rain Badge",
              [("luvdisc", 44), ("whiscash", 44), ("sealeo", 44),
               ("crawdaunt", 46), ("kingdra", 48)]),
        ],
        "elite4": [
            e("花月", "Sidney", "Dark",
              [("mightyena", 50), ("shiftry", 50), ("cacturne", 50),
               ("sharpedo", 50), ("absol", 52)]),
            e("芙蓉", "Phoebe", "Ghost",
              [("dusclops", 51), ("banette", 51), ("sableye", 51),
               ("banette", 51), ("dusknoir", 53)]),
            e("波妮", "Glacia", "Ice",
              [("glalie", 52), ("froslass", 52), ("glalie", 52),
               ("walrein", 54)]),
            e("源治", "Drake", "Dragon",
              [("altaria", 53), ("flygon", 53), ("kingdra", 53),
               ("flygon", 53), ("salamence", 55)]),
        ],
        "champion": {
            "name": "大吾", "name_en": "Steven", "title": "冠军",
            "team": [{"species": s, "level": lv} for s, lv in [
                ("skarmory", 57), ("claydol", 57), ("aggron", 57),
                ("cradily", 57), ("armaldo", 57), ("metagross", 59)]],
        },
    },
    "sinnoh": {
        "gyms": [
            g("oreburgh-city", "瓢太", "Roark", "黑金道馆", "Rock", "石炭徽章",
              "Coal Badge",
              [("geodude", 12), ("onix", 12), ("cranidos", 14)]),
            g("eterna-city", "菜种", "Gardenia", "百代道馆", "Grass", "森林徽章",
              "Forest Badge",
              [("cherubi", 18), ("turtwig", 18), ("roserade", 22)]),
            g("veilstone-city", "阿李", "Maylene", "帷幕道馆", "Fighting",
              "圆石徽章", "Cobble Badge",
              [("meditite", 28), ("machoke", 29), ("lucario", 32)]),
            g("pastoria-city", "吉宪", "Crasher Wake", "湿原道馆", "Water",
              "沼泽徽章", "Fen Badge",
              [("gyarados", 33), ("quagsire", 34), ("floatzel", 37)]),
            g("hearthome-city", "梅丽莎", "Fantina", "缘之道馆", "Ghost",
              "遗迹徽章", "Relic Badge",
              [("duskull", 32), ("haunter", 34), ("mismagius", 38)]),
            g("canalave-city", "东钢", "Byron", "水脉道馆", "Steel", "矿山洞徽章",
              "Mine Badge",
              [("magnemite", 37), ("steelix", 38), ("bastiodon", 41)]),
            g("snowpoint-city", "小菘", "Candice", "切锋道馆", "Ice", "冰河徽章",
              "Icicle Badge",
              [("snover", 38), ("sneasel", 40), ("medicham", 40),
               ("abomasnow", 42)]),
            g("sunyshore-city", "电次", "Volkner", "滨海道馆", "Electric",
              "灯塔徽章", "Beacon Badge",
              [("raichu", 46), ("jolteon", 46), ("luxray", 48),
               ("electivire", 50)]),
        ],
        "elite4": [
            e("阿柳", "Aaron", "Bug",
              [("yanmega", 49), ("scizor", 49), ("vespiquen", 50),
               ("heracross", 51), ("drapion", 53)]),
            e("菊野", "Bertha", "Ground",
              [("whiscash", 50), ("gliscor", 50), ("golem", 53),
               ("hippowdon", 53), ("rhyperior", 53)]),
            e("大叶", "Flint", "Fire",
              [("houndoom", 50), ("flareon", 50), ("rapidash", 52),
               ("magmortar", 52), ("infernape", 54)]),
            e("悟松", "Lucian", "Psychic",
              [("mr-mime", 51), ("espeon", 51), ("alakazam", 53),
               ("bronzong", 54), ("gallade", 55)]),
        ],
        "champion": {
            "name": "竹兰", "name_en": "Cynthia", "title": "冠军",
            "team": [{"species": s, "level": lv} for s, lv in [
                ("spiritomb", 56), ("roserade", 56), ("togekiss", 56),
                ("lucario", 56), ("milotic", 56), ("garchomp", 58)]],
        },
    },
    "unova": {
        "gyms": [
            g("aspertia-city", "黑连", "Cheren", "桧扇道馆", "Normal", "基础徽章",
              "Basic Badge", [("patrat", 11), ("lillipup", 13)]),
            g("virbank-city", "霍米加", "Roxie", "立涌道馆", "Poison", "毒性徽章",
              "Toxic Badge", [("koffing", 16), ("whirlipede", 18)]),
            g("castelia-city", "亚堤", "Burgh", "飞云道馆", "Bug", "甲虫徽章",
              "Insect Badge",
              [("swadloon", 22), ("dwebble", 22), ("leavanny", 24)]),
            g("nimbasa-city", "小菊儿", "Elesa", "雷文道馆", "Electric",
              "伏特徽章", "Bolt Badge",
              [("emolga", 28), ("flaaffy", 28), ("zebstrika", 30)]),
            g("driftveil-city", "菊老大", "Clay", "帆巴道馆", "Ground", "震动徽章",
              "Quake Badge",
              [("sandslash", 33), ("krokorok", 33), ("excadrill", 35)]),
            g("mistralton-city", "风露", "Skyla", "吹寄道馆", "Flying", "喷射徽章",
              "Jet Badge",
              [("swoobat", 37), ("skarmory", 37), ("sigilyph", 38),
               ("swanna", 40)]),
            g("opelucid-city", "夏卡", "Drayden", "双龙道馆", "Dragon", "传说徽章",
              "Legend Badge",
              [("druddigon", 46), ("flygon", 46), ("haxorus", 48)]),
            g("humilau-city", "西子伊", "Marlon", "青海波道馆", "Water", "波浪徽章",
              "Wave Badge",
              [("carracosta", 49), ("wailord", 49), ("jellicent", 51)]),
        ],
        "elite4": [
            e("婉龙", "Shauntal", "Ghost",
              [("cofagrigus", 56), ("drifblim", 56), ("golurk", 56),
               ("chandelure", 58)]),
            e("越橘", "Grimsley", "Dark",
              [("scrafty", 56), ("krookodile", 56), ("liepard", 56),
               ("bisharp", 58)]),
            e("嘉德丽雅", "Caitlin", "Psychic",
              [("musharna", 56), ("sigilyph", 56), ("reuniclus", 56),
               ("gothitelle", 58)]),
            e("连武", "Marshal", "Fighting",
              [("throh", 56), ("sawk", 56), ("mienshao", 56),
               ("conkeldurr", 58)]),
        ],
        "champion": {
            "name": "艾莉丝", "name_en": "Iris", "title": "冠军",
            "team": [{"species": s, "level": lv} for s, lv in [
                ("hydreigon", 60), ("druddigon", 59), ("aggron", 59),
                ("archeops", 59), ("lapras", 59), ("haxorus", 62)]],
        },
    },
    "kalos": {
        "gyms": [
            g("santalune-city", "紫罗兰", "Viola", "白檀道馆", "Bug", "虫虫徽章",
              "Bug Badge", [("surskit", 10), ("vivillon", 12)]),
            g("cyllage-city", "查克洛", "Grant", "遥香道馆", "Rock", "岩壁徽章",
              "Cliff Badge", [("amaura", 25), ("tyrunt", 25)]),
            g("shalour-city", "可尔妮", "Korrina", "娑罗道馆", "Fighting",
              "战斗徽章", "Rumble Badge",
              [("mienfoo", 29), ("machoke", 28), ("hawlucha", 32)]),
            g("coumarine-city", "福爷", "Ramos", "香薰道馆", "Grass", "植物徽章",
              "Plant Badge",
              [("jumpluff", 30), ("weepinbell", 31), ("gogoat", 34)]),
            g("lumiose-city", "希特隆", "Clemont", "密阿雷道馆", "Electric",
              "电压徽章", "Voltage Badge",
              [("emolga", 35), ("magneton", 35), ("heliolisk", 37)]),
            g("laverre-city", "玛绣", "Valerie", "香薰道馆", "Fairy", "妖精徽章",
              "Fairy Badge",
              [("mawile", 38), ("mr-mime", 39), ("sylveon", 42)]),
            g("anistar-city", "葛吉花", "Olympia", "映雪道馆", "Psychic",
              "超能徽章", "Psychic Badge",
              [("sigilyph", 44), ("slowking", 45), ("meowstic", 48)]),
            g("snowbelle-city", "得抚", "Wulfric", "映雪道馆", "Ice", "冰山徽章",
              "Iceberg Badge",
              [("cryogonal", 55), ("abomasnow", 56), ("avalugg", 59)]),
        ],
        "elite4": [
            e("帕琦拉", "Malva", "Fire",
              [("pyroar", 63), ("torkoal", 63), ("chandelure", 63),
               ("talonflame", 65)]),
            e("志米", "Siebold", "Water",
              [("clawitzer", 63), ("starmie", 63), ("gyarados", 63),
               ("barbaracle", 65)]),
            e("雁铠", "Wikstrom", "Steel",
              [("klefki", 63), ("probopass", 63), ("scizor", 63),
               ("aegislash", 65)]),
            e("朵拉塞娜", "Drasna", "Dragon",
              [("dragalge", 63), ("altaria", 63), ("druddigon", 63),
               ("noivern", 65)]),
        ],
        "champion": {
            "name": "卡露妮", "name_en": "Diantha", "title": "冠军",
            "team": [{"species": s, "level": lv} for s, lv in [
                ("hawlucha", 64), ("tyrantrum", 65), ("aurorus", 65),
                ("gourgeist", 65), ("goodra", 66), ("gardevoir", 68)]],
        },
    },
    "galar": {
        "gyms": [
            g("turffield", "亚洛", "Milo", "草路道馆", "Grass", "草之徽章",
              "Grass Badge", [("gossifleur", 19), ("eldegoss", 20)]),
            g("hulbury", "露璃娜", "Nessa", "水舟道馆", "Water", "水之徽章",
              "Water Badge",
              [("goldeen", 22), ("arrokuda", 23), ("drednaw", 24)]),
            g("motostoke", "卡芜", "Kabu", "机擎道馆", "Fire", "火之徽章",
              "Fire Badge",
              [("ninetales", 25), ("arcanine", 25), ("centiskorch", 27)]),
            g("stow-on-side", "彩豆", "Bea", "溯传道馆", "Fighting", "格斗徽章",
              "Fighting Badge",
              [("hitmontop", 34), ("pangoro", 34), ("sirfetchd", 35),
               ("machamp", 36)]),
            g("ballonlea", "波普菈", "Opal", "舞姿道馆", "Fairy", "妖精徽章",
              "Fairy Badge",
              [("weezing-galar", 36), ("mawile", 36), ("togekiss", 37),
               ("alcremie", 38)]),
            g("circhester", "玛瓜", "Gordie", "战竞道馆", "Rock", "岩石徽章",
              "Rock Badge",
              [("barbaracle", 40), ("shuckle", 40), ("stonjourner", 41),
               ("coalossal", 42)]),
            g("spikemuth", "聂梓", "Piers", "尖钉道馆", "Dark", "恶之徽章",
              "Dark Badge",
              [("scrafty", 44), ("malamar", 44), ("skuntank", 44),
               ("obstagoon", 46)]),
            g("hammerlocke", "奇巴纳", "Raihan", "宫门道馆", "Dragon", "龙之徽章",
              "Dragon Badge",
              [("flygon", 46), ("sandaconda", 46), ("gigalith", 47),
               ("duraludon", 48)]),
        ],
        "elite4": [
            e("玛俐", "Marnie", "Dark",
              [("morpeko", 50), ("liepard", 50), ("toxicroak", 51),
               ("scrafty", 51), ("grimmsnarl", 52)], title="冠军杯"),
            e("彼特", "Bede", "Fairy",
              [("mawile", 51), ("rapidash-galar", 51), ("gardevoir", 52),
               ("hatterene", 53)], title="冠军杯"),
            e("赫普", "Hop", "Normal",
              [("dubwool", 52), ("corviknight", 52), ("pincurchin", 52),
               ("snorlax", 53)], title="冠军杯"),
            e("奇巴纳", "Raihan", "Dragon",
              [("flygon", 52), ("sandaconda", 52), ("gigalith", 53),
               ("duraludon", 54)], title="冠军杯"),
        ],
        "champion": {
            "name": "丹帝", "name_en": "Leon", "title": "冠军",
            "team": [{"species": s, "level": lv} for s, lv in [
                ("aegislash", 62), ("haxorus", 62), ("rhyperior", 62),
                ("dragapult", 62), ("rillaboom", 63), ("charizard", 64)]],
        },
    },
    "alola": {
        "gyms": [
            g("verdant-cavern", "伊利马", "Ilima", "考验", "Normal", "一般Z",
              "Normalium Z", [("yungoos", 10), ("gumshoos", 12)]),
            g("iki-town", "哈拉", "Hala", "大考验", "Fighting", "格斗Z",
              "Fightinium Z",
              [("mankey", 14), ("makuhita", 14), ("crabrawler", 16)]),
            g("brooklet-hill", "水莲", "Lana", "考验", "Water", "水Z",
              "Waterium Z", [("dewpider", 18), ("wishiwashi", 20)]),
            g("wela-volcano-park", "卡奇", "Kiawe", "考验", "Fire", "火Z",
              "Firium Z", [("salandit", 20), ("salazzle", 22)]),
            g("lush-jungle", "玛奥", "Mallow", "考验", "Grass", "草Z",
              "Grassium Z", [("fomantis", 22), ("lurantis", 24)]),
            g("konikoni-city", "丽姿", "Olivia", "大考验", "Rock", "岩石Z",
              "Rockium Z",
              [("nosepass", 26), ("boldore", 26), ("lycanroc", 28)]),
            g("mount-hokulani", "马玛内", "Sophocles", "考验", "Electric", "电Z",
              "Electrium Z",
              [("charjabug", 27), ("togedemaru", 28), ("vikavolt", 29)]),
            g("thrifty-megamart", "阿塞萝拉", "Acerola", "考验", "Ghost", "幽灵Z",
              "Ghostium Z", [("gengar", 32), ("mimikyu", 33)]),
            g("malie-city", "默丹", "Nanu", "大考验", "Dark", "恶Z", "Darkinium Z",
              [("sableye", 38), ("krokorok", 38), ("persian", 39)]),
            g("seafolk-village", "哈普乌", "Hapu", "大考验", "Ground", "地面Z",
              "Groundium Z",
              [("dugtrio", 47), ("gastrodon", 47), ("flygon", 47),
               ("mudsdale", 48)]),
            g("vast-poni-canyon", "茉莉", "Mina", "考验", "Fairy", "妖精Z",
              "Fairium Z", [("mawile", 49), ("ribombee", 51)]),
        ],
        "elite4": [
            e("哈拉", "Hala", "Fighting",
              [("hariyama", 54), ("primeape", 54), ("bewear", 54),
               ("crabominable", 54), ("poliwrath", 55)], title="四天王"),
            e("丽姿", "Olivia", "Rock",
              [("relicanth", 54), ("lycanroc", 54), ("golem", 54),
               ("probopass", 54), ("cradily", 55)], title="四天王"),
            e("阿塞萝拉", "Acerola", "Ghost",
              [("sableye", 54), ("dhelmise", 54), ("froslass", 54),
               ("drifblim", 54), ("palossand", 55)], title="四天王"),
            e("卡希丽", "Kahili", "Flying",
              [("skarmory", 54), ("crobat", 54), ("oricorio", 54),
               ("mandibuzz", 54), ("toucannon", 55)], title="四天王"),
        ],
        "champion": {
            "name": "库库伊", "name_en": "Kukui", "title": "冠军",
            "team": [{"species": s, "level": lv} for s, lv in [
                ("lycanroc", 57), ("magnezone", 57), ("braviary", 57),
                ("ninetales", 57), ("snorlax", 58)]],
        },
    },
}


DATA["paldea"] = {
    "gyms": [
        g("paldea-cortondo", "阿枫", "Katy", "圆模道馆", "Bug", "圆模徽章",
          "Cortondo Badge", [("nymble", 14), ("tarountula", 15)]),
        g("paldea-artazon", "寇沙", "Brassius", "深钵道馆", "Grass", "深钵徽章",
          "Artazon Badge", [("smoliv", 16), ("petilil", 17), ("sudowoodo", 18)]),
        g("paldea-levincia", "奇树", "Iono", "酿光道馆", "Electric", "酿光徽章",
          "Levincia Badge", [("wattrel", 23), ("luxio", 24), ("mismagius", 25)]),
        g("paldea-cascarrafa", "海岱", "Kofu", "玻瓶道馆", "Water", "玻瓶徽章",
          "Cascarrafa Badge", [("veluza", 29), ("wugtrio", 30), ("crabominable", 31)]),
        g("paldea-medali", "青木", "Larry", "锦汇道馆", "Normal", "锦汇徽章",
          "Medali Badge", [("komala", 35), ("dudunsparce", 36), ("staraptor", 37)]),
        g("paldea-montenevera", "莱姆", "Ryme", "冰柜道馆", "Ghost", "冰柜徽章",
          "Montenevera Badge", [("mimikyu", 41), ("banette", 42), ("toxtricity", 43)]),
        g("paldea-alfornada", "莉普", "Tulip", "焙固道馆", "Psychic", "焙固徽章",
          "Alfornada Badge", [("gardevoir", 44), ("espathra", 45), ("farigiraf", 46)]),
        g("paldea-glaseado-mountain", "古鲁夏", "Grusha", "霜抹山道馆", "Ice", "霜抹山徽章",
          "Glaseado Badge", [("frosmoth", 47), ("beartic", 48), ("cetitan", 48)]),
    ],
    "elite4": [
        {"name": "辛俐", "name_en": "Rika", "title": "四天王", "type": "Ground", "team": [{"species": "whiscash", "level": 57}, {"species": "camerupt", "level": 57}, {"species": "donphan", "level": 58}, {"species": "dugtrio", "level": 58}, {"species": "clodsire", "level": 59}]},
        {"name": "波琵", "name_en": "Poppy", "title": "四天王", "type": "Steel", "team": [{"species": "copperajah", "level": 58}, {"species": "bronzong", "level": 58}, {"species": "corviknight", "level": 58}, {"species": "magnezone", "level": 59}, {"species": "tinkaton", "level": 60}]},
        {"name": "青木", "name_en": "Larry", "title": "四天王", "type": "Flying", "team": [{"species": "tropius", "level": 59}, {"species": "staraptor", "level": 59}, {"species": "altaria", "level": 59}, {"species": "flamigo", "level": 60}, {"species": "dudunsparce", "level": 61}]},
        {"name": "八朔", "name_en": "Hassel", "title": "四天王", "type": "Dragon", "team": [{"species": "noivern", "level": 60}, {"species": "dragalge", "level": 60}, {"species": "flapple", "level": 60}, {"species": "baxcalibur", "level": 61}, {"species": "dragapult", "level": 62}]},
    ],
    "champion": {"name": "也慈", "name_en": "Geeta", "title": "冠军", "team": [{"species": "espathra", "level": 62}, {"species": "avalugg", "level": 62}, {"species": "kingambit", "level": 62}, {"species": "veluza", "level": 62}, {"species": "glimmora", "level": 63}, {"species": "dragapult", "level": 63}]},
}

def norm_species(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def load_regions() -> dict:
    maps = json.loads(MAPS.read_text(encoding="utf-8"))
    return maps["regions"]


def resolve_town(region_nodes: dict, region_id: str, slug: str, last_order: list[int]):
    if slug in region_nodes:
        return slug, False
    towns = sorted(
        (n for n, v in region_nodes.items() if v["kind"] == "town"),
        key=lambda n: region_nodes[n]["order"],
    )
    if not towns:
        towns = sorted(region_nodes, key=lambda n: region_nodes[n]["order"])
    if not towns:
        return None, True
    ref = last_order[0] if last_order else -1
    candidate = next(
        (t for t in towns if region_nodes[t]["order"] > ref), towns[-1]
    )
    return candidate, True


def validate_team(team: list[dict], species_keys: set[str], problems: list[str], where: str):
    for mon in team:
        key = norm_species(mon["species"])
        if key not in species_keys:
            problems.append(f"{where}: unknown species {mon['species']!r}->{key!r}")
            continue
        mon["species"] = key
        if not 1 <= mon["level"] <= 100:
            problems.append(f"{where}: level out of range {mon['level']}")


def main() -> int:
    if not MAPS.exists():
        print(f"missing {MAPS}; run build_map_graph.py first")
        return 1
    maps_regions = load_regions()
    species_keys = set(json.loads(SPECIES.read_text(encoding="utf-8")))
    type_keys = set(json.loads(META.read_text(encoding="utf-8"))["type_zh"])

    out_regions: dict[str, dict] = {}
    problems: list[str] = []
    report: list[str] = []
    fallbacks: list[str] = []

    for region in INCLUDED:
        if region not in DATA:
            continue
        nodes = maps_regions.get(region, {}).get("nodes", {})
        zh = maps_regions.get(region, {}).get("zh", region)
        src = DATA[region]
        last_order: list[int] = [-1]

        gyms_out = []
        for order, gym in enumerate(src["gyms"], start=1):
            location, fell_back = resolve_town(
                nodes, region, gym["location"], last_order
            )
            if location is None:
                problems.append(f"{region} gym {order}: no location available")
                continue
            if fell_back:
                fallbacks.append(
                    f"{region} gym {order}: {gym['location']} -> {location}"
                )
            last_order[0] = nodes[location]["order"]
            entry = {"order": order}
            entry.update(
                {
                    "location": location,
                    "leader": gym["leader"],
                    "leader_en": gym["leader_en"],
                    "title": gym["title"],
                    "type": gym["type"],
                    "badge": gym["badge"],
                    "badge_en": gym["badge_en"],
                    "team": [dict(m) for m in gym["team"]],
                }
            )
            validate_team(entry["team"], species_keys, problems,
                          f"{region} gym {order} {gym['leader']}")
            if gym["type"] not in type_keys:
                problems.append(f"{region} gym {order}: bad type {gym['type']!r}")
            gyms_out.append(entry)

        elite_out = []
        for order, member in enumerate(src["elite4"], start=1):
            entry = {"order": order}
            entry.update(
                {
                    "name": member["name"],
                    "name_en": member["name_en"],
                    "title": member.get("title", "四天王"),
                    "type": member["type"],
                    "team": [dict(m) for m in member["team"]],
                }
            )
            validate_team(entry["team"], species_keys, problems,
                          f"{region} elite4 {order} {member['name']}")
            if member["type"] not in type_keys:
                problems.append(f"{region} elite4 {order}: bad type {member['type']!r}")
            attach_ace_mega(entry["team"])
            elite_out.append(entry)

        champ = src["champion"]
        champ_entry = {
            "name": champ["name"],
            "name_en": champ["name_en"],
            "title": champ["title"],
            "team": [dict(m) for m in champ["team"]],
        }
        validate_team(champ_entry["team"], species_keys, problems,
                      f"{region} champion {champ['name']}")
        attach_ace_mega(champ_entry["team"])

        # ── level monotonicity ──────────────────────────────────────────
        def max_lv(team: list[dict]) -> int:
            return max(m["level"] for m in team)

        ace_gyms = [max_lv(m["team"]) for m in gyms_out]
        ace_e4 = [max_lv(m["team"]) for m in elite_out]
        ace_champ = max_lv(champ_entry["team"])
        problems.extend(
            f"{region}: gym ace level decreased {a}->{b}"
            for a, b in itertools.pairwise(ace_gyms)
            if b < a
        )
        problems.extend(
            f"{region}: elite4 ace level decreased {a}->{b}"
            for a, b in itertools.pairwise(ace_e4)
            if b < a
        )
        if ace_e4 and ace_e4[0] <= ace_gyms[-1]:
            problems.append(f"{region}: elite4 does not outlevel last gym")
        if ace_e4 and ace_champ <= ace_e4[-1]:
            problems.append(f"{region}: champion does not outlevel elite4")

        # ── team-size constraints ───────────────────────────────────────
        problems.extend(
            f"{region} gym {entry['order']}: team size {len(entry['team'])}"
            for entry in gyms_out
            if not 2 <= len(entry["team"]) <= 5
        )
        problems.extend(
            f"{region} elite4 {entry['order']}: team size {len(entry['team'])}"
            for entry in elite_out
            if not 4 <= len(entry["team"]) <= 6
        )
        if not 5 <= len(champ_entry["team"]) <= 6:
            problems.append(f"{region} champion: team size {len(champ_entry['team'])}")

        out_regions[region] = {
            "zh": zh,
            "version": VERSIONS[region],
            "gyms": gyms_out,
            "elite4": elite_out,
            "champion": champ_entry,
        }

        report.append(
            f"{region}: gyms={len(gyms_out)} (L{ace_gyms[0]}..L{ace_gyms[-1]}) "
            f"elite4={len(elite_out)} (L{ace_e4[0]}..L{ace_e4[-1]}) "
            f"champion={champ['name']} (L{ace_champ}) fallbacks={sum(1 for f in fallbacks if f.startswith(region))}"
        )

    out = {"meta": {"source": "hand-curated (real game data)"}, "regions": out_regions}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print("=== build_gym_data report ===")
    for line in report:
        print(line)
    print("location fallbacks:")
    for line in fallbacks:
        print("  -", line)
    print(f"regions={len(out_regions)} wrote {OUT}")

    if problems:
        print("VALIDATION FAILED:")
        for problem in problems:
            print("  -", problem)
        return 1
    print("self-check: OK (locations/species/levels/team sizes/monotonic)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
