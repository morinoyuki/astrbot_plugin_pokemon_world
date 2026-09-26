"""对战画面渲染测试(Pillow)。

运行:
    /mnt/e/astrbot_plugin_life_sim/.venv/bin/python -m pytest tests/test_battle_render.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from io import BytesIO

from PIL import Image, ImageStat

from pw import battle_render as BR

SCALE = 3


def _sample():
    my = {
        "species": "charizard",
        "name": "喷火龙",
        "level": 60,
        "cur_hp": 180,
        "max_hp": 200,
        "status": "",
        "gender": "M",
        "exp_pct": 42.0,
    }
    foe = {
        "species": "blastoise",
        "name": "水箭龟",
        "level": 58,
        "cur_hp": 90,
        "max_hp": 190,
        "status": "brn",
        "gender": "F",
        "exp_pct": 0,
    }
    log = [
        "喷火龙 使用了 喷射火焰!",
        "水箭龟 使用了 水炮!",
        "水箭龟 受到了灼伤伤害!",
    ]
    party = [
        {"species": "charizard", "cur_hp": 180, "max_hp": 200},
        {"species": "pikachu", "cur_hp": 0, "max_hp": 120},
        {"species": "snorlax", "cur_hp": 240, "max_hp": 240},
    ]
    return my, foe, log, party


def test_render_battle_png_size_and_header():
    my, foe, log, party = _sample()
    data = BR.render_battle(
        my, foe, log, title="道馆战", weather="rain", terrain="electricterrain",
        location="常磐市", my_party=party, turn=3, scale=SCALE,
    )
    assert data, "渲染返回空字节"
    assert data.startswith(b"\x89PNG"), "不是 PNG 头"
    with Image.open(BytesIO(data)) as im:
        assert im.size == (240 * SCALE, 160 * SCALE)


def test_render_handles_missing_sprite_and_empty_fields():
    my = {"species": "not-a-real-mon"}
    foe = {"species": "also-not-real"}
    data = BR.render_battle(my, foe, [], scale=SCALE)
    assert data.startswith(b"\x89PNG")
    with Image.open(BytesIO(data)) as im:
        assert im.size == (240 * SCALE, 160 * SCALE)


def _player_hp_strip(data):
    """采样玩家血条内部左侧一小段,返回平均 RGB。"""
    bx, by, _bw, _bh = BR.PLAYER_HP_BAR
    x0 = int((bx + 2) * SCALE)
    y0 = int((by + 1) * SCALE)
    x1 = int((bx + 10) * SCALE)
    y1 = int((by + 5) * SCALE)
    with Image.open(BytesIO(data)) as im:
        crop = im.convert("RGB").crop((x0, y0, x1, y1))
        mean = ImageStat.Stat(crop).mean
    return tuple(mean)


def test_hp_bar_color_changes_with_ratio():
    my_full = {"species": "charizard", "name": "喷火龙", "level": 60,
               "cur_hp": 200, "max_hp": 200}
    my_low = {"species": "charizard", "name": "喷火龙", "level": 60,
              "cur_hp": 30, "max_hp": 200}
    foe = {"species": "blastoise", "name": "水箭龟", "level": 60,
           "cur_hp": 150, "max_hp": 150}
    full = _player_hp_strip(BR.render_battle(my_full, foe, ["满血"], scale=SCALE))
    low = _player_hp_strip(BR.render_battle(my_low, foe, ["残血"], scale=SCALE))
    diff = sum(abs(full[i] - low[i]) for i in range(3))
    assert diff > 60, f"血条颜色差异过小: {full} vs {low}"


def test_render_all_weathers_and_status():
    statuses = ["", "brn", "par", "psn", "tox", "slp", "frz"]
    for weather in ["", "sun", "rain", "sand", "snow"]:
        for status in statuses:
            my = {"species": "pikachu", "name": "皮卡丘", "level": 25,
                  "cur_hp": 40, "max_hp": 70, "status": status, "gender": "F"}
            foe = {"species": "bulbasaur", "name": "妙蛙种子", "level": 22,
                   "cur_hp": 20, "max_hp": 65, "status": status, "gender": "M"}
            data = BR.render_battle(my, foe, ["回合"], weather=weather, scale=SCALE)
            assert data.startswith(b"\x89PNG"), f"{weather}/{status} 渲染失败"


def test_battle_render_available():
    assert BR.available() is True


def test_player_uses_back_sprite():
    """我方必须使用背面图(正作里看不到自己的宝可梦正脸)。"""
    from pw import sprites

    assert sprites.back_available_count() > 1200, (
        f"背面图数量过少:{sprites.back_available_count()}"
    )
    path = sprites.back_sprite_path("charizard")
    assert path.endswith("sprites_back/charizard.png"), path
    # 背面图与正面图必须是不同的两张
    assert sprites.back_sprite_path("charizard") != sprites.sprite_path("charizard")
    # 没有背面图时回退到正面图(而不是空)
    assert sprites.back_sprite_path("venusaur") or sprites.sprite_path("venusaur")
    assert sprites.back_sprite_path("no-such-mon") == ""


def test_back_sprite_actually_drawn_for_player(monkeypatch):
    """把"我方背面图"换成正面图后,渲染结果必须变化(证明背面图确实画进去了)。"""
    from pw import battle_render as br
    from pw import sprites

    my = {"species": "charizard", "name": "喷火龙", "level": 60,
          "cur_hp": 180, "max_hp": 200}
    foe = {"species": "blastoise", "name": "水箭龟", "level": 60,
           "cur_hp": 150, "max_hp": 150}
    with_back = br.render_battle(my, foe, ["测试"], scale=SCALE)
    monkeypatch.setattr(br, "back_sprite_path", lambda key, base="": sprites.sprite_path(key))
    with_front = br.render_battle(my, foe, ["测试"], scale=SCALE)
    assert with_back.startswith(b"\x89PNG") and with_front.startswith(b"\x89PNG")
    assert with_back != with_front, "我方精灵图没有随背面/正面切换而变化"
