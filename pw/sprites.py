"""宝可梦缩略图(头像用)。

图片在 ``static/sprites/<species_key>.png``(96x96 官方像素图,构建脚本:
``tools/build_pokemon_sprites.py``)。运行时只读本地文件、不联网。

用途:聊天卡片渲染时,若说话人名能解析成宝可梦,就默认用该缩略图当头像
(优先于全局默认头像、低于用户通过 /头像 设置的角色专属头像)。
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

from .dex import get_dex

SPRITES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "sprites")
# 背面图(战斗界面我方用):tools/build_back_sprites.py 生成
BACK_SPRITES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "static", "sprites_back"
)

# 说话人标签:<d name="角色名" ...>
_SPEAKER_RE = re.compile(r'name\s*=\s*"([^"]*)"', re.IGNORECASE)
_WS_RE = re.compile(r"[\s\u3000]")


@lru_cache(maxsize=1)
def _available() -> frozenset[str]:
    try:
        return frozenset(f[:-4] for f in os.listdir(SPRITES_DIR) if f.endswith(".png"))
    except OSError:
        return frozenset()


def available_count() -> int:
    return len(_available())


def _base_of(species_key: str) -> str:
    """取该形态的基础物种 key(拿不到就返回空串)。"""
    try:
        from .dex import get_dex

        entry = get_dex().species.get(species_key) or {}
        for field in ("baseSpecies", "base_species"):
            base = str(entry.get(field) or "")
            if base and base in _available():
                return base
    except Exception:  # 拿不到就当作没有基础形态
        pass
    return ""


def sprite_path(species_key: str, base_species: str = "") -> str:
    """宝可梦 key → 本地缩略图路径(没有则空串)。

    缺图时**退到基础形态**(与背面图一致):数据源里极少数形态没有官方像素图
    (例如 PokeAPI 完全没有超级基格尔德,所有候选 URL 都是 404),
    旧实现直接返回空串 → 界面里那一只**完全没有图**;背面却走 baseSpecies 回退,
    前后不一致。
    """
    if not species_key:
        return ""
    candidates = [species_key]
    if base_species:
        candidates.append(base_species)
    base = _base_of(species_key)
    if base:
        candidates.append(base)
    for key in candidates:
        if key in _available():
            return os.path.join(SPRITES_DIR, f"{key}.png")
    return ""


@lru_cache(maxsize=1)
def _available_back() -> frozenset[str]:
    try:
        return frozenset(
            f[:-4] for f in os.listdir(BACK_SPRITES_DIR) if f.endswith(".png")
        )
    except OSError:
        return frozenset()


def back_sprite_path(species_key: str, base_species: str = "") -> str:
    """宝可梦 key → 本地**背面**图路径(战斗界面我方用)。

    没有背面图时退回正面图(渲染层会再水平翻转),再没有就返回空串。
    """
    for key in (species_key, base_species):
        if key and key in _available_back():
            return os.path.join(BACK_SPRITES_DIR, f"{key}.png")
    return sprite_path(species_key) or sprite_path(base_species)


def back_available_count() -> int:
    return len(_available_back())


def clean_speaker_name(name: str) -> str:
    n = _WS_RE.sub("", str(name or ""))
    return n.split("(")[0].split("（")[0].strip()


def sprite_for_name(name: str) -> str:
    """说话人名 → 宝可梦缩略图路径;不是宝可梦则空串。"""
    clean = clean_speaker_name(name)
    if not clean:
        return ""
    try:
        dex = get_dex()
        r = dex.resolve_species(clean)
    except Exception:
        return ""
    if not r:
        return ""
    key, entry = r
    path = sprite_path(key)
    if path:
        return path
    base = entry.get("baseSpecies")
    if base:
        b = dex.resolve_species(base)
        if b:
            return sprite_path(b[0])
    return ""


def speaker_names(text: str) -> list[str]:
    """从聊天卡片文本里取出所有 <d name="…"> 的说话人名(去重、保序)。"""
    out: list[str] = []
    seen: set[str] = set()
    for m in _SPEAKER_RE.finditer(text or ""):
        name = clean_speaker_name(m.group(1))
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def pokemon_avatars_for_text(text: str, existing: dict | None = None) -> dict:
    """给文本里出现的宝可梦说话人补上缩略图头像(不覆盖已有)。

    existing: 角色名 → 头像(路径/URL/PIL Image),用户设置的头像优先。
    返回新的映射(含原内容)。
    """
    avatars: dict = dict(existing or {})
    for name in speaker_names(text):
        if name in avatars:
            continue
        path = sprite_for_name(name)
        if path:
            avatars[name] = path
    return avatars


__all__ = [
    "BACK_SPRITES_DIR",
    "SPRITES_DIR",
    "available_count",
    "back_available_count",
    "back_sprite_path",
    "clean_speaker_name",
    "pokemon_avatars_for_text",
    "speaker_names",
    "sprite_for_name",
    "sprite_path",
]
