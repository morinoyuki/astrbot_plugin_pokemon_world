"""SQLite 存档后端(玩家数据 + 世界状态)。

为什么用 SQLite
--------------
旧实现是「每人一个 JSON 文件」:每次保存都要全量序列化 + 临时文件 + rename。
文件多、目录深(600+ 玩家就是 600+ 文件),而且 `_world.json` 在多进程/多任务下
只能靠"原子改名"避免半个文件,做不到真正的行级并发安全。

SQLite(WAL 模式 + busy_timeout)提供:
  · 事务级原子性(不再需要临时文件 + rename);
  · 单文件、单目录,备份/迁移简单;
  · 读写并发(WAL 下读不阻塞写),并用一把进程内锁串行化本进程的写;
  · 行级更新,存档大小不再影响"保存代价"。

数据仍是 JSON 文本存在 ``data`` 列里 —— 这样旧存档可以原样导入,
字段语义、损坏判定(读不出 JSON 就当作损坏)与旧实现完全一致,
不需要迁移期改任何业务代码。

兼容
----
`storage` 配置默认 ``sqlite``;设为 ``json`` 时仍走旧的按文件存储。
首次打开数据库时,若某 scope 在库里没有记录而磁盘上有旧的 JSON 存档,
会自动导入(**只导入一次**,并把旧文件改名为 ``*.imported`` 保留,不删除用户数据)。
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import time

from astrbot.api import logger

from .util import safe_name

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trainers (
    scope       TEXT NOT NULL,
    uid         TEXT NOT NULL,
    data        TEXT NOT NULL,
    updated_day INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, uid)
);
CREATE TABLE IF NOT EXISTS worlds (
    scope      TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at REAL NOT NULL DEFAULT 0
);
-- 损坏存档的备份区(替代旧实现的 *.json.bak 文件)
CREATE TABLE IF NOT EXISTS backups (
    scope   TEXT NOT NULL,
    uid     TEXT NOT NULL,
    reason  TEXT NOT NULL DEFAULT '',
    data    TEXT NOT NULL,
    saved_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, uid, saved_at)
);
CREATE INDEX IF NOT EXISTS idx_trainers_scope ON trainers(scope);
"""


class SqliteBackend:
    """一个 SQLite 连接 + 一把写锁。连接以线程方式共享(见 check_same_thread)。"""

    def __init__(self, data_dir: str):
        root = os.path.join(data_dir, "pokemon_world")
        os.makedirs(root, exist_ok=True)
        self.root = root
        self.path = os.path.join(root, "pokemon_world.db")
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path, timeout=10.0, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            # WAL:读写并发;busy_timeout:多进程时等锁而不是立刻报 database is locked
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=8000")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ── 低层工具 ──
    def _write(self, sql: str, params: tuple) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def _rows(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def close(self) -> None:
        with self._lock, contextlib.suppress(sqlite3.Error):  # 已经关了/被回收
            self._conn.close()

    # ── 玩家存档 ──
    def load_trainer(self, scope: str, uid: str) -> dict | None:
        rows = self._rows(
            "SELECT data FROM trainers WHERE scope=? AND uid=?", (scope, uid)
        )
        if not rows:
            return None
        return _decode(rows[0]["data"], f"{scope}/{uid}")

    def save_trainer(self, scope: str, uid: str, data: dict, day: int = 0) -> None:
        self._write(
            "INSERT INTO trainers(scope, uid, data, updated_day, updated_at)"
            " VALUES(?,?,?,?,?)"
            " ON CONFLICT(scope, uid) DO UPDATE SET"
            " data=excluded.data, updated_day=excluded.updated_day,"
            " updated_at=excluded.updated_at",
            (scope, uid, _encode(data), int(day or 0), time.time()),
        )

    def trainer_exists(self, scope: str, uid: str) -> bool:
        return bool(
            self._rows(
                "SELECT 1 FROM trainers WHERE scope=? AND uid=? LIMIT 1", (scope, uid)
            )
        )

    def delete_trainer(self, scope: str, uid: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM trainers WHERE scope=? AND uid=?", (scope, uid)
            )
            self._conn.commit()
        return cur.rowcount > 0

    def list_players(self, scope: str) -> list[str]:
        return [
            r["uid"]
            for r in self._rows(
                "SELECT uid FROM trainers WHERE scope=? ORDER BY uid", (scope,)
            )
        ]

    def count_players(self, scope: str) -> int:
        rows = self._rows("SELECT COUNT(*) AS n FROM trainers WHERE scope=?", (scope,))
        return int(rows[0]["n"]) if rows else 0

    def backup_corrupt_trainer(self, scope: str, uid: str, reason: str = "") -> str:
        """把读不出来的存档挪进备份表,返回备份标识(旧实现是 .bak 文件名)。

        SQLite 下没有"文件"可改名,所以复制到 backups 表后删除原行;
        备份标识形如 ``u1.bak``,与旧提示文案兼容。
        """
        rows = self._rows("SELECT data FROM trainers WHERE scope=? AND uid=?", (scope, uid))
        if not rows:
            return ""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO backups(scope, uid, reason, data, saved_at)"
                " VALUES(?,?,?,?,?)",
                (scope, uid, reason, rows[0]["data"], time.time()),
            )
            self._conn.execute(
                "DELETE FROM trainers WHERE scope=? AND uid=?", (scope, uid)
            )
            self._conn.commit()
        return f"{safe_name(uid)}.bak"

    def delete_scope(self, scope: str) -> int:
        n = self.count_players(scope)
        with self._lock:
            self._conn.execute("DELETE FROM trainers WHERE scope=?", (scope,))
            self._conn.execute("DELETE FROM worlds WHERE scope=?", (scope,))
            self._conn.commit()
        return n

    # ── 世界状态 ──
    def load_world(self, scope: str) -> dict:
        rows = self._rows("SELECT data FROM worlds WHERE scope=?", (scope,))
        if not rows:
            return {}
        data = _decode(rows[0]["data"], f"{scope}/world")
        return data or {}

    def save_world(self, scope: str, data: dict) -> None:
        self._write(
            "INSERT INTO worlds(scope, data, updated_at) VALUES(?,?,?)"
            " ON CONFLICT(scope) DO UPDATE SET data=excluded.data,"
            " updated_at=excluded.updated_at",
            (scope, _encode(data), time.time()),
        )

    def delete_world(self, scope: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM worlds WHERE scope=?", (scope,))
            self._conn.commit()
        return cur.rowcount > 0

    def list_scopes(self) -> list[str]:
        return [
            r["scope"]
            for r in self._rows("SELECT scope FROM worlds ORDER BY scope")
        ]

    # ── 旧 JSON 存档导入(只做一次) ──
    def import_legacy(self) -> dict:
        """把磁盘上的旧 JSON 存档导入数据库;返回 {"trainers": n, "worlds": n}。

        幂等:只在"库里还没有该 scope/uid 的记录"时导入;导入后把旧文件改名为
        `*.imported` 保留(不删用户数据)。任何异常都不能影响启动。
        """
        stat = {"trainers": 0, "worlds": 0}
        try:
            scopes = [
                d
                for d in os.listdir(self.root)
                if os.path.isdir(os.path.join(self.root, d))
            ]
        except OSError:
            return stat
        for scope in scopes:
            d = os.path.join(self.root, scope)
            try:
                files = [f for f in os.listdir(d) if f.endswith(".json")]
            except OSError:
                continue
            for fname in files:
                path = os.path.join(d, fname)
                try:
                    with open(path, encoding="utf-8") as fh:
                        data = json.load(fh)
                except (OSError, ValueError):
                    logger.warning("宝可梦世界: 旧存档 %s 读不出来,跳过导入", path)
                    continue
                if not isinstance(data, dict) or not data:
                    continue
                if fname == "_world.json":
                    if not self._rows(
                        "SELECT 1 FROM worlds WHERE scope=? LIMIT 1", (scope,)
                    ):
                        self.save_world(scope, data)
                        stat["worlds"] += 1
                        _mark_imported(path)
                    continue
                uid = fname[:-5]
                if not self.trainer_exists(scope, uid):
                    self.save_trainer(scope, uid, data)
                    stat["trainers"] += 1
                    _mark_imported(path)
        if stat["trainers"] or stat["worlds"]:
            logger.info(
                "宝可梦世界: 已从旧 JSON 存档导入 %d 名训练家 / %d 个世界",
                stat["trainers"], stat["worlds"],
            )
        return stat


def _mark_imported(path: str) -> None:
    """把已导入的旧存档改名保留(失败不影响运行)。"""
    try:
        target = path + ".imported"
        if os.path.exists(target):
            os.remove(target)
        os.rename(path, target)
    except OSError:
        logger.debug("宝可梦世界: 旧存档改名失败(不影响运行): %s", path)


def _encode(data: dict) -> str:
    """存档 → JSON 文本。ensure_ascii=False 让中文可读,也省空间。"""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _decode(raw, what: str) -> dict | None:
    """JSON 文本 → 存档;损坏/非对象一律返回 None(与旧实现语义一致)。"""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("宝可梦世界: %s 的存档内容损坏,按没有存档处理", what)
        return None
    return data if isinstance(data, dict) and data else None
