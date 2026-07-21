import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data_profile import (  # noqa: E402
    DataProfileError,
    UNKNOWN_PROFILE,
    inspect_runtime_data,
    load_data_profile,
)


class DataProfileTest(unittest.TestCase):
    @staticmethod
    def _write_runtime_fixture(root: Path, *, database_profile: str = "demo", poi_count: int = 1):
        manifest = root / "manifest.json"
        payload = {
            "profile": "demo",
            "classification": "synthetic",
            "public_reproducible": True,
            "sources": {"pois": "fixture", "ugc": "fixture", "routes": "estimated"},
            "counts": {"pois": 1, "ugc_aspects": 1, "routes": 1},
            "limitations": ["not live"],
        }
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        database = root / "runtime.db"
        with sqlite3.connect(database) as connection:
            connection.executescript("""
                CREATE TABLE pois(id TEXT);
                CREATE TABLE ugc_aspects(id TEXT);
                CREATE TABLE routes(id TEXT);
                CREATE TABLE dataset_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            connection.executemany(
                "INSERT INTO dataset_metadata(key, value) VALUES (?, ?)",
                {
                    "profile": database_profile,
                    "classification": "synthetic",
                    "public_reproducible": "true",
                    "sources": json.dumps(payload["sources"], sort_keys=True),
                    "limitations": json.dumps(tuple(payload["limitations"])),
                }.items(),
            )
            connection.executemany("INSERT INTO pois VALUES (?)", [(str(i),) for i in range(poi_count)])
            connection.execute("INSERT INTO ugc_aspects VALUES ('ugc-1')")
            connection.execute("INSERT INTO routes VALUES ('route-1')")
        return manifest, database

    def test_missing_manifest_is_unknown_and_not_publicly_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = load_data_profile(Path(tmp) / "missing.json")

        self.assertEqual(profile, UNKNOWN_PROFILE)
        self.assertFalse(profile.public_reproducible)

    def test_demo_manifest_keeps_synthetic_provenance_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps({
                "profile": "demo",
                "classification": "synthetic",
                "public_reproducible": True,
                "sources": {"pois": "deterministic-synthetic-fixtures"},
                "counts": {"pois": 1286},
                "limitations": ["not live availability"],
            }), encoding="utf-8")

            profile = load_data_profile(path)

        self.assertEqual(profile.name, "demo")
        self.assertTrue(profile.public_reproducible)
        self.assertTrue(profile.contains_synthetic_data)
        self.assertEqual(profile.counts["pois"], 1286)

    def test_invalid_manifest_raises_stable_profile_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(DataProfileError):
                load_data_profile(path)

    def test_runtime_audit_requires_manifest_schema_metadata_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, database = self._write_runtime_fixture(Path(tmp))
            audit = inspect_runtime_data(
                manifest_path=manifest,
                database_path=database,
            )

        self.assertTrue(audit.ready)
        self.assertTrue(all(value == "ok" for value in audit.checks.values()))

    def test_runtime_audit_fails_closed_on_metadata_or_count_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, database = self._write_runtime_fixture(
                Path(tmp), database_profile="stale-profile", poi_count=2
            )
            audit = inspect_runtime_data(
                manifest_path=manifest,
                database_path=database,
            )

        self.assertFalse(audit.ready)
        self.assertEqual(audit.checks["sqlite_integrity"], "ok")
        self.assertIn("profile", audit.checks["profile_consistency"])
        self.assertIn("pois:2!=1", audit.checks["row_counts"])

    def test_runtime_audit_rejects_invalid_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, database = self._write_runtime_fixture(root)
            database.write_text("not sqlite", encoding="utf-8")
            audit = inspect_runtime_data(
                manifest_path=manifest,
                database_path=database,
            )

        self.assertFalse(audit.ready)
        self.assertEqual(audit.checks["sqlite_database"], "unreadable")
        self.assertEqual(audit.checks["sqlite_integrity"], "failed")

    def test_bootstrapped_database_persists_profile(self) -> None:
        db_path = ROOT / "bj_pal.db"
        if not db_path.exists():
            self.skipTest("run `make bootstrap-demo` before the data integration suite")

        with sqlite3.connect(db_path) as conn:
            metadata = dict(conn.execute(
                "SELECT key, value FROM dataset_metadata"
            ).fetchall())

        self.assertEqual(metadata["profile"], "demo")
        self.assertEqual(metadata["classification"], "synthetic")


if __name__ == "__main__":
    unittest.main()
