"""神兽(传说/幻之宝可梦)定点遭遇。

规则参考真实游戏:**特定地点 + 进度阶段**才会出现,捕获率极低(多为 3),
一旦捕获永久入队;战斗失败/被击倒则当天逃走,次日可再战。
"""

from __future__ import annotations

from .dex import get_dex
from .util import clamp
from .world import WorldMap

# 每条:物种 / 地点 / 需求徽章数 / 是否需先成为冠军 / 等级(参考真实游戏)
# 地点标识均已校验存在于 maps.json,缺失的会被 sites_for() 过滤掉。
_SITES: dict[str, list[dict]] = {
    "kanto": [
        {"species": "articuno", "location": "seafoam-islands", "need": 4, "level": 50},
        {"species": "zapdos", "location": "kanto-power-plant", "need": 5, "level": 50},
        {"species": "moltres", "location": "kanto-victory-road-2", "need": 7, "level": 50},
        {"species": "mewtwo", "location": "cerulean-cave", "need": 0, "champion": True, "level": 70},
        {"species": "mew", "location": "birth-island", "need": 0, "champion": True, "level": 30},
    ],
    "johto": [
        {"species": "suicune", "location": "burned-tower", "need": 4, "level": 40},
        {"species": "lugia", "location": "whirl-islands", "need": 7, "level": 45},
        {"species": "ho-oh", "location": "bell-tower", "need": 8, "level": 45},
        {"species": "celebi", "location": "sinjoh-ruins", "need": 0, "champion": True, "level": 30},
    ],
    "hoenn": [
        {"species": "regirock", "location": "desert-ruins", "need": 4, "level": 40},
        {"species": "regice", "location": "island-cave", "need": 4, "level": 40},
        {"species": "registeel", "location": "ancient-tomb", "need": 4, "level": 40},
        {"species": "latias", "location": "southern-island", "need": 6, "level": 50},
        {"species": "kyogre", "location": "marine-cave", "need": 8, "level": 70},
        {"species": "groudon", "location": "terra-cave", "need": 8, "level": 70},
        {"species": "rayquaza", "location": "sky-pillar", "need": 0, "champion": True, "level": 70},
    ],
    "sinnoh": [
        {"species": "uxie", "location": "lake-acuity", "need": 5, "level": 50},
        {"species": "mesprit", "location": "lake-verity", "need": 5, "level": 50},
        {"species": "azelf", "location": "lake-valor", "need": 5, "level": 50},
        {"species": "heatran", "location": "stark-mountain", "need": 7, "level": 50},
        {"species": "regigigas", "location": "snowpoint-temple", "need": 8, "level": 70},
        {"species": "dialga", "location": "spear-pillar", "need": 0, "champion": True, "level": 70},
        {"species": "giratina", "location": "mt-coronet", "need": 0, "champion": True, "level": 70},
    ],
    "unova": [
        {"species": "cobalion", "location": "guidance-chamber", "need": 5, "level": 42},
        {"species": "terrakion", "location": "trial-chamber", "need": 5, "level": 42},
        {"species": "virizion", "location": "pinwheel-forest", "need": 5, "level": 42},
        {"species": "reshiram", "location": "dragonspiral-tower", "need": 0, "champion": True, "level": 70},
        {"species": "kyurem", "location": "giant-chasm", "need": 8, "level": 70},
    ],
    "kalos": [
        {"species": "zygarde", "location": "terminus-cave", "need": 8, "level": 70},
        {"species": "xerneas", "location": "team-flare-secret-hq", "need": 0, "champion": True, "level": 70},
        {"species": "yveltal", "location": "team-flare-secret-hq", "need": 0, "champion": True, "level": 70},
    ],
    "alola": [
        {"species": "tapu-koko", "location": "ruins-of-conflict", "need": 6, "level": 60},
        {"species": "tapu-lele", "location": "ruins-of-life", "need": 6, "level": 60},
        {"species": "tapu-bulu", "location": "ruins-of-abundance", "need": 6, "level": 60},
        {"species": "tapu-fini", "location": "ruins-of-hope", "need": 6, "level": 60},
        {"species": "necrozma", "location": "ten-carat-hill", "need": 8, "level": 65},
        {"species": "solgaleo", "location": "altar-of-the-sunne", "need": 0, "champion": True, "level": 70},
    ],
    "galar": [
        {"species": "eternatus", "location": "energy-plant", "need": 8, "level": 70},
        {"species": "zacian", "location": "slumbering-weald", "need": 0, "champion": True, "level": 70},
        {"species": "zamazenta", "location": "slumbering-weald", "need": 0, "champion": True, "level": 70},
    ],
}


def sites_for(region: str, *, world: WorldMap | None = None) -> list[dict]:
    """返回该地区**真实存在**的神兽定点(过滤掉地图里没有的地点/物种)。"""
    world = world or WorldMap()
    dex = get_dex()
    out: list[dict] = []
    for raw in _SITES.get(region, []):
        site = dict(raw)
        # 物种用图鉴解析(容错连字符/别名):tapu-koko → tapukoko, ho-oh → hooh
        resolved = dex.resolve_species(str(site["species"]))
        if not resolved:
            continue
        if site["location"] not in world.nodes(region):
            continue
        site["species"] = resolved[0]
        site["region"] = region
        site["zh"] = (resolved[1] or {}).get("zh") or site["species"]
        site["location_zh"] = world.node_zh(site["location"])
        out.append(site)
    return out


def caught(trainer, species: str) -> bool:
    return bool(trainer.flag(f"legend:caught:{species}"))


def fled_today(trainer, species: str, day: int) -> bool:
    return int(trainer.flag(f"legend:fled:{species}", 0) or 0) == int(day)


def mark_caught(trainer, species: str) -> None:
    trainer.set_flag(f"legend:caught:{species}", True)
    trainer.set_flag(f"legend:fled:{species}", 0)


def mark_fled(trainer, species: str, day: int) -> None:
    trainer.set_flag(f"legend:fled:{species}", int(day))


def ready(trainer, *, world: WorldMap | None = None, day: int = 0) -> list[dict]:
    """当前**可以立即开战**的神兽:位置正确、条件满足、未被捕获、没在今天逃走过。"""
    world = world or WorldMap()
    out = []
    for site in sites_for(trainer.region, world=world):
        if trainer.location != site["location"]:
            continue
        if caught(trainer, site["species"]) or fled_today(trainer, site["species"], day):
            continue
        if site.get("champion") and not trainer.flag(f"champion:{trainer.region}"):
            continue
        if trainer.badge_count(trainer.region) < int(site.get("need", 0)):
            continue
        out.append(site)
    return out


def known(trainer, *, world: WorldMap | None = None) -> list[dict]:
    """玩家"听说"过的神兽(条件已接近:徽章达标或已满足)。"""
    world = world or WorldMap()
    out = []
    for site in sites_for(trainer.region, world=world):
        if caught(trainer, site["species"]):
            continue
        champ_ok = (not site.get("champion")) or bool(trainer.flag(f"champion:{trainer.region}"))
        if champ_ok and trainer.badge_count(trainer.region) >= int(site.get("need", 0)):
            out.append(site)
    return out


def legendary_meta(trainer, site: dict) -> dict:
    dex = get_dex()
    entry = dex.species.get(site["species"]) or {}
    return {
        "kind": "legend",
        "title": f"传说的宝可梦 —— {site['zh']}",
        "legend": site,
        "wild": True,
        "location": site["location"],
        "region": site["region"],
        "team": [
            {
                "species": site["species"],
                "level": int(clamp(site.get("level", 50), 1, 100)),
            }
        ],
        "_types": entry.get("types") or [],
    }


def panel_text(trainer, *, world: WorldMap | None = None, day: int = 0) -> str:
    world = world or WorldMap()
    dex = get_dex()
    here = ready(trainer, world=world, day=day)
    knowns = known(trainer, world=world)
    region = trainer.region
    lines = [
        f"🐉 {world.region_zh(region)} 的传说宝可梦"
        f"(徽章 {trainer.badge_count(region)}/{len(world.gyms(region)) or 8})",
    ]
    if here:
        lines.append("✨ 就在此地:")
        lines.extend(
            f"　· {site['zh']} Lv{site['level']}(捕获率 "
            f"{dex.species.get(site['species'], {}).get('captureRate', '?')})"
            f" —— `/神兽 挑战 {site['zh']}`"
            for site in here
        )
    caughts = [site for site in sites_for(region, world=world)
               if caught(trainer, site["species"])]
    if caughts:
        lines.append("✅ 已收服:" + "、".join(s["zh"] for s in caughts))
    pending = [s for s in knowns if s not in here]
    if pending:
        lines.append("🗺️ 已知栖息地(前往对应地点即可挑战):")
        for s in pending:
            hint = f"(需 {s['need']} 枚徽章)" if int(s.get("need", 0)) > trainer.badge_count(region) else ""
            champ = "(需先成为冠军)" if s.get("champion") and not trainer.flag(f"champion:{region}") else ""
            lines.append(
                f"　· {s['zh']} —— {world.node_zh(s['location'])} Lv{s['level']}{hint}{champ}"
            )
    locked = [
        s
        for s in sites_for(region, world=world)
        if (not caught(trainer, s["species"]))
        and (
            trainer.badge_count(region) < int(s.get("need", 0))
            or (s.get("champion") and not trainer.flag(f"champion:{region}"))
        )
    ]
    if locked:
        lines.append("❓ 传闻中还有别的存在,但你的实力/资历还不够。")
    if not here and not pending and not caughts and not locked:
        lines.append("这一带没有神兽的传说。")
    lines.append("提示:`/探索` 在神兽栖息的地点也会遇到它。")
    return "\n".join(lines)
