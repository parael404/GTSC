import os
import re
from pathlib import Path

import mysql.connector
from mysql.connector import Error


BASE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = BASE_DIR / "schema.sql"

def _env_first(*names, default=""):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


DB_CONFIG = {
    "host": _env_first("DB_HOST", "MYSQLHOST", default="localhost"),
    "user": _env_first("DB_USER", "MYSQLUSER", default="root"),
    "password": _env_first("DB_PASSWORD", "MYSQLPASSWORD", default=""),
    "port": int(_env_first("DB_PORT", "MYSQLPORT", default="3307")),
    "database": _env_first("DB_NAME", "MYSQLDATABASE", default="codexmbs_db"),
}


def _safe_database_name(name):
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError("DB_NAME may only contain letters, numbers, and underscores.")
    return name


class CursorResult:
    def __init__(self, cursor):
        self.cursor = cursor

    @property
    def lastrowid(self):
        return self.cursor.lastrowid

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


class DBConnection:
    def __init__(self, conn):
        self.conn = conn

    def _normalize_query(self, query):
        return query.replace("?", "%s")

    def execute(self, query, params=()):
        cursor = self.conn.cursor(dictionary=True)
        cursor.execute(self._normalize_query(query), params)
        return CursorResult(cursor)

    def executemany(self, query, seq_of_params):
        cursor = self.conn.cursor()
        cursor.executemany(self._normalize_query(query), seq_of_params)
        return cursor

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


def bootstrap_db():
    server_conn = mysql.connector.connect(
        host=DB_CONFIG["host"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        port=DB_CONFIG["port"],
    )
    server_cursor = server_conn.cursor()
    database_name = _safe_database_name(DB_CONFIG["database"])
    server_cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database_name}`")
    server_conn.commit()
    server_cursor.close()
    server_conn.close()

    db_conn = mysql.connector.connect(**DB_CONFIG)
    db_cursor = db_conn.cursor()

    sql_text = SCHEMA_PATH.read_text(encoding="utf-8")
    statements = [statement.strip() for statement in sql_text.split(";") if statement.strip()]
    for statement in statements:
        try:
            db_cursor.execute(statement)
        except Error as exc:
            if getattr(exc, "errno", None) not in {1060, 1061, 1826}:
                raise

    db_conn.commit()
    db_cursor.close()
    db_conn.close()


def get_db():
    return DBConnection(mysql.connector.connect(**DB_CONFIG))
