"""NPC:道路训练家 / 道馆馆主 / 四天王 / 冠军 / 邪恶组织成员。

道馆与联盟的等级、队伍直接取自 `gyms.json`(真实游戏数据),
道路上的杂兵则按玩家进度用 `trainer.generate_team` 生成(贴合当前实力)。
"""

from __future__ import annotations

from .dex import get_dex
from .encounter import roll_location_encounter
from .trainer import generate_team
from .util import clamp, stable_rng
from .world import WorldMap

ROUTE_TRAINER_NAMES = [
    "短裤小子",
    "捕虫少年",
    "登山男",
    "露营少女",
    "钓客",
    "泳者",
    "观光客",
    "研究员",
    "记者",
    "画家",
    "水手",
    "农夫",
    "精英训练家",
    "忍者",
]
ROUTE_TRAINER_GIVEN = [
    "阿明",
    "小健",
    "大辅",
    "由美",
    "拓也",
    "奈奈",
    "小茜",
    "健太",
    "美月",
    "翔太",
    "莉子",
    "和也",
]


def route_trainers(trainer, location: str, *, day: int = 0) -> list[dict]:
    """该地点今天可能遇到的训练家(稳定随机,不落盘)。"""
    world = WorldMap()
    if not location:
        return []
    rng = stable_rng("npc", trainer.scope, location, day)
    budget = world.tier(location)
    n = rng.randint(1, min(3, max(1, budget // 2 + 1)))
    out = []
    for i in range(n):
        title = rng.choice(ROUTE_TRAINER_NAMES)
        given = rng.choice(ROUTE_TRAINER_GIVEN)
        out.append(
            {
                "id": f"{location}:{day}:{i}",
                "name": f"{title} {given}",
                "tier": clamp(budget + rng.randint(-1, 1), 1, 8),
                "location": location,
            }
        )
    return out


def build_route_battle(trainer, npc: dict, *, day: int = 0) -> dict:
    """按玩家实力生成一名道路训练家的队伍(返回 meta)。"""
    dex = get_dex()
    rng = stable_rng("npc-team", trainer.scope, npc.get("id") or "", day)
    tier = int(npc.get("tier", 1) or 1)
    difficulty = {1: "简单", 2: "简单", 3: "普通", 4: "普通", 5: "普通"}.get(
        tier, "困难"
    )
    team = generate_team(
        dex,
        party=trainer.party,
        trainer=str(npc.get("name") or "训练家"),
        level=0,
        difficulty=difficulty,
        location=trainer.location,
        region=trainer.region,
        rng=rng,
    )
    specs = [
        {"species": m["species"], "level": int(m["level"]), "item": m.get("item", "")}
        for m in (team.get("team") or [])
    ]
    return {
        "kind": "trainer",
        "title": f"训练家 {npc.get('name')} 的挑战",
        "npc": npc,
        "team": specs,
        "location": trainer.location,
        "region": trainer.region,
    }


def build_gym_battle(trainer, gym: dict) -> dict:
    """真实道馆队伍(等级/物种来自 gyms.json)。"""
    specs = [
        {
            "species": m["species"],
            "level": int(clamp(m.get("level", 10), 1, 100)),
            "moves": list(m.get("moves") or []),
            "item": str(m.get("item") or ""),
        }
        for m in (gym.get("team") or [])
        if isinstance(m, dict) and m.get("species")
    ]
    kind = "trial" if gym.get("kind") == "trial" or "考验" in str(gym.get("title", "")) else "gym"
    return {
        "kind": kind,
        "title": f"{gym.get('title') or '道馆'} — {gym.get('leader') or '馆主'}",
        "gym": gym,
        "team": specs,
        "region": trainer.region,
        "location": trainer.location,
        "weather": _gym_weather(gym),
    }


def _gym_weather(gym: dict) -> str:
    t = str(gym.get("type") or "")
    return {
        "Ice": "snow",
        "Fire": "sun",
        "Water": "rain",
        "Rock": "sand",
        "Ground": "sand",
    }.get(t, "")


def build_elite4_battle(trainer, member: dict) -> dict:
    specs = [
        {
            "species": m["species"],
            "level": int(clamp(m.get("level", 50), 1, 100)),
            "item": str(m.get("item") or ""),
            "moves": list(m.get("moves") or []),
        }
        for m in (member.get("team") or [])
        if isinstance(m, dict) and m.get("species")
    ]
    return {
        "kind": "elite",
        "title": f"四天王 — {member.get('name') or ''}",
        "elite": member,
        "team": specs,
        "region": trainer.region,
        "location": trainer.location,
    }


def build_champion_battle(trainer, champion: dict) -> dict:
    specs = [
        {
            "species": m["species"],
            "level": int(clamp(m.get("level", 55), 1, 100)),
            "item": str(m.get("item") or ""),
            "moves": list(m.get("moves") or []),
        }
        for m in (champion.get("team") or [])
        if isinstance(m, dict) and m.get("species")
    ]
    return {
        "kind": "champion",
        "title": f"冠军 — {champion.get('name') or ''}",
        "champion": champion,
        "team": specs,
        "region": trainer.region,
        "location": trainer.location,
    }


def rocket_battle(trainer, event: dict) -> dict:
    specs = [
        {"species": m["species"], "level": int(clamp(m.get("level", 12), 1, 100))}
        for m in (event.get("team") or [])
        if isinstance(m, dict) and m.get("species")
    ]
    if not specs:
        dex = get_dex()
        team = generate_team(
            dex,
            party=trainer.party,
            trainer=str(event.get("trainer") or "火箭队手下"),
            location=event.get("location") or trainer.location,
            region=event.get("region") or trainer.region,
        )
        specs = [
            {"species": m["species"], "level": int(m["level"])}
            for m in (team.get("team") or [])[:3]
        ]
    return {
        "kind": "rocket",
        "title": f"{event.get('trainer') or '火箭队手下'}的阻挠",
        "event_id": event.get("id"),
        "location": event.get("location") or trainer.location,
        "region": event.get("region") or trainer.region,
        "team": specs,
    }


def legendary_at(trainer, event: dict) -> dict | None:
    """rare 事件可能引来稀有宝可梦(作为野生战)。"""
    world = WorldMap()
    loc = event.get("location") or trainer.location
    if not loc:
        return None
    rng = stable_rng("legend", trainer.scope, event.get("id") or "", event.get("created_day"))
    hit = roll_location_encounter(
        get_dex(), loc, include_special=True, rng=rng
    )
    if not hit:
        return None
    if event.get("species"):
        hit["species"] = event["species"]
        entry = get_dex().species.get(hit["species"]) or {}
        hit["zh"] = entry.get("zh") or entry.get("name") or hit["species"]
        hit["types"] = list(entry.get("types") or [])
    lead = trainer.party[0].get("level", 5) if trainer.party else 5
    hit["level"] = int(clamp(max(hit.get("level", 1), int(lead) + 5), 5, 100))
    hit["_rare"] = True
    _ = world
    return hit
