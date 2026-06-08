"""
meshcore-chat database module
Stores and retrieves chat history using SQLite.
"""

import sqlite3
import logging
import time
import os

logger = logging.getLogger("meshcore-chat.db")

DB_PATH     = os.environ.get("MC_DB_PATH", "/opt/meshcore-chat/messages.db")
MSG_HISTORY = int(os.environ.get("MC_MSG_HISTORY", "200"))


def init(path: str = None):
    p = path or DB_PATH
    con = sqlite3.connect(p)
    con.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        INTEGER NOT NULL,
            type      TEXT NOT NULL,
            conv_key  TEXT NOT NULL,
            sender    TEXT NOT NULL,
            text      TEXT NOT NULL,
            self      INTEGER NOT NULL DEFAULT 0,
            channel   INTEGER,
            ch_name   TEXT,
            from_key  TEXT
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_conv ON messages(conv_key, ts)")
    con.commit()
    con.close()
    logger.info(f"Database ready: {p}")


def save(msg: dict, path: str = None):
    p = path or DB_PATH
    try:
        con = sqlite3.connect(p)
        con.execute(
            "INSERT INTO messages "
            "(ts, type, conv_key, sender, text, self, channel, ch_name, from_key) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                msg.get("ts") or int(time.time()),
                msg.get("type", "channel"),
                msg.get("conv_key", ""),
                msg.get("from", ""),
                msg.get("text", ""),
                1 if msg.get("self") else 0,
                msg.get("channel"),
                msg.get("ch_name"),
                msg.get("from_key", ""),
            )
        )
        # Prune: keep only MSG_HISTORY newest per conversation
        con.execute(
            "DELETE FROM messages WHERE conv_key=? AND id NOT IN "
            "(SELECT id FROM messages WHERE conv_key=? ORDER BY ts DESC LIMIT ?)",
            (msg["conv_key"], msg["conv_key"], MSG_HISTORY)
        )
        con.commit()
        con.close()
    except Exception as e:
        logger.error(f"DB save error: {e}")


def get_history(conv_key: str, limit: int = None, path: str = None) -> list:
    p   = path or DB_PATH
    lim = limit or MSG_HISTORY
    try:
        con  = sqlite3.connect(p)
        rows = con.execute(
            "SELECT ts, type, sender, text, self, channel, ch_name, from_key "
            "FROM messages WHERE conv_key=? ORDER BY ts DESC LIMIT ?",
            (conv_key, lim)
        ).fetchall()
        con.close()
        return [
            {
                "ts":       r[0],
                "type":     r[1],
                "from":     r[2],
                "text":     r[3],
                "self":     bool(r[4]),
                "channel":  r[5],
                "ch_name":  r[6],
                "from_key": r[7] or "",
            }
            for r in reversed(rows)  # chronological order
        ]
    except Exception as e:
        logger.error(f"DB get_history error: {e}")
        return []


def get_conversations(path: str = None) -> list:
    """Return all conv_keys that have messages, newest first."""
    p = path or DB_PATH
    try:
        con  = sqlite3.connect(p)
        rows = con.execute(
            "SELECT conv_key, MAX(ts) as last_ts, COUNT(*) as cnt "
            "FROM messages GROUP BY conv_key ORDER BY last_ts DESC"
        ).fetchall()
        con.close()
        return [{"conv_key": r[0], "last_ts": r[1], "count": r[2]} for r in rows]
    except Exception as e:
        logger.error(f"DB get_conversations error: {e}")
        return []
