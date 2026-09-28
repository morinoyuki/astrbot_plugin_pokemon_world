"""Mega 进化:形态表 / Mega 石 / 触发判定。

数据只有一份来源 —— `pw/static/species.json` 里 `forme` 含 "Mega" 的条目:
每条的 `baseSpecies` 是原种、`requiredItem` 是对应的 Mega 石(如 Heracronite)。
本模块据此生成:

* 形态 → 石头 / 石头 → 形态 的双向索引(超能妙喵/米立龙/玛机雅娜的多个
  形态共用一块石头,按原形态挑);
* 商店里出售的 Mega 石条目(由 `pw.items` 注入 `_ITEMS`);
* 触发判定 `target_for()` —— 携带对应石头(或烈空坐会「画龙点睛」)才返回
  目标形态。

Mega 进化本身是**战斗内**行为(每侧每场一次,战斗结束自动还原):
见 `engine.Battle.mega_evolve` / `engine.Pokemon.mega_evolve_to`。
"""

from __future__ import annotations

import re
from functools import lru_cache

from .dex import get_dex

# 钥石:训练家背包里的重要物品,是 Mega 进化的前提
KEY_STONE = "key-stone"
KEY_STONE_ZH = "钥石"
# 解锁档位(徽章数,与商店分档一致)
KEY_STONE_BADGES = 3
MEGA_STONE_BADGES = 3
# 价格由 world.item_price 按 kind 给出(base:见那里的 kind 表)
KEY_STONE_PRICE_KIND = "key"
MEGA_STONE_PRICE_KIND = "mega"

# 超级烈空坐是特例:不需要 Mega 石,会「画龙点睛」即可
RAYQUAZA = "rayquaza"
RAYQUAZA_MEGA = "rayquazamega"
RAYQUAZA_MOVE = "dragonascent"


def _is_mega(entry: dict) -> bool:
    """形态名里含 "Mega" 才算 Mega 形态(排除超梦 X/Y 之外的其它道具形态)。"""
    return "mega" in str(entry.get("forme") or "").lower()


def _slug(name: str) -> str:
    """英文石头名 → 道具 key:"Charizardite X" → "charizardite-x"。"""
    return re.sub(r"[^0-9a-z]+", "-", str(name or "").lower()).strip("-")


def _suffix(forme: str) -> str:
    """"Mega-X" → "X";用于「喷火龙进化石X」这类命名。"""
    f = str(forme or "").upper()
    for tail in ("-X", "-Y", "-Z"):
        if f.endswith(tail):
            return tail[1]
    return ""


@lru_cache(maxsize=1)
def _table() -> dict:
    """从图鉴构建 Mega 索引(只建一次)。"""
    dex = get_dex()
    stones: dict[str, dict] = {}
    forms: dict[str, dict] = {}
    for key, entry in dex.species.items():
        if not _is_mega(entry):
            continue
        base = str(entry.get("baseSpecies") or "")
        if not base or base not in dex.species:
            continue
        forme = str(entry.get("forme") or "")
        types = list(entry.get("types") or [])
        row = {
            "key": key,
            "base": base,
            "forme": forme,
            "stone": _slug(str(entry.get("requiredItem") or "")),
            "zh": str(entry.get("zh") or key),
            "types": types,
            "num": int(entry.get("num") or 0),
        }
        forms[key] = row
        if not row["stone"]:
            continue
        st = stones.setdefault(row["stone"], {
            "name": str(entry.get("requiredItem") or ""),
            "zh": "",
            "forms": [],
            "bases": [],
            "num": row["num"],
            "type": types[0] if types else "",
        })
        st["forms"].append(key)
        if base not in st["bases"]:
            st["bases"].append(base)
        base_zh = str((dex.species.get(base) or {}).get("zh") or base)
        name_zh = f"{base_zh}进化石{_suffix(forme)}"
        # 共用石可能先遇到带后缀的形态:尽量保留不带后缀的名字
        if not st["zh"] or (not _suffix(forme) and len(name_zh) < len(st["zh"])):
            st["zh"] = name_zh
    return {"stones": stones, "forms": forms}


def all_stones() -> list[str]:
    """全部 Mega 石 key(按图鉴编号排序,展示稳定)。"""
    stones = _table()["stones"]
    return sorted(stones, key=lambda k: (stones[k]["num"], k))


def stone_entry(key: str | None) -> dict | None:
    return _table()["stones"].get(str(key or ""))


def stone_zh(key: str | None) -> str:
    row = stone_entry(key)
    return str(row.get("zh") or "") if row else ""


def form_entry(mega_key: str | None) -> dict | None:
    return _table()["forms"].get(str(mega_key or ""))


def iter_stones():
    """遍历 (石头 key, 中文名, 主属性),给图标按属性配色用。"""
    for key in all_stones():
        row = _table()["stones"][key]
        yield key, str(row["zh"]), str(row["type"])


def build_items() -> dict[str, dict]:
    """构造 Mega 石的背包/携带条目(由 `pw.items` 注入 `_ITEMS`)。"""
    dex = get_dex()
    out: dict[str, dict] = {}
    for key in all_stones():
        row = _table()["stones"][key]
        names = "、".join(
            str((dex.species.get(b) or {}).get("zh") or b) for b in row["bases"]
        )
        out[key] = {
            "zh": str(row["zh"]),
            "kind": MEGA_STONE_PRICE_KIND,
            "desc": f"让{names}在战斗中 Mega 进化。携带后训练家还需持有钥石。",
            "effect": {"mega": list(row["forms"])},
        }
    return out


def _pick_form(cands: list[str], species: str) -> str:
    """同一块石头对应多个形态时,按当前宝可梦挑正确的那一个。"""
    if len(cands) == 1:
        return cands[0]
    dex = get_dex()
    forms = _table()["forms"]
    mon_forme = str((dex.species.get(species) or {}).get("forme") or "").lower()
    if mon_forme:
        for key in cands:
            mf = str(forms[key]["forme"]).lower()
            if mf.startswith(f"{mon_forme}-") or mf == f"mega-{mon_forme}":
                return key
    # 没有形态名(或名字对不上):「Mega」优先;再挑没有独立图鉴 key 的那支
    # (米立龙默认形态是 Curly,数据里没有 tatsugiricurly 这个 key)。
    for key in cands:
        if str(forms[key]["forme"]).lower() == "mega":
            return key
    defaults: list[str] = []
    for key in cands:
        mf = str(forms[key]["forme"]).lower()
        prefix = mf[:-5] if mf.endswith("-mega") else ""
        sibling = f"{species}{prefix}" if prefix else ""
        if not sibling or sibling not in dex.species:
            defaults.append(key)
    return (defaults or cands)[0]


def target_for(species: str, item: str | None = None, moves=()) -> str:
    """这只宝可梦能 Mega 成哪个形态(不能则返回空串)。

    * 普通 Mega:必须携带**对应**的 Mega 石(石头与宝可梦不匹配 → 空串);
    * 超级烈空坐:不需要石头,会「画龙点睛」即可(与原作一致)。
    """
    dex = get_dex()
    forms = _table()["forms"]
    if (
        species == RAYQUAZA
        and RAYQUAZA_MOVE in set(moves or ())
        and RAYQUAZA_MEGA in dex.species
    ):
        return RAYQUAZA_MEGA
    row = stone_entry(item)
    if not row:
        return ""
    entry = dex.species.get(species) or {}
    mon_base = str(entry.get("baseSpecies") or species)
    cands = [k for k in row["forms"] if forms[k]["base"] == mon_base]
    if not cands:
        return ""
    return _pick_form(cands, species)


def stone_for_form(mega_key: str | None) -> str:
    """某个 Mega 形态对应哪块石头(烈空坐没有石头 → 空串)。"""
    row = form_entry(mega_key)
    return str(row.get("stone") or "") if row else ""


def stones_for(caught) -> list[str]:
    """已捕获这些物种(含进化前形态也可)时,商店应上架的 Mega 石。

    只看"石头里列出的原种",玩家拥有一只就能买它对应的石头。
    """
    have = {str(x) for x in (caught or ()) if x}
    if not have:
        return []
    stones = _table()["stones"]
    return [
        key for key in all_stones()
        if any(base in have for base in stones[key]["bases"])
    ]


def mega_report() -> list[str]:
    """数据自检:返回不一致项(空列表 = 健康)。"""
    dex = get_dex()
    stones = _table()["stones"]
    forms = _table()["forms"]
    bad: list[str] = []
    for key, row in forms.items():
        if not row["stone"] and key != RAYQUAZA_MEGA:
            bad.append(f"{key} 没有 Mega 石")
        if row["base"] not in dex.species:
            bad.append(f"{key} 的原种 {row['base']} 不存在")
    for key, row in stones.items():
        bad.extend(
            f"{key} 的目标形态 {mk} 不存在"
            for mk in row["forms"]
            if mk not in dex.species
        )
        if not row["zh"]:
            bad.append(f"{key} 没有中文名")
    return bad
