"""从 PokeAPI 抓取地区野生分布,补进 `pw/static/locations.json`。

只**新增 / 补池子**,不覆盖已有数据(已有 620 条真实分布是原本就有的)。

```bash
python tools/build_region_locations.py                  # 补 paldea(朱/紫 + 两个 DLC)
python tools/build_region_locations.py --region galar   # 只补伽勒尔(含铠之孤岛/冠之雪原)
python tools/build_region_locations.py --dry-run        # 只统计不写盘
```

数据源是 PokeAPI 的 CSV(比逐区域打 JSON 接口快两个数量级):
`encounters.csv / location_areas.csv / location_area_prose.csv / locations.csv /
regions.csv / versions.csv / version_groups.csv / pokemon_species.csv`。
中文名取 `location_area_prose` 的 `zh-Hans`(南第1区、零区一览…)。

背景:以前为了"让没有获取途径的宝可梦能抓到",临时做了一层
`foreign_pools.json` 叠加(把别地区的宝可梦塞进特殊区域)。这里用**真实分布**
替代它 —— 帕底亚补全后,那一层就可以删掉了。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.request

UA = {"User-Agent": "astrbot-plugin-pokemon-world/1.0 (build script)"}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pw.dex import (  # noqa: E402  与运行时共用同一套“算不算野生的方式/算不算传说”
    FISH_METHODS,
    LAND_METHODS,
    WATER_METHODS,
    get_dex,
)

OUT = os.path.join(ROOT, "pw", "static", "locations.json")
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "pokeapi_csv")
BASE = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/"
FILES = (
    "encounters.csv", "location_areas.csv", "location_area_prose.csv",
    "locations.csv", "regions.csv", "versions.csv", "version_groups.csv",
    "pokemon_species.csv", "location_names.csv",
    "encounter_slots.csv", "encounter_methods.csv",
)
ZH_HANS = 12          # PokeAPI languages.csv: 12 = zh-Hans
# 地区 → 要补的版本组(键与 locations.json 的 pools/versionGroups 对齐)
REGION_GROUPS = {
    "paldea": ("scarlet-violet", "the-teal-mask", "the-indigo-disk"),
    "galar": ("sword-shield", "the-isle-of-armor", "the-crown-tundra"),
}
GROUP_ZH = {
    "scarlet-violet": "朱/紫", "the-teal-mask": "碧之假面",
    "the-indigo-disk": "蓝之圆盘", "sword-shield": "剑/盾",
    "the-isle-of-armor": "铠之孤岛", "the-crown-tundra": "冠之雪原",
}


def _rows(name: str) -> list[dict]:
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name)
    if not os.path.exists(path):
        print(f"  下载 {name} …")
        req = urllib.request.Request(BASE + name, headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        with open(path, "wb") as f:
            f.write(data)
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _int(v, default: int = 0) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


WILD_METHODS = set(LAND_METHODS) | set(WATER_METHODS) | set(FISH_METHODS)


def _is_wild_species(key: str) -> bool:
    """传说 / 幻兽不算野生分布(与自检一致);数据里查不到的一律排除。"""
    sp = get_dex().species.get(key)
    if not sp:
        return False
    for flag in ("legendary", "mythical", "is_legendary", "is_mythical"):
        if sp.get(flag):
            return False
    return True


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def collect(region: str, groups: tuple[str, ...]) -> dict[str, dict]:
    """把该地区各版本组的遭遇整理成 locations.json 的 `pools` 结构。"""
    areas = {_int(r["id"]): r for r in _rows("location_areas.csv")}
    prose: dict[int, str] = {}
    for r in _rows("location_area_prose.csv"):
        if _int(r["local_language_id"]) == ZH_HANS:
            prose[_int(r["location_area_id"])] = r["name"]
    locs = {_int(r["id"]): r for r in _rows("locations.csv")}
    # 地点名的中文(区域名缺中文时,用「地点中文名·第N区」兜底)
    loc_zh: dict[int, str] = {}
    for r in _rows("location_names.csv"):
        if _int(r["local_language_id"]) == ZH_HANS:
            loc_zh[_int(r["location_id"])] = r["name"]
    regions = {_int(r["id"]): r["identifier"] for r in _rows("regions.csv")}
    versions = {_int(r["id"]): r for r in _rows("versions.csv")}
    vgroups = {_int(r["id"]): r["identifier"] for r in _rows("version_groups.csv")}
    species = {_int(r["id"]): r for r in _rows("pokemon_species.csv")}
    # encounters.csv 里**没有**方法列:要走 slot → method 关联表
    emeth = {_int(r["id"]): r["identifier"] for r in _rows("encounter_methods.csv")}
    eslot = {_int(r["id"]): emeth.get(_int(r["encounter_method_id"]), "walk")
             for r in _rows("encounter_slots.csv")}

    wanted_area = {
        aid for aid, a in areas.items()
        if regions.get(_int(locs.get(_int(a["location_id"]), {}).get("region_id"))) == region
    }
    out: dict[str, dict] = {}
    methods_seen: set[str] = set()
    for r in _rows("encounters.csv"):
        aid = _int(r["location_area_id"])
        if aid not in wanted_area:
            continue
        vg = vgroups.get(_int(versions.get(_int(r["version_id"]), {}).get("version_group_id")))
        if vg not in groups:
            continue
        sp = species.get(_int(r["pokemon_id"])) or {}
        key = sp.get("identifier") or ""
        if not key:
            continue
        area_key = areas[aid]["identifier"]
        zh_name = prose.get(aid) or ""
        if not _has_cjk(zh_name):
            # PokeAPI 的 DLC 区域缺中文名 → 用「地点中文名·第N区」合成
            parent = _int(areas[aid]["location_id"])
            base = loc_zh.get(parent) or ""
            if not _has_cjk(base):
                continue                    # 连地点都没中文名就先不收
            idx = _int(areas[aid].get("game_index"), 0)
            zh_name = f"{base}·第{idx}区" if idx else base
        # 地区键统一成 <region>-<area>,与已有命名风格一致(如 galar-route-1)
        node = area_key if area_key.startswith(f"{region}-") else f"{region}-{area_key}"
        slot = out.setdefault(node, {"zh": zh_name, "pools": {}})
        pool = slot["pools"].setdefault(vg, [])
        lo, hi = _int(r["min_level"], 1), _int(r["max_level"], 1)
        method = eslot.get(_int(r["encounter_slot_id"]), "walk")
        if method not in WILD_METHODS:      # 礼物/定点这类不算野生分布
            continue
        if not _is_wild_species(key):
            continue
        methods_seen.add(method)
        hit = next((x for x in pool if x[0] == key and x[3] == method), None)
        if hit:                       # 同一只多次出现 → 等级取并集、权重更高
            hit[1], hit[2] = min(hit[1], lo), max(hit[2], hi)
            hit[4] = min(100, int(hit[4]) + 10)
        else:
            pool.append([key, lo, hi, method, 10])
    return out


API = "https://pokeapi.co/api/v2/"
API_CACHE = os.path.join(os.path.expanduser("~"), ".cache", "pokeapi_api")


def _api(path: str) -> dict:
    """带缓存的实时 API 请求(PokeAPI 的 CSV 导出没有帕底亚,只能走接口)。"""
    os.makedirs(API_CACHE, exist_ok=True)
    slug = path.strip("/").replace("/", "_") + ".json"
    fp = os.path.join(API_CACHE, slug)
    if not os.path.exists(fp):
        req = urllib.request.Request(API + path.strip("/"), headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        with open(fp, "wb") as f:
            f.write(data)
    with open(fp, encoding="utf-8") as f:
        return json.load(f)


def _zh_of(obj: dict) -> str:
    for n in (obj.get("names") or []):
        if ((n.get("language") or {}).get("name")) == "zh-Hans":
            return str(n.get("name") or "")
    return ""


# DLC 区域靠 slug 认(接口里 DLC 的遭遇仍挂在朱/紫两个版本下)
DLC_HINT = {
    "the-teal-mask": ("kitakami", "mossui", "oni-mountain", "apple-hills", "timeless-woods",
                      "crystal-pool", "fellhorn", "infernal", "paradise-barrens", "reveler"),
    "the-indigo-disk": ("blueberry", "terarium", "plasma", "savanna", "polar", "canyon",
                        "coastal-plaza", "torchlit", "charged", "jungle", "volcano"),
}


def collect_api(region: str, groups: tuple[str, ...]) -> dict[str, dict]:
    """走实时接口抓一个地区(用于 CSV 里缺失的帕底亚)。"""
    reg = _api(f"region/{region}")
    out: dict[str, dict] = {}
    for loc_ref in reg.get("locations") or []:
        slug = str(loc_ref.get("name") or "")
        if not slug:
            continue
        try:
            loc = _api(f"location/{slug}")
        except Exception:
            continue
        loc_zh = _zh_of(loc)
        for area_ref in loc.get("areas") or []:
            aslug = str(area_ref.get("name") or "")
            if not aslug:
                continue
            try:
                area = _api(f"location-area/{aslug}")
            except Exception:
                continue
            zh = _zh_of(area) or loc_zh
            if not _has_cjk(zh):
                continue
            node = aslug if aslug.startswith(f"{region}-") else f"{region}-{aslug}"
            slot = out.setdefault(node, {"zh": zh, "pools": {}})
            for pe in area.get("pokemon_encounters") or []:
                key = str(((pe.get("pokemon") or {}).get("name")) or "")
                if not key or not _is_wild_species(key):
                    continue
                for vd in pe.get("version_details") or []:
                    vname = str(((vd.get("version") or {}).get("name")) or "")
                    for vg in _groups_for_version(vname, groups):
                        for ed in vd.get("encounter_details") or []:
                            meth = str(((ed.get("method") or {}).get("name")) or "walk")
                            if meth not in WILD_METHODS:
                                continue
                            lo = _int(ed.get("min_level"), 1)
                            hi = _int(ed.get("max_level"), lo)
                            ch = max(1, min(100, _int(ed.get("chance"), 10)))
                            pool = slot["pools"].setdefault(vg, [])
                            hit = next((x for x in pool if x[0] == key and x[3] == meth), None)
                            if hit:
                                hit[1], hit[2] = min(hit[1], lo), max(hit[2], hi)
                                hit[4] = min(100, max(int(hit[4]), ch))
                            else:
                                pool.append([key, lo, hi, meth, ch])
    # 只留非空的池子
    return {k: v for k, v in out.items() if v["pools"]}


_VERSION_GROUP_HINT = {}


def _groups_for_version(version_name: str, groups: tuple[str, ...]) -> list[str]:
    """版本名 → 该写进哪些版本组的池子(朱/紫的 DLC 从区域 slug 认)。"""
    if not version_name:
        return []
    if not _VERSION_GROUP_HINT:
        for r in _rows("versions.csv"):
            vgid = _int(r["version_group_id"])
            vg = next((v["identifier"] for v in _rows("version_groups.csv")
                       if _int(v["id"]) == vgid), "")
            _VERSION_GROUP_HINT[str(r["identifier"])] = vg
    vg = _VERSION_GROUP_HINT.get(version_name, "")
    return [g for g in groups if g == vg] or ([groups[0]] if vg else [])


def collect_dlc_extra(node_slug: str, groups: tuple[str, ...]) -> list[str]:
    """DLC 区域再额外挂到对应 DLC 版本组(接口里只有一个版本组)。"""
    return [g for g, keys in DLC_HINT.items()
            if g in groups and any(k in node_slug for k in keys)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", action="append", default=None,
                    help="只补指定地区(可多次);默认补 paldea 与 galar")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    targets = args.region or ["paldea", "galar"]

    with open(OUT, encoding="utf-8") as f:
        doc = json.load(f)
    loc = doc["locations"]
    for region in targets:
        groups = REGION_GROUPS.get(region)
        if not groups:
            print(f"⚠️ 不认识的地区:{region}")
            continue
        print(f"== {region} ==")
        found = collect(region, groups)
        if not found:
            print("   CSV 里没有 → 改走实时接口 …")
            found = collect_api(region, groups)
        for node, val in found.items():          # DLC 区域同时挂到 DLC 版本组
            for g in collect_dlc_extra(node, groups):
                val["pools"].setdefault(g, list(next(iter(val["pools"].values()))))
        added = updated = 0
        for key, node in sorted(found.items()):
            cur = loc.get(key)
            if cur is None:
                loc[key] = {"name": node["zh"], "zh": node["zh"], "aliases": [],
                            "region": region, "pools": node["pools"]}
                added += 1
            else:
                cur.setdefault("pools", {})
                for vg, pool in node["pools"].items():
                    if vg not in cur["pools"]:
                        cur["pools"][vg] = pool
                        updated += 1
        for vg in groups:
            doc["versionGroups"].setdefault(
                vg, {"gen": 9 if region == "paldea" else 8, "order": 90,
                     "label": GROUP_ZH.get(vg, vg)})
        n_species = len({e[0] for node in found.values()
                         for pool in node["pools"].values() for e in pool})
        print(f"   区域 {len(found)} 个(新增 {added} / 补池 {updated}),宝可梦 {n_species} 种")
        if not found:
            print("   ⚠️ 一条都没抓到 —— 检查网络或 PokeAPI 字段名")

    if args.dry_run:
        print("--dry-run:不写盘")
        return 0
    doc["meta"]["count"] = len(loc)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    print(f"✅ 写回 {OUT}:locations {len(loc)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
