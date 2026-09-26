"""SQLite 存档后端测试(玩家数据 + 世界状态 + 旧 JSON 导入)。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from pw.player import TrainerStore  # noqa: E402
from pw.sqlite_store import SqliteBackend  # noqa: E402
from pw.worldstate import WorldStore  # noqa: E402


def _backend(tmp: str) -> SqliteBackend:
    return SqliteBackend(tmp)


def test_trainer_round_trip_and_persistence():
    with tempfile.TemporaryDirectory() as tmp:
        db = _backend(tmp)
        data = {
            "name": "小智",
            "money": 3000,
            "bag": {"poke-ball": 5, "potion": 2},
            "party": [{"species": "pikachu", "level": 25, "nickname": "皮卡"}],
        }
        db.save_trainer("g1", "u1", data)
        assert db.load_trainer("g1", "u1") == data
        assert db.trainer_exists("g1", "u1")
        assert db.load_trainer("g1", "nobody") is None
        db.close()

        # 重新打开(新连接)后数据仍在
        db2 = _backend(tmp)
        assert db2.load_trainer("g1", "u1") == data
        db2.close()


def test_trainer_store_default_backend_branches():
    """TrainerStore 传 backend 时走 SQLite,不传时仍是 JSON(向后兼容)。"""
    with tempfile.TemporaryDirectory() as tmp:
        db = _backend(tmp)
        sqlite_store = TrainerStore(tmp, backend=db)
        json_store = TrainerStore(tmp)
        sqlite_store.save("g1", "u1", {"name": "SQLite 玩家"})
        json_store.save("g1", "u2", {"name": "JSON 玩家"})

        assert sqlite_store.load("g1", "u1")["name"] == "SQLite 玩家"
        assert json_store.load("g1", "u2")["name"] == "JSON 玩家"
        # SQLite 模式下磁盘上不应该出现 u1.json
        assert not os.path.exists(
            os.path.join(tmp, "pokemon_world", "g1", "u1.json")
        )
        # 两个后端互不可见(各自独立)
        assert sqlite_store.load("g1", "u2") is None
        assert json_store.load("g1", "u1") is None
        assert sqlite_store.list_players("g1") == ["u1"]
        assert json_store.list_players("g1") == ["u2"]


def test_world_store_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        db = _backend(tmp)
        store = WorldStore(tmp, backend=db)
        store.save("g1", {"day": 7, "events": [{"kind": "rocket"}], "locks": {}})
        assert store.load("g1")["day"] == 7
        assert store.list_scopes() == ["g1"]
        assert store.load("g2") == {}
        assert store.delete("g1") is True
        assert store.list_scopes() == []


def test_delete_scope_counts_only_players():
    with tempfile.TemporaryDirectory() as tmp:
        db = _backend(tmp)
        trainers = TrainerStore(tmp, backend=db)
        worlds = WorldStore(tmp, backend=db)
        trainers.save("g1", "u1", {"name": "A"})
        trainers.save("g1", "u2", {"name": "B"})
        worlds.save("g1", {"day": 1})
        assert trainers.delete_scope("g1") == 2, "世界存档不该被算成玩家存档"
        assert trainers.list_players("g1") == []
        assert worlds.load("g1") == {}


def test_corrupt_row_is_treated_as_missing_and_backed_up():
    with tempfile.TemporaryDirectory() as tmp:
        db = _backend(tmp)
        db.save_trainer("g1", "u1", {"x": 1})
        # 直接把 data 列写坏,模拟磁盘/程序异常导致的坏数据
        db._write(
            "UPDATE trainers SET data=? WHERE scope=? AND uid=?",
            ("{这不是 JSON", "g1", "u1"),
        )
        assert db.load_trainer("g1", "u1") is None, "坏数据必须按'没有存档'处理"
        assert db.trainer_exists("g1", "u1") is True, "行还在(exists 看的是存在性)"
        bak = db.backup_corrupt_trainer("g1", "u1", "test")
        assert bak.endswith(".bak")
        assert db.trainer_exists("g1", "u1") is False, "备份后应允许重新开始"
        rows = db._rows("SELECT COUNT(*) AS n FROM backups WHERE scope='g1'")
        assert int(rows[0]["n"]) == 1, "坏存档要被保留在备份表里"


def test_import_legacy_json_once():
    """旧 JSON 存档自动导入,且只导入一次(改名 *.imported 保留)。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "pokemon_world")
        os.makedirs(os.path.join(root, "g1"))
        with open(os.path.join(root, "g1", "u1.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "旧玩家", "money": 999}, fh, ensure_ascii=False)
        with open(os.path.join(root, "g1", "_world.json"), "w", encoding="utf-8") as fh:
            json.dump({"day": 5}, fh)

        db = _backend(tmp)
        stat = db.import_legacy()
        assert stat == {"trainers": 1, "worlds": 1}
        assert db.load_trainer("g1", "u1")["name"] == "旧玩家"
        assert db.load_world("g1")["day"] == 5
        # 旧文件改名保留,而不是删除
        assert os.path.exists(os.path.join(root, "g1", "u1.json.imported"))
        assert not os.path.exists(os.path.join(root, "g1", "u1.json"))
        assert os.path.exists(os.path.join(root, "g1", "_world.json.imported"))

        # 幂等:再导入不会覆盖库里的新数据
        db.save_trainer("g1", "u1", {"name": "新数据"})
        stat2 = db.import_legacy()
        assert stat2 == {"trainers": 0, "worlds": 0}
        assert db.load_trainer("g1", "u1")["name"] == "新数据"


def test_import_legacy_skips_broken_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "pokemon_world", "g1")
        os.makedirs(root)
        with open(os.path.join(root, "bad.json"), "w", encoding="utf-8") as fh:
            fh.write("{坏掉的")
        with open(os.path.join(root, "good.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "好的"}, fh)
        db = _backend(tmp)
        stat = db.import_legacy()          # 不能抛异常
        assert stat["trainers"] == 1
        assert db.load_trainer("g1", "good")["name"] == "好的"
        assert db.load_trainer("g1", "bad") is None


def test_concurrent_writes_do_not_lose_data():
    """多线程写入不能丢更新(每线程写各自的玩家)。"""
    with tempfile.TemporaryDirectory() as tmp:
        db = _backend(tmp)
        errors: list[Exception] = []

        def worker(idx: int):
            try:
                for i in range(20):
                    db.save_trainer("g1", f"u{idx}", {"name": f"玩家{idx}", "n": i})
                    db.load_trainer("g1", f"u{idx}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        assert len(db.list_players("g1")) == 6
        assert db.load_trainer("g1", "u3")["n"] == 19, "最后一次写入要生效"


def test_large_save_round_trip():
    """盒子塞满 600 只也不能丢数据(JSON 文本存在 data 列里)。"""
    with tempfile.TemporaryDirectory() as tmp:
        db = _backend(tmp)
        box = [
            {"species": f"mon-{i}", "level": (i % 100) + 1, "moves": ["a", "b", "c", "d"]}
            for i in range(600)
        ]
        db.save_trainer("g1", "u1", {"name": "收藏家", "box": box})
        got = db.load_trainer("g1", "u1")
        assert len(got["box"]) == 600
        assert got["box"][599]["species"] == "mon-599"


def test_plugin_uses_sqlite_by_default():
    """插件与测试宿主默认都用 SQLite 后端(配置 storage=json 时才退回文件)。"""
    sys.path.insert(0, os.path.join(_ROOT, "tests"))
    from test_commands import _Cmd

    with tempfile.TemporaryDirectory() as tmp:
        p = _Cmd(tmp)
        assert p._db is not None, "默认应当启用 SQLite"
        assert p.trainers._db is not None
        legacy = _Cmd(tmp, storage="json")
        assert legacy._db is None, "storage=json 时应退回文件存储"
