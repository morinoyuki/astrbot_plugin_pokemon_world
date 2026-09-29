"""帕底亚(朱/紫 + 碧之假面 + 蓝之圆盘)野生分布:从社区整理的 CSV 落地。

PokeAPI **完全没有**朱/紫的遭遇数据(见 CHANGELOG 1.28.1 的勘察结论),所以这里
换成社区维护、按区域分文件的 CSV(每行:`Pokemon,Frequency,MinLevel,MaxLevel`)。

```bash
python tools/build_sv_locations.py --dry-run     # 只看条数,不写盘
python tools/build_sv_locations.py               # 合并进 pw/static/locations.json
```

区域中文名走 Bulbapedia 的 zh langlinks(与 `build_region_locations.py` 同一套缓存),
查不到就用「帕底亚·<英文名>」兜底 —— 地图节点必须有中文名(自检会检查)。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pw.dex import get_dex  # noqa: E402

OUT = os.path.join(ROOT, "pw", "static", "locations.json")
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "sv_csv")
ZHCACHE = os.path.join(os.path.expanduser("~"), ".cache", "pokeapi_zh_names.json")
UA = {"User-Agent": "astrbot-plugin-pokemon-world/1.0 (build script)"}
LIST = ("https://api.github.com/repos/Colek45/ScarletVioletNuzlockeTool/contents/csv_files")
RAW = ("https://raw.githubusercontent.com/Colek45/ScarletVioletNuzlockeTool/main/csv_files/")
BULBA = "https://bulbapedia.bulbagarden.net/w/api.php"
REGION = "paldea"
BASE_GROUP = "scarlet-violet"
# DLC 文件 → 版本组(EP* 是碧之假面,NP* 是蓝之圆盘)
DLC_GROUP = {"EP": "the-teal-mask", "NP": "the-indigo-disk"}
GROUP_ZH = {"scarlet-violet": "朱/紫", "the-teal-mask": "碧之假面",
            "the-indigo-disk": "蓝之圆盘"}
# 少数没有独立地区页的区域:直接给中文名
NAME_FIX = {"EP1": "北上乡·第1区", "EP2": "北上乡·第2区", "EP3": "北上乡·第3区",
            "NP1": "蓝莓学园·第1区", "NP2": "蓝莓学园·第2区", "NP3": "蓝莓学园·第3区",
            "Crater": "帕底亚大坑", "EastSea": "东之海", "WestSea": "西之海",
            "NorthSea": "北之海", "SouthSea": "南之海"}


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def _slug(name: str) -> str:
    """AreaZero → area-zero;AlfornadaCavern → alfornada-cavern。"""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", str(name)).replace("_", "-")
    return s.lower().strip("-")


def _title(name: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(name)).replace("_", " ")
    return " ".join(w.capitalize() if w.islower() else w for w in s.split())


def _bulba_zh(key: str, title: str) -> str:
    """区域官方中文名(缓存与 build_region_locations.py 共用)。"""
    cache: dict[str, str] = {}
    if os.path.exists(ZHCACHE):
        try:
            with open(ZHCACHE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    if key in cache:
        return cache[key]
    zh = ""
    try:
        url = (BULBA + "?action=query&prop=langlinks&lllang=zh&format=json&redirects=1"
               "&titles=" + urllib.parse.quote(title))
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        for pg in ((d.get("query") or {}).get("pages") or {}).values():
            for link in (pg.get("langlinks") or []):
                zh = str(link.get("*") or "")
                break
    except Exception as e:
        print(f"   ⚠️ Bulbapedia「{title}」失败:{e}")
    cache[key] = zh
    os.makedirs(os.path.dirname(ZHCACHE), exist_ok=True)
    with open(ZHCACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    return zh


def _fetch(name: str) -> str:
    os.makedirs(CACHE, exist_ok=True)
    fp = os.path.join(CACHE, name)
    if not os.path.exists(fp):
        req = urllib.request.Request(RAW + urllib.parse.quote(name), headers=UA)
        with urllib.request.urlopen(req, timeout=90) as r:
            data = r.read()
        with open(fp, "wb") as f:
            f.write(data)
    with open(fp, encoding="utf-8") as f:
        return f.read()


def _files() -> list[str]:
    with urllib.request.urlopen(urllib.request.Request(LIST, headers=UA), timeout=90) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return sorted(x["name"] for x in d if str(x.get("name", "")).endswith(".csv"))


def _is_wild(key: str) -> bool:
    sp = get_dex().species.get(key) or {}
    if not sp:
        return False
    return not any(sp.get(f) for f in ("legendary", "mythical", "is_legendary", "is_mythical"))


def collect() -> dict[str, dict]:
    dex = get_dex()
    out: dict[str, dict] = {}
    skipped: list[str] = []
    for fname in _files():
        stem = fname[:-4]
        if stem == "SaveData":
            continue
        group = BASE_GROUP
        for pre, g in DLC_GROUP.items():
            if stem.startswith(pre):
                group = g
        pool: list[list] = []
        names = 0
        for row in csv.DictReader(io.StringIO(_fetch(fname))):
            name = str(row.get("Pokemon") or "").strip()
            if not name:
                continue
            try:
                lo = int(str(row.get("MinLevel") or "").strip())
                hi = int(str(row.get("MaxLevel") or lo).strip())
            except ValueError:
                continue                     # 御三家礼物那几行没有等级 → 跳过
            hit = dex.resolve_species(name)          # → (key, entry) | None
            key = str(hit[0]) if hit else ""
            if not key or not _is_wild(key):
                # 礼物 / 交换 / 定点:没有等级区间 —— 以前直接丢掉,导致“镇内无池”
                # 的城镇进不了地图(道馆落点解析失败)。按 Lv5 记成 gift 行,
                # 让城镇成为 hub(商店/中心)并给道馆一个落点。
                pool.append([key, 5, 5, "gift", ch])
            ch = max(1, min(100, int(float(str(row.get("Frequency") or 10)))))
            pool.append([key, max(1, lo), max(1, hi), "walk", ch])
            names += 1
        if not pool:
            continue
        zh = NAME_FIX.get(stem) or _bulba_zh(f"sv:{stem}", _title(stem))
        if not _has_cjk(zh):
            zh = f"帕底亚·{_title(stem)}"
        node = f"{REGION}-{_slug(stem)}"
        out[node] = {"zh": zh, "pools": {group: pool}, "_n": names}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    with open(OUT, encoding="utf-8") as f:
        doc = json.load(f)
    loc = doc["locations"]
    found = collect()
    added = updated = species = 0
    for node, val in sorted(found.items()):
        species += len({e[0] for e in val["pools"][BASE_GROUP]}) if BASE_GROUP in val["pools"] else \
            len({e[0] for pool in val["pools"].values() for e in pool})
        cur = loc.get(node)
        if cur is None:
            loc[node] = {"name": val["zh"], "zh": val["zh"], "aliases": [],
                         "region": REGION, "pools": val["pools"]}
            added += 1
        else:
            for g, pool in val["pools"].items():
                cur.setdefault("pools", {})
                if g not in cur["pools"]:
                    cur["pools"][g] = pool
                    updated += 1
    for g, zh in GROUP_ZH.items():
        doc["versionGroups"].setdefault(g, {"gen": 9, "order": 95, "label": zh})
    print(f"== {REGION} ==\n   区域 {len(found)} 个(新增 {added} / 补池 {updated}),宝可梦 {species} 种")
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
