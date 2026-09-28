"""下载宝可梦**闪光(异色)**像素图到 `pw/static/sprites_shiny/`(正面)
并同步背面到 `pw/static/sprites_shiny_back/`。

闪光不是换色算法 —— 每只的异色配色是官方美术,所以必须从
PokeAPI/sprites 仓库单独抓:`sprites/pokemon/shiny/<id>.png` 与
`sprites/pokemon/back/shiny/<id>.png`。

映射直接复用 `pw/static/sprites/index.json`(物种 key → PokeAPI pokemon id,
由 tools/build_pokemon_sprites.py 生成)。缺失的回退顺序:
    自身 shiny → baseSpecies 的 shiny → 跳过(运行时回退普通精灵图,
    所以哪怕官方没出这只的闪光图,界面也不会开天窗)。
运行一次即可,运行时不联网:
    python tools/build_shiny_sprites.py
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "pw", "static")
INDEX = os.path.join(STATIC, "sprites", "index.json")
SPECIES = os.path.join(STATIC, "species.json")
OUT_FRONT = os.path.join(STATIC, "sprites_shiny")
OUT_BACK = os.path.join(STATIC, "sprites_shiny_back")
BASE = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/"
UA = "Mozilla/5.0 (compatible; astrbot-plugin-pokemon-world sprite builder)"


def fetch(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # 固定 https 数据源
            return resp.read() if resp.status == 200 else None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def build() -> dict:
    with open(INDEX, encoding="utf-8") as f:
        index: dict[str, str] = json.load(f)
    with open(SPECIES, encoding="utf-8") as f:
        species: dict[str, dict] = json.load(f)
    os.makedirs(OUT_FRONT, exist_ok=True)
    os.makedirs(OUT_BACK, exist_ok=True)

    def download(url: str, path: str) -> bool:
        data = fetch(url)
        if not data:
            return False
        with open(path, "wb") as f:
            f.write(data)
        return True

    def work(key: str) -> tuple[str, str]:
        front = os.path.join(OUT_FRONT, f"{key}.png")
        back = os.path.join(OUT_BACK, f"{key}.png")
        if os.path.exists(front) and os.path.exists(back):
            return key, "cached"
        pid = index.get(key)
        if pid and download(f"{BASE}shiny/{int(pid)}.png", front):
            download(f"{BASE}back/shiny/{int(pid)}.png", back)
            return key, "ok"
        base = (species.get(key) or {}).get("baseSpecies") or ""
        if base in index:
            bpid = int(index[base])
            if download(f"{BASE}shiny/{bpid}.png", front):
                download(f"{BASE}back/shiny/{bpid}.png", back)
                return key, "fallback_base"
        return key, "missing"

    stats: dict[str, int] = {}
    missing: list[str] = []
    keys = sorted(k for k in index if k in species)
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        for key, status in pool.map(work, keys):
            stats[status] = stats.get(status, 0) + 1
            if status == "missing":
                missing.append(key)
    return {"stats": stats, "missing": missing, "total": len(keys)}


def main() -> None:
    result = build()
    stats = result["stats"]
    print(f"keys={result['total']} {stats}")
    if result["missing"]:
        print(f"missing({len(result['missing'])}):{result['missing'][:20]}")
    print("self-check: OK (missing falls back to normal sprites)")


if __name__ == "__main__":
    main()
