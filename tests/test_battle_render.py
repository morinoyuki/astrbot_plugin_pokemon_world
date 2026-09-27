"""对战画面渲染测试(Pillow)。

运行:
    /mnt/e/astrbot_plugin_life_sim/.venv/bin/python -m pytest tests/test_battle_render.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import io
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


def test_sprites_are_bottom_aligned_on_ground_line():
    """体型/透明边距不同的宝可梦必须**脚底对齐**到同一条落地线。

    官方 96×96 图的底部透明边距从 10px(暴鲤龙)到 32px(地鼠)不等,
    若按画布底对齐,脚底会差 20 多像素。这里直接验证:
    贴图后最下面一行非透明像素必须落在 ground_y - 1。
    """
    from PIL import Image

    from pw import battle_render as br

    def lowest_row(species, ground, factor, bounds, back):
        # 用 alpha 通道判断(精灵图边缘有 alpha=1 的抗锯齿像素,按 RGB 会漏判)
        canvas = Image.new("RGBA", (br.LOGICAL_W, br.LOGICAL_H), (0, 0, 0, 0))
        br._paste_small(canvas, {"species": species}, ground, factor=factor,
                        bounds=bounds, back=back, dim=False)
        rows = [
            y
            for y in range(br.LOGICAL_H)
            if any(canvas.getpixel((x, y))[3] > 16 for x in range(br.LOGICAL_W))
        ]
        assert rows, f"{species} 没有画出任何像素"
        return max(rows)

    for species in ("pikachu", "onix", "diglett", "magikarp", "gyarados", "snorlax"):
        row = lowest_row(species, br.MY_GROUND, br.MY_SCALE, (92, 96), back=True)
        assert row == br.MY_GROUND[1] - 1, (
            f"{species} 我方脚底在第 {row} 行,应为 {br.MY_GROUND[1] - 1}"
        )
        row = lowest_row(species, br.FOE_GROUND, br.FOE_SCALE, (64, 58), back=False)
        assert row == br.FOE_GROUND[1] - 1, (
            f"{species} 敌方脚底在第 {row} 行,应为 {br.FOE_GROUND[1] - 1}"
        )


def test_sprite_scale_differs_by_side_and_keeps_relative_size():
    """两侧缩放不同(我方更近更大),但同一侧内相对体型要保留。"""
    from pw import battle_render as br

    def size(species, factor, bounds, back):
        img = br._load_sprite(br.sprite_for(species, back=back), factor, bounds)
        return (img.width, img.height)

    my_big = size("gyarados", br.MY_SCALE, (92, 96), True)
    my_small = size("diglett", br.MY_SCALE, (92, 96), True)
    assert my_big[1] > my_small[1] * 1.5, f"相对体型丢了:{my_big} vs {my_small}"
    foe_big = size("onix", br.FOE_SCALE, (64, 58), False)
    foe_small = size("pikachu", br.FOE_SCALE, (64, 58), False)
    assert foe_big[1] > foe_small[1], f"相对体型丢了:{foe_big} vs {foe_small}"
    # 同一只:我方比敌方大(近景)
    assert size("gyarados", br.MY_SCALE, (92, 96), True)[0] > size(
        "gyarados", br.FOE_SCALE, (64, 58), False
    )[0]


def test_message_box_never_covers_player_panel():
    """长战报不能让对话框长到我方信息框(血条/经验条)上面。

    对话框高度自适应之后,6 行的上限一度设在 y=88 —— 那已经在我方框(68~110)内部,
    实测长战报会把 HP/EXP 条整块盖住。上限必须是 MY_BOX 底边之下。
    """
    from PIL import Image

    from pw import battle_render as BR

    assert BR.MY_BOX[3] < BR.MSG_TOP_MAX, "对话框上限必须低于我方信息框底边"

    my = {"species": "gyarados", "name": "暴鲤龙", "level": 50, "cur_hp": 150,
          "max_hp": 170}
    foe = {"species": "mewtwo", "name": "超梦", "level": 70, "cur_hp": 200,
           "max_hp": 253}
    long_line = "暴鲤龙 使用了 水流喷射!效果绝佳!对手的 超梦 倒下了!这行刻意写得很长"
    for count in (1, 2, 3, 4, 5, 6, 8):
        data = BR.render_battle(my, foe, [long_line] * count, scale=3)
        im = Image.open(io.BytesIO(data)).convert("RGB")
        # 我方信息框区域内不得出现对话框边框色
        inside = sum(
            1
            for y in range(BR.MY_BOX[1] * 3, BR.MY_BOX[3] * 3)
            for x in range(BR.MY_BOX[0] * 3, BR.MY_BOX[2] * 3)
            if im.getpixel((x, y)) == BR.MSG_FRAME
        )
        assert inside == 0, f"{count} 行时对话框压住了我方信息框({inside} 像素)"


def test_pvp_battle_layout_round_trips():
    """玩家对战画面:两只正面宝可梦面对面(左侧翻转),返回合法 PNG。"""
    from pw import battle_render as BR

    left = {"species": "squirtle", "name": "杰尼龟", "level": 20, "gender": "M",
            "cur_hp": 53, "max_hp": 53}
    right = {"species": "charmander", "name": "小火龙", "level": 20, "gender": "F",
             "cur_hp": 31, "max_hp": 51, "status": "brn"}
    data = BR.render_pvp_battle(
        left, right, ["杰尼龟 使用了 水枪!", "效果拔群!"],
        left_name="小智", right_name="小霞", turn=3, wager=200,
        weather="rain", location="深灰市",
        left_party=[left, {"cur_hp": 0}], right_party=[right], scale=2)
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "没有返回 PNG"
    assert len(data) > 2000, len(data)
