"""形态名的中文标签 + 事件限定形态不该出现在野外。

实测反馈:道路训练家掏出一只「皮卡丘（Cosplay）」并用「电光!」打人 ——
名字是 PokeAPI 的英文形态标签直译(皮卡丘（Cosplay）),又怪又出戏,
而且换装皮卡丘本来就是 ORAS 活动限定,根本不该进杂兵队伍。

两件事分开锁住:
1. 所有形态标签都有中文名(`FORM_LABEL_ZH`),数据更新带来的新标签必须补翻译;
2. 皮卡丘/伊布的活动形态(换装、戴帽、Let's Go 伙伴)不参与野生/杂兵抽取。
"""

from __future__ import annotations

import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pw.dex import (  # noqa: E402
    FORM_LABEL_ZH,
    SPECIES_FORM_ZH,
    get_dex,
    localize_form_zh,
    untranslated_forme_keys,
)
from pw.encounter import is_wild_candidate  # noqa: E402

_SPECIES_JSON = os.path.join(_ROOT, "pw", "static", "species.json")
_FORM_RE = re.compile(r"（([^（）]*)）")


def test_every_forme_label_has_a_chinese_translation():
    """species.json 里的每个形态标签都要能在 FORM_LABEL_ZH 里查到。

    数据是 PokeAPI 生成的:上游新增形态时,这里会红,提醒补一行翻译。
    """
    with open(_SPECIES_JSON, encoding="utf-8") as f:
        raw = json.load(f)
    labels = set()
    for key, s in raw.items():
        if key in SPECIES_FORM_ZH:
            continue          # 同名标签在个别物种上有专属翻译(戴帽皮卡丘)
        m = _FORM_RE.search(str(s.get("zh") or ""))
        if m:
            labels.add(m.group(1))
    missing = sorted(labels - set(FORM_LABEL_ZH))
    assert not missing, f"这些形态标签还没有中文名:{missing}"
    assert not untranslated_forme_keys(), untranslated_forme_keys()


def test_common_forme_names_read_as_chinese():
    """玩家真会看到的形态名:地区形态 / Mega / 属性形态 / 活动形态。"""
    dex = get_dex()
    cases = {
        "pikachucosplay": "皮卡丘（换装）",
        "pikachualola": "皮卡丘（阿罗拉帽）",
        "pikachuoriginal": "皮卡丘（初始帽）",
        "ninetalesalola": "九尾（阿罗拉）",
        "arcaninehisui": "风速狗（洗翠）",
        "meowthgalar": "喵喵（伽勒尔）",
        "wooperpaldea": "乌波（帕底亚）",
        "charizardmegax": "喷火龙（Mega-X）",
        "absolmegaz": "阿勃梭鲁（Mega-Z）",
        "alcremiegmax": "霜奶仙（超极巨）",
        "arceusbug": "阿尔宙斯（虫）",
        "rotomwash": "洛托姆（清洗）",
        "kyuremblack": "酋雷姆（暗黑）",
        "eiscuenoice": "冰砌鹅（冻头）",
        "mimikyubusted": "谜拟丘（现形）",
        "ursalunabloodmoon": "月月熊（赫月）",
        "ogerponwellspring": "厄诡椪（水井面具）",
        "deoxysattack": "代欧奇希斯（攻击）",
        "necrozmadawnwings": "奈克洛兹玛（拂晓之翼）",
        "zaciancrowned": "苍响（王）",
    }
    bad = {k: (dex.species[k]["zh"], want) for k, want in cases.items()
           if dex.species[k]["zh"] != want}
    assert not bad, bad
    # 普通物种不该被误伤
    for key in ("pikachu", "charizard", "ninetales"):
        assert "（" not in dex.species[key]["zh"]


def test_forme_label_helper_keeps_unknown_names():
    """没见过的写法原样返回:宁可不翻,也不能把名字弄丢。"""
    assert localize_form_zh("皮卡丘（Cosplay）", "pikachucosplay") == "皮卡丘（换装）"
    assert localize_form_zh("皮卡丘", "pikachu") == "皮卡丘"
    assert localize_form_zh("") == ""
    assert localize_form_zh("未知兽（Mystery）") == "未知兽（Mystery）"


def test_old_and_new_names_both_still_resolve():
    """中文化不能把旧查询弄坏:图鉴页里输入英文形态或存档里的旧名都要能查到。"""
    dex = get_dex()
    for query in ("pikachucosplay", "皮卡丘（换装）", "皮卡丘（Cosplay）", "Pikachu-Cosplay"):
        r = dex.resolve_species(query)
        assert r and r[0] == "pikachucosplay", (query, r)


def test_event_only_pikachu_and_eevee_forms_stay_out_of_wild_pools():
    """换装/戴帽皮卡丘、Let's Go 伙伴形态:既不是野生,也不该进杂兵队伍。"""
    dex = get_dex()
    for key, entry in dex.species.items():
        if str(entry.get("baseSpecies") or "") not in ("pikachu", "eevee"):
            continue
        if key in ("pikachu", "eevee"):
            continue
        assert not is_wild_candidate(entry), f"{key} 还能在野外/杂兵里出现"
    # 正常形态不能被误伤
    for key in ("pikachu", "eevee", "vulpixalola", "raichualola"):
        assert is_wild_candidate(dex.species[key]), key


def test_route_trainers_never_get_cosplay_pikachu():
    """真的按杂兵队伍生成跑一遍:几百个种子都不该冒出活动形态皮卡丘。"""
    import tempfile

    from test_commands import _Cmd, _Event, run_cmd

    from pw.npc import build_route_battle

    dex = get_dex()
    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        run_cmd(p, _Event("/开始 小智 皮卡丘"), p.cmd_start)
        t = p._load(_Event())
        t.data["location"] = "kanto-route-5"
        bad: list[str] = []
        for i in range(400):
            meta = build_route_battle(
                t, {"id": f"npc:{i}", "name": "短裤小子 阿明", "tier": 4,
                    "location": "kanto-route-5"}, day=i)
            for spec in meta.get("team") or []:
                key = str(spec.get("species") or "")
                entry = dex.species.get(key) or {}
                if str(entry.get("baseSpecies") or "") in ("pikachu", "eevee"):
                    bad.append(f"{key} (seed {i})")
        assert meta.get("team"), "队伍生成器没产出队伍(测试本身要修)"
    assert not bad, f"杂兵队伍里出现了活动形态:{sorted(set(bad))[:5]}"
