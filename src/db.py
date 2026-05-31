import sqlite3
from pathlib import Path


DEFAULT_DB_PATH = "bot.db"


class ListingStore:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._init_db()
        self._migrate_from_text_file()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS seen_listings (
                    ref TEXT PRIMARY KEY,
                    user_name TEXT NOT NULL,
                    address TEXT NOT NULL,
                    wg_type TEXT NOT NULL,
                    rental_length_months INTEGER NOT NULL,
                    rental_start TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS contacted_listings (
                    user_name TEXT NOT NULL,
                    address TEXT NOT NULL,
                    ref TEXT,
                    contacted_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (user_name, address)
                );

                CREATE TABLE IF NOT EXISTS failed_listings (
                    ref TEXT PRIMARY KEY,
                    user_name TEXT NOT NULL,
                    address TEXT NOT NULL,
                    fail_count INTEGER NOT NULL DEFAULT 1,
                    last_failed_at TEXT NOT NULL DEFAULT (datetime('now')),
                    last_error TEXT
                );
                """
            )

    def _migrate_from_text_file(self, path: str = "past_listings.txt") -> None:
        file_path = Path(path)
        if not file_path.exists():
            return

        with file_path.open() as handle:
            for line in handle:
                line = line.strip()
                if not line or ": " not in line:
                    continue
                user_name, address = line.split(": ", 1)
                self.mark_contacted(user_name, address, ref="")

        migrated_path = file_path.with_suffix(".txt.migrated")
        file_path.rename(migrated_path)

    @staticmethod
    def read_contacted_pairs(db_path: str = DEFAULT_DB_PATH) -> set[tuple[str, str]]:
        if not Path(db_path).exists():
            return set()

        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT user_name, address FROM contacted_listings"
            ).fetchall()
        return {(row[0], row[1]) for row in rows}

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM seen_listings")
            conn.execute("DELETE FROM contacted_listings")

    def get_unseen_listings(self, listings: dict) -> list[dict]:
        if not listings:
            return []

        refs = [listing["ref"] for listing in listings.values()]
        placeholders = ",".join("?" * len(refs))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT ref FROM seen_listings WHERE ref IN ({placeholders})",
                refs,
            ).fetchall()

        seen_refs = {row["ref"] for row in rows}
        return [
            listing
            for listing in listings.values()
            if listing["ref"] not in seen_refs
        ]

    def get_uncontacted_listings(self, listings: dict) -> list[dict]:
        if not listings:
            return []

        contacted_pairs = self.read_contacted_pairs(self.db_path)
        return [
            listing
            for listing in listings.values()
            if (listing["user_name"], listing["address"]) not in contacted_pairs
        ]

    def mark_seen(self, listings: dict) -> None:
        if not listings:
            return

        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO seen_listings (
                    ref, user_name, address, wg_type, rental_length_months, rental_start
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        listing["ref"],
                        listing["user_name"],
                        listing["address"],
                        listing["wg_type"],
                        listing["rental_length_months"],
                        listing["rental_start"].isoformat(),
                    )
                    for listing in listings.values()
                ],
            )

    def is_contacted(self, user_name: str, address: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM contacted_listings
                WHERE user_name = ? AND address = ?
                """,
                (user_name, address),
            ).fetchone()
        return row is not None

    def mark_contacted(self, user_name: str, address: str, ref: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO contacted_listings (user_name, address, ref)
                VALUES (?, ?, ?)
                """,
                (user_name, address, ref),
            )
            conn.execute("DELETE FROM failed_listings WHERE ref = ?", (ref,))

    def mark_failed(
        self, user_name: str, address: str, ref: str, error: str = ""
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO failed_listings (ref, user_name, address, last_error)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(ref) DO UPDATE SET
                    fail_count = fail_count + 1,
                    last_failed_at = datetime('now'),
                    last_error = excluded.last_error
                """,
                (ref, user_name, address, error[:500]),
            )
