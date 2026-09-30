"""训练家档案:队伍 / 电脑 / 背包 / 金钱 / 徽章 / 图鉴 / 位置。"""

from __future__ import annotations

import contextlib
import os
from typing import Any

from .dex import get_dex
from .engine import Pokemon, create_pokemon
from .items import BAG_ITEMS, resolve_bag_item
from .util import ensure_dir, read_json, safe_name, write_json_atomic

MAX_PARTY = 6
START_MONEY = 3000
START_BAG = {"poke-ball": 5, "potion": 3, "antidote": 1}

# 队伍字典里由本层维护、`Pokemon.to_dict()` 不认识的附加字段
# pending_tm:待决定的招式如果是招式机教的,记住是哪台机器 ——
# 决定"替换"时才真正消耗它,决定"放弃"则留在背包里(不浪费)
EXTRA_KEYS = ("id", "pending", "pending_tm", "met_at", "met_level", "box_at")


def dict_to_mon(d: dict) -> Pokemon:
    return Pokemon.from_dict(d)


def mon_to_dict(mon: Pokemon, prev: dict | None = None) -> dict:
    """序列化并把附加字段(背包格的 id / 待学招式)带回来。

    走 `to_storage_dict()`:Mega 进化的形态**只在战斗中有效**,写回存档时
    会自动还原成原种(HP/数值按比例带回去)。
    """
    d = mon.to_storage_dict()
    if prev:
        for k in EXTRA_KEYS:
            if prev.get(k):
                d[k] = prev[k]
    return d


class Trainer:
    """单个玩家的存档(可变 dict 包装)。"""

    def __init__(self, data: dict, uid: str = "", scope: str = ""):
        self.data = data
        self.uid = uid
        self.scope = scope
        self._fixup()

    # ── 生命周期 ──
    def _fixup(self) -> None:
        d = self.data
        d.setdefault("uid", self.uid)
        d.setdefault("scope", self.scope)
        d.setdefault("name", "训练家")
        d.setdefault("money", START_MONEY)
        d.setdefault("party", [])
        d.setdefault("box", [])
        d.setdefault("bag", {})
        d.setdefault("badges", [])
        d.setdefault("dex_seen", [])
        d.setdefault("dex_caught", [])
        # 闪光(异色)图鉴:记录哪些物种抓过闪光形态(与普通捕获分开)
        d.setdefault("shiny_caught", [])
        d.setdefault("region", "kanto")
        d.setdefault("location", "")
        d.setdefault("visited", [])
        d.setdefault("unlocked_regions", ["kanto"])
        d.setdefault("flags", {})
        d.setdefault("stats", {})
        d.setdefault("created_at", 0)
        d.setdefault("play_day", 0)
        self._reindex()

        # 旧档补算(1.30.3 之前进化不写图鉴):手上与箱子里的形态都算「得到过」
        # —— 自愈式迁移,每次载入都会补齐,不用玩家做任何操作
        for md in (self.data.get("party") or []) + (self.data.get("box") or []):
            sp = str((md or {}).get("species") or "")
            if not sp:
                continue
            if sp not in self.data["dex_seen"]:
                self.data["dex_seen"].append(sp)
            if sp not in self.data["dex_caught"]:
                self.data["dex_caught"].append(sp)
            if md.get("shiny") and sp not in self.data["shiny_caught"]:
                self.data["shiny_caught"].append(sp)
    def _reindex(self) -> None:
        """确保每只都有稳定 id(离线/老存档补齐)。"""
        used = set()
        for p in self.party + self.box:
            pid = str(p.get("id") or "")
            if not pid or pid in used:
                pid = f"m{len(used) + 1}"
                while pid in used:
                    pid += "x"
                p["id"] = pid
            used.add(pid)

    # ── 基础属性 ──
    @property
    def name(self) -> str:
        return str(self.data.get("name") or "训练家")

    @property
    def money(self) -> int:
        return int(self.data.get("money", 0) or 0)

    @property
    def party(self) -> list[dict]:
        return self.data["party"]

    @property
    def box(self) -> list[dict]:
        return self.data["box"]

    @property
    def bag(self) -> dict[str, int]:
        return self.data["bag"]

    @property
    def badges(self) -> list[str]:
        return self.data["badges"]

    @property
    def region(self) -> str:
        return str(self.data.get("region") or "kanto")

    @property
    def location(self) -> str:
        return str(self.data.get("location") or "")

    def flag(self, key: str, default: Any = False) -> Any:
        return (self.data.get("flags") or {}).get(key, default)

    def set_flag(self, key: str, value: Any = True) -> None:
        self.data.setdefault("flags", {})[key] = value

    def day_no(self, day: int | None = None) -> int:
        """玩家自己的第 N 天(创建那天 = 第 1 天)。

        `play_day` 存的是**绝对天数序号**(ordinal),直接显示就是"第 739885 天"。
        """
        from .util import game_day

        start = int(self.data.get("play_day") or 0)
        if not start:
            return 1
        return max(1, int(day if day is not None else game_day()) - start + 1)

    def badge_count(self, region: str | None = None) -> int:
        region = region or self.region
        return sum(1 for b in self.badges if str(b).startswith(f"{region}:"))

    def add_badge(self, region: str, order: int) -> bool:
        key = f"{region}:{int(order)}"
        if key in self.badges:
            return False
        self.badges.append(key)
        return True

    # ── 队伍 ──
    def mon(self, index: int) -> Pokemon | None:
        if 0 <= index < len(self.party):
            return dict_to_mon(self.party[index])
        return None

    def commit(self, index: int, mon: Pokemon) -> None:
        if 0 <= index < len(self.party):
            self.party[index] = mon_to_dict(mon, self.party[index])
        # 图鉴解锁:凡是「得到过」的形态都算 —— 进化出来的也要解锁
        # (实测反馈:进化成炽焰咆哮虎后 /图鉴 仍是未发现;以前只登记野生捕获)
        sp = str(getattr(mon, "species", "") or "")
        if sp:
            self.mark_caught(sp)

    def party_mon(self) -> list[Pokemon]:
        return [dict_to_mon(p) for p in self.party]

    def first_healthy(self) -> int | None:
        for i, p in enumerate(self.party):
            if int(p.get("cur_hp", 0) or 0) > 0:
                return i
        return None

    def all_fainted(self) -> bool:
        return self.first_healthy() is None

    def find(self, query: str) -> tuple[int, Pokemon] | None:
        """按序号(从 1 开始)、昵称、物种中英文、id 找队伍成员。"""
        dex = get_dex()
        q = str(query or "").strip()
        if not q:
            return None
        if q.isdigit():
            i = int(q) - 1
            if 0 <= i < len(self.party):
                return i, dict_to_mon(self.party[i])
            return None
        nq = q.lower()
        for i, p in enumerate(self.party):
            pid = str(p.get("id") or "")
            nick = str(p.get("nickname") or "")
            if pid and pid.lower() == nq:
                return i, dict_to_mon(p)
            if nick and nick.lower() == nq:
                return i, dict_to_mon(p)
        resolved = dex.resolve_species(q)
        if resolved:
            for i, p in enumerate(self.party):
                if p.get("species") == resolved[0]:
                    return i, dict_to_mon(p)
        for i, p in enumerate(self.party):
            if nq and nq in str(p.get("nickname") or "").lower():
                return i, dict_to_mon(p)
        return None

    def add_pokemon(self, mon: Pokemon, *, to_box: bool = False, day: int = 0) -> dict:
        """入队(满 6 自动进电脑)。返回附加了 id 的字典。"""
        d = mon.to_dict()
        used = {str(p.get("id")) for p in self.party + self.box}
        n = 1
        while f"m{n}" in used:
            n += 1
        d["id"] = f"m{n}"
        d["met_level"] = mon.level
        d["met_at"] = int(day or 0)
        if not to_box and len(self.party) < MAX_PARTY:
            self.party.append(d)
        else:
            d["box_at"] = int(day or 0)
            self.box.append(d)
        self.mark_caught(mon.species)
        return d

    def remove_pokemon(self, index: int) -> Pokemon | None:
        if not 0 <= index < len(self.party):
            return None
        d = self.party.pop(index)
        if len(self.party) == 0 and self.box:
            back = self.box.pop(0)
            self.party.append(back)
        return dict_to_mon(d)

    def deposit(self, index: int) -> bool:
        if len(self.party) <= 1 or not 0 <= index < len(self.party):
            return False
        d = self.party.pop(index)
        self.box.append(d)
        return True

    def box_find(self, ident) -> tuple[int, dict] | None:
        """在**电脑**里找:序号(1 起)/id/昵称/物种(中英文)。"""
        q = str(ident or "").strip()
        if not q:
            return None
        if q.isdigit():
            i = int(q) - 1
            return (i, self.box[i]) if 0 <= i < len(self.box) else None
        nq = q.lower()
        for i, p in enumerate(self.box):
            if nq and nq in (str(p.get("id") or "").lower(), str(p.get("nickname") or "").lower()):
                return i, p
        resolved = get_dex().resolve_species(q)
        if resolved:
            for i, p in enumerate(self.box):
                if p.get("species") == resolved[0]:
                    return i, p
        for i, p in enumerate(self.box):
            if nq and nq in str(p.get("nickname") or "").lower():
                return i, p
        return None

    def swap_party(self, a: int, b: int) -> bool:
        """交换队伍里的两只(1 起的序号)。"""
        if a == b:
            return False
        n = len(self.party)
        if not (1 <= a <= n) or not (1 <= b <= n):
            return False
        self.party[a - 1], self.party[b - 1] = self.party[b - 1], self.party[a - 1]
        return True

    def release_pokemon(self, ident, *, where: str = "party") -> Pokemon | None:
        """放生。返回被放生的宝可梦;不合法返回 None。

        - 队伍里最后一只有战斗力的宝可梦不能放生(否则玩家没有可用宝可梦);
        - 队伍成员放生后队伍为空时会自动从电脑补一只(沿用 remove_pokemon 的行为)。
        """
        if where == "box":
            hit = self.box_find(ident)
            if hit is None:
                return None
            return dict_to_mon(self.box.pop(hit[0]))
        if where == "party":
            if isinstance(ident, str) and ident.isdigit():
                index = int(ident) - 1
            else:
                found = self.find(ident)
                if found is None:
                    return None
                index = found[0]
            if len(self.party) <= 1:
                return None      # 不能把最后一只放掉
            return self.remove_pokemon(index)
        return None

    def withdraw(self, ident: str) -> bool:
        if len(self.party) >= MAX_PARTY:
            return False
        for i, p in enumerate(self.box):
            if str(p.get("id")) == ident or str(p.get("nickname")) == ident:
                self.party.append(self.box.pop(i))
                return True
        return False

    # ── 背包 / 金钱 ──
    def add_item(self, key: str, n: int = 1) -> int:
        r = resolve_bag_item(key)
        k = r[0] if r else key
        self.bag[k] = int(self.bag.get(k, 0)) + max(0, int(n))
        if self.bag[k] <= 0:
            self.bag.pop(k, None)
        return int(self.bag.get(k, 0))

    def count(self, key: str) -> int:
        r = resolve_bag_item(key)
        return int(self.bag.get(r[0] if r else key, 0) or 0)

    def take_item(self, key: str, n: int = 1) -> bool:
        r = resolve_bag_item(key)
        k = r[0] if r else key
        have = int(self.bag.get(k, 0) or 0)
        if have < n:
            return False
        left = have - n
        if left:
            self.bag[k] = left
        else:
            self.bag.pop(k, None)
        return True

    def bag_items(self) -> list[tuple[str, dict, int]]:
        out = []
        for k, n in sorted(self.bag.items()):
            entry = BAG_ITEMS.get(k)
            if entry and n:
                out.append((k, entry, int(n)))
        return out

    def add_money(self, n: int) -> int:
        self.data["money"] = max(0, self.money + int(n))
        return self.data["money"]

    def spend_money(self, n: int) -> bool:
        if self.money < n:
            return False
        self.data["money"] = self.money - int(n)
        return True

    # ── 图鉴 ──
    def mark_seen(self, species: str) -> None:
        if species and species not in self.data["dex_seen"]:
            self.data["dex_seen"].append(species)

    def mark_caught(self, species: str) -> None:
        self.mark_seen(species)
        if species and species not in self.data["dex_caught"]:
            self.data["dex_caught"].append(species)

    def seen(self, species: str) -> bool:
        return species in self.data["dex_seen"]

    def caught(self, species: str) -> bool:
        return species in self.data["dex_caught"]

    def mark_shiny(self, species: str) -> bool:
        """记录抓到了闪光形态;返回是否是新纪录(第一次抓到这只的闪光)。"""
        if not species:
            return False
        shiny = self.data.setdefault("shiny_caught", [])
        if species in shiny:
            return False
        shiny.append(species)
        return True

    def shiny_caught(self, species: str) -> bool:
        return species in (self.data.get("shiny_caught") or [])

    def shiny_count(self) -> int:
        return len(self.data.get("shiny_caught") or [])

    # ── 治疗 ──
    def heal_party(self) -> int:
        n = 0
        for i, p in enumerate(self.party):
            mon = dict_to_mon(p)
            mon.full_heal()
            self.party[i] = mon_to_dict(mon, p)
            n += 1
        return n

    def party_summary_count(self) -> dict:
        return {
            "party": len(self.party),
            "box": len(self.box),
            "alive": sum(1 for p in self.party if int(p.get("cur_hp", 0) or 0) > 0),
            "caught": len(self.data["dex_caught"]),
            "seen": len(self.data["dex_seen"]),
        }


def new_trainer(
    uid: str,
    scope: str,
    name: str,
    *,
    region: str = "kanto",
    starter: str = "",
    day: int = 0,
    now: int = 0,
) -> Trainer:
    """创建新训练家:位置为地区起始城镇,初始金钱/背包/御三家。"""
    from .world import WorldMap

    world = WorldMap()
    start = world.start_location(region)
    data = {
        "uid": uid,
        "scope": scope,
        "name": (name or "训练家").strip()[:16],
        "money": START_MONEY,
        "party": [],
        "box": [],
        "bag": dict(START_BAG),
        "badges": [],
        "dex_seen": [],
        "dex_caught": [],
        "region": region,
        "location": start,
        "visited": [start] if start else [],
        "unlocked_regions": [region],
        "flags": {},
        "created_at": int(now or 0),
        "play_day": int(day or 0),
    }
    t = Trainer(data, uid=uid, scope=scope)
    if starter:
        mon = create_pokemon(starter, level=5)
        # 初始伙伴亲密度更高、性别/性格随机已由 create_pokemon 决定
        mon.friendship = 120
        t.add_pokemon(mon, day=day)
    return t


class TrainerStore:
    """训练家存档。

    默认走 SQLite(见 `pw/sqlite_store.py`);`backend=None` 时退回旧的
    "每 scope 一个目录、每 uid 一个 JSON 文件"实现(配置 storage=json)。
    """

    def __init__(self, data_dir: str, *, backend=None):
        self._db = backend
        self._root = ensure_dir(os.path.join(data_dir, "pokemon_world"))

    def _scope_dir(self, scope: str) -> str:
        return ensure_dir(os.path.join(self._root, safe_name(scope)))

    def _path(self, scope: str, uid: str) -> str:
        return os.path.join(self._scope_dir(scope), f"{safe_name(uid)}.json")

    def load(self, scope: str, uid: str) -> dict | None:
        """读存档;文件缺失或内容损坏/为空时返回 None(调用方据此判断"没存档")。

        注意语义:损坏与"不存在"都返回 None —— 因此判断"是否已开始旅程"要看
        `exists()`(文件在不在),只看 load() 会把损坏存档误判成"还没开始"。
        """
        if self._db is not None:
            return self._db.load_trainer(scope, uid)
        data = read_json(self._path(scope, uid))
        return data if isinstance(data, dict) and data else None

    def save(self, scope: str, uid: str, data: dict) -> None:
        if self._db is not None:
            self._db.save_trainer(
                scope, uid, data, day=int((data or {}).get("day") or 0)
            )
            return
        write_json_atomic(self._path(scope, uid), data)

    def rename_player(self, scope: str, old: str, new: str, *,
                      force: bool = False) -> tuple[int, str]:
        """玩家换 ID:把 (scope, old) 的存档搬到 (scope, new)。"""
        scope, old, new = str(scope or ""), str(old or ""), str(new or "")
        if not scope or not old or not new:
            return 0, "群 ID / 新旧玩家 ID 不能为空"
        if old == new:
            return 0, "新旧玩家 ID 相同,无需迁移"
        data = self.load(scope, old)
        if data is None:
            return 0, f"群里没有 {old} 的存档"
        if not force and self.load(scope, new) is not None:
            return 0, f"{new} 在该群已有存档,已拒绝(确认无误后加 force)"
        self.save(scope, new, data)
        self.delete(scope, old)
        return 1, ""

    def rename_scope(self, old: str, new: str, *, force: bool = False
                     ) -> tuple[int, str]:
        """整群玩家存档换 ID(世界状态由 WorldStore.rename 负责)。

        返回 (迁移的玩家数, 错误信息)。目标群已有存档时**拒绝**(除非 force),
        避免静默覆盖别人的存档。
        """
        old, new = str(old or ""), str(new or "")
        if not old or not new:
            return 0, "旧/新群 ID 不能为空"
        if old == new:
            return 0, "新旧群 ID 相同,无需迁移"
        if not force and self.list_players(new):
            return 0, f"目标群 {new} 已有存档,已拒绝(确认无误后加 force)"
        players = self.list_players(old)
        moved = 0
        for uid in players:
            data = self.load(old, uid)
            if data is None:
                continue
            self.save(new, uid, data)
            self.delete(old, uid)
            moved += 1
        return moved, ""

    def exists(self, scope: str, uid: str) -> bool:
        if self._db is not None:
            return self._db.trainer_exists(scope, uid)
        return os.path.exists(self._path(scope, uid))

    def backup_corrupt(self, scope: str, uid: str) -> str:
        """把读不出来的存档改名备份,返回备份文件名。

        存档文件存在但内容损坏时,`/状态` 会走 load() 说"还没开始"(load 返回 None),
        而 `/开始` 看到文件存在又说"已经开始了" —— 玩家被两句话夹住、一脸茫然。
        改名备份后允许重新开始,旧文件也不丢。
        """
        if self._db is not None:
            return self._db.backup_corrupt_trainer(scope, uid, "unreadable")
        path = self._path(scope, uid)
        bak = path + ".bak"
        try:
            if os.path.exists(bak):
                os.remove(bak)
            os.rename(path, bak)
        except OSError:
            return ""
        return os.path.basename(bak)

    def delete(self, scope: str, uid: str) -> bool:
        if self._db is not None:
            return self._db.delete_trainer(scope, uid)
        p = self._path(scope, uid)
        if os.path.exists(p):
            os.remove(p)
            return True
        return False

    def list_players(self, scope: str) -> list[str]:
        if self._db is not None:
            return self._db.list_players(scope)
        d = self._scope_dir(scope)
        return sorted(
            f[:-5] for f in os.listdir(d) if f.endswith(".json") and not f.startswith("_")
        )

    def list_scopes(self) -> list[str]:
        """所有有玩家存档的 scope(JSON 后端就扫目录)。"""
        if self._db is not None:
            return self._db.list_trainer_scopes()
        if not os.path.isdir(self._root):
            return []
        return sorted(
            name
            for name in os.listdir(self._root)
            if os.path.isdir(os.path.join(self._root, name))
        )

    def scope_stats(self) -> dict:
        """库统计(管理页总览用)。"""
        if self._db is not None:
            return self._db.scope_stats()
        files = size = 0
        for root, _dirs, fs in os.walk(self._root):
            for f in fs:
                if f.endswith(".json"):
                    files += 1
                    with contextlib.suppress(OSError):
                        size += os.path.getsize(os.path.join(root, f))
        return {"trainers": files, "worlds": 0, "backups": 0, "size": size,
                "path": self._root}

    def delete_scope(self, scope: str) -> int:
        if self._db is not None:
            return self._db.delete_scope(scope)
        import shutil

        d = os.path.join(self._root, safe_name(scope))
        if not os.path.isdir(d):
            return 0
        # 排除 _world.json 等共享文件:它们不是玩家存档(否则提示数量多 1)
        n = len([
            f for f in os.listdir(d)
            if f.endswith(".json") and not f.startswith("_")
        ])
        shutil.rmtree(d, ignore_errors=True)
        return n
