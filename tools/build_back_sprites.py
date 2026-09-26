"""下载宝可梦**背面**像素图到 `pw/static/sprites_back/`。

战斗界面里我方宝可梦必须是背面(和正作一致),而 `sprites/` 只有正面图,
所以这里从 PokeAPI/sprites 仓库的 `sprites/pokemon/back/<id>.png` 再抓一份。

映射直接复用 `pw/static/sprites/index.json`(物种 key → PokeAPI pokemon id,
由 tools/build_pokemon_sprites.py 生成)。缺失的回退顺序:
    自身 back → baseSpecies 的 back → 自身 front(渲染时再水平翻转)
运行一次即可,运行时不联网:
    python tools/build_back_sprites.py
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
FRONT_DIR = os.path.join(STATIC, "sprites")
OUT_DIR = os.path.join(STATIC, "sprites_back")
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
    os.makedirs(OUT_DIR, exist_ok=True)

    def work(key: str) -> tuple[str, str]:
        path = os.path.join(OUT_DIR, f"{key}.png")
        if os.path.exists(path):
            return key, "cached"
        pid = index.get(key)
        if pid:
            data = fetch(f"{BASE}back/{int(pid)}.png")
            if data:
                with open(path, "wb") as f:
                    f.write(data)
                return key, "ok"
        base = (species.get(key) or {}).get("baseSpecies") or ""
        if base in index:
            data = fetch(f"{BASE}back/{int(index[base])}.png")
            if data:
                with open(path, "wb") as f:
                    f.write(data)
                return key, "fallback_base"
        front = os.path.join(FRONT_DIR, f"{key}.png")
        if os.path.exists(front):
            with open(front, "rb") as f:
                data = f.read()
            with open(path, "wb") as f:
                f.write(data)
            return key, "fallback_front"
        return key, "missing"

    stats: dict[str, int] = {}
    missing: list[str] = []
    keys = sorted(k for k in index if k in species)
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        for key, status in pool.map(work, keys):
            stats[status] = stats.get(status, 0) + 1
            if status == "missing":
                missing.append(key)

    total = len([f for f in os.listdir(OUT_DIR) if f.endswith(".png")])
    return {"stats": stats, "total": total, "missing_keys": missing}


def main() -> int:
    result = build()
    print("背面精灵图统计:", result["stats"])
    print("输出目录文件数:", result["total"])
    if result["missing_keys"]:
        print(f"完全缺失 {len(result['missing_keys'])} 个:", result["missing_keys"][:20])
    return 0 if result["total"] > 1200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
