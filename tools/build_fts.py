"""Построение FTS5 (trigram) индекса по локальному зеркалу db_records.

SQLite FTS5 с токенизатором 'trigram' даёт подстроковый поиск (LIKE %q%)
за миллисекунды по сотням тысяч записей, вместо полного скана в Python,
из-за которого локальный fallback-поиск занимал ~30 секунд.

Индекс триграмм НЕ чувствителен к регистру только для ASCII, поэтому мы
храним normalised (нижний регистр) копии полей в самой FTS-таблице.

Запуск:
    .venv\\Scripts\\python.exe tools\\build_fts.py
"""
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "bot.db"

FTS_TABLE = "db_records_fts"
COLUMNS = ("text_lower", "author_lower", "source_lower", "section_lower")

_SCHEMA = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
    {COLUMNS[0]}, {COLUMNS[1]}, {COLUMNS[2]}, {COLUMNS[3]},
    record_id UNINDEXED,
    tokenize = 'trigram'
);
"""


def build() -> None:
    t0 = time.time()
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_SCHEMA)

    # Полная перестройка индекса (идемпотентно, чтобы не дублировать).
    conn.execute(f"DELETE FROM {FTS_TABLE}")
    conn.commit()

    # Копируем нижним регистром; record_id = rowid старой таблицы.
    # Порциями, чтобы не держать 964K строк в памяти сразу.
    CHUNK = 50000
    cur = conn.execute("SELECT id, text, author, source, section FROM db_records")
    total = 0
    while True:
        rows = cur.fetchmany(CHUNK)
        if not rows:
            break
        conn.executemany(
            f"INSERT INTO {FTS_TABLE} (text_lower, author_lower, source_lower, "
            f"section_lower, record_id) VALUES (?,?,?,?,?)",
            [(_s(r[1]), _s(r[2]), _s(r[3]), _s(r[4]), r[0]) for r in rows],
        )
        total += len(rows)
        conn.commit()
        if total % 250000 == 0:
            print(f"  indexed {total:,} ...".replace(",", " "))

    conn.commit()
    print(f"FTS index ready: {total:,} entries в {time.time()-t0:.1f}s"
          .replace(",", " "))
    conn.close()


def _s(v) -> str:
    return " ".join((v or "").lower().split())


if __name__ == "__main__":
    build()