"""Build and validate the project DuckDB database.

This final pipeline step deliberately lets DuckDB read the CSV directly. The
Python code is responsible for orchestration, transactions and validation;
the persistent database schema remains in ``sql/schema.sql``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_INPUT = PROJECT_ROOT / "data" / "external" / "ev_augmented.csv"
DEFAULT_SUMMARY = (
    PROJECT_ROOT / "data" / "external" / "augmentation_summary.json"
)
DEFAULT_SA4 = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "SA4_2026_AUST_SHP_GDA2020"
    / "SA4_2026_AUST_GDA2020.shp"
)
DEFAULT_SCHEMA = PROJECT_ROOT / "sql" / "schema.sql"
DEFAULT_DATABASE = PROJECT_ROOT / "db" / "ev_chargers.duckdb"


def sql_string(value: Path | str) -> str:
    """Return a safely quoted DuckDB SQL string literal."""
    escaped = str(value).replace("\\", "/").replace("'", "''")
    return f"'{escaped}'"


def sha256_file(path: Path) -> str:
    """Calculate a file hash without loading the entire file into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_file(path: Path, label: str) -> None:
    """Fail early when a required input is absent or empty."""
    if not path.is_file():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"{label} is empty: {path}")


def ensure_spatial(connection: duckdb.DuckDBPyConnection) -> None:
    """Load spatial, installing it only when it is not already available."""
    try:
        connection.execute("LOAD spatial")
    except duckdb.Error:
        connection.execute("INSTALL spatial")
        connection.execute("LOAD spatial")


def table_columns(
    connection: duckdb.DuckDBPyConnection,
    table: str,
) -> list[str]:
    """Return table columns in their declared order."""
    return [
        row[0]
        for row in connection.execute(f"DESCRIBE {table}").fetchall()
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the DuckDB schema and load the final EV dataset."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--sa4", type=Path, default=DEFAULT_SA4)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    input_file = args.input.resolve()
    summary_file = args.summary.resolve()
    sa4_file = args.sa4.resolve()
    schema_file = args.schema.resolve()
    database_file = args.database.resolve()

    require_file(input_file, "Augmented charger CSV")
    require_file(summary_file, "Augmentation summary")
    require_file(sa4_file, "SA4 Shapefile")
    require_file(schema_file, "DuckDB schema")

    schema_sql = schema_file.read_text(encoding="utf-8")
    if "TODO" in schema_sql.upper() or not schema_sql.strip():
        raise ValueError(f"The schema is still a placeholder: {schema_file}")

    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    required_summary_fields = {
        "input_records",
        "dc_records",
        "augmented_dc_records",
    }
    missing = required_summary_fields - summary.keys()
    if missing:
        raise ValueError(
            f"Summary is missing required fields: {sorted(missing)}"
        )

    database_file.parent.mkdir(parents=True, exist_ok=True)
    if database_file.suffix:
        build_file = database_file.with_name(
            f"{database_file.stem}.build{database_file.suffix}"
        )
    else:
        build_file = database_file.with_name(f"{database_file.name}.build")
    build_wal_file = Path(f"{build_file}.wal")
    for stale_file in (build_file, build_wal_file):
        if stale_file.exists():
            stale_file.unlink()

    # Build separately so a failed refresh never damages the last successful
    # database and repeated rebuilds do not grow the persistent DuckDB file.
    connection = duckdb.connect(str(build_file))
    transaction_started = False
    build_complete = False

    try:
        # LOAD is session-scoped. INSTALL is needed only for a new DuckDB
        # version/platform and intentionally occurs outside the data transaction.
        ensure_spatial(connection)

        connection.execute("BEGIN TRANSACTION")
        transaction_started = True

        # The DDL is idempotent and recreates only this project's objects.
        connection.execute(schema_sql)

        # Import as text first so that future changes in sampled values cannot
        # silently change the inferred database schema.
        connection.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE stage_ev AS
            SELECT *
            FROM read_csv(
                {sql_string(input_file)},
                header = true,
                all_varchar = true,
                nullstr = ''
            )
            """
        )

        stage_columns = table_columns(connection, "stage_ev")
        target_columns = [
            column
            for column in table_columns(connection, "ev_chargers")
            if column != "geometry"
        ]
        if stage_columns != target_columns:
            raise ValueError(
                "CSV columns do not match schema.sql.\n"
                f"CSV:    {stage_columns}\n"
                f"Schema: {target_columns}"
            )

        # Load parent SA4 rows before chargers so the foreign key can be
        # checked during the charger insert.
        connection.execute(
            f"""
            INSERT INTO sa4_regions BY NAME
            SELECT
                CAST(SA4_CODE26 AS VARCHAR) AS SA4_CODE26,
                CAST(SA4_NAME26 AS VARCHAR) AS SA4_NAME26,
                CAST(CHG_FLAG26 AS VARCHAR) AS CHG_FLAG26,
                CAST(CHG_LBL26 AS VARCHAR) AS CHG_LBL26,
                CAST(GCC_CODE26 AS VARCHAR) AS GCC_CODE26,
                CAST(GCC_NAME26 AS VARCHAR) AS GCC_NAME26,
                CAST(STE_CODE26 AS VARCHAR) AS STE_CODE26,
                CAST(STE_NAME26 AS VARCHAR) AS STE_NAME26,
                CAST(AUS_CODE26 AS VARCHAR) AS AUS_CODE26,
                CAST(AUS_NAME26 AS VARCHAR) AS AUS_NAME26,
                CAST(AREASQKM26 AS DOUBLE) AS AREASQKM26,
                geom AS geometry
            FROM ST_Read({sql_string(sa4_file)})
            """
        )

        # Textual identifiers deliberately remain VARCHAR. In particular,
        # external_id contains both zero-padded IDs and OSM IDs such as
        # "node/13206154678". Numerical and Boolean fields are cast explicitly.
        connection.execute(
            """
            INSERT INTO ev_chargers BY NAME
            WITH typed AS (
                SELECT * REPLACE (
                    CAST(Number_of_plugs AS INTEGER)
                        AS Number_of_plugs,
                    CAST(Latitude AS DOUBLE)
                        AS Latitude,
                    CAST(Longitude AS DOUBLE)
                        AS Longitude,
                    CAST(match_distance_m AS DOUBLE)
                        AS match_distance_m,
                    CAST(external_bay_count AS INTEGER)
                        AS external_bay_count,
                    CAST(external_number_of_points AS INTEGER)
                        AS external_number_of_points,
                    CAST(augmented AS BOOLEAN)
                        AS augmented,
                    CAST(evie_augmented AS BOOLEAN)
                        AS evie_augmented,
                    CAST(ampol_augmented AS BOOLEAN)
                        AS ampol_augmented,
                    CAST(ocm_augmented AS BOOLEAN)
                        AS ocm_augmented,
                    CAST(osm_augmented AS BOOLEAN)
                        AS osm_augmented
                )
                FROM stage_ev
            )
            SELECT
                *,
                ST_Transform(
                    ST_Point(Longitude, Latitude),
                    'EPSG:4326',
                    'EPSG:7844',
                    always_xy := true
                ) AS geometry
            FROM typed
            """
        )

        actual = connection.execute(
            """
            SELECT
                count(*) AS row_count,
                count(*) FILTER (
                    WHERE upper(trim(Charger_Type)) = 'DC'
                ) AS dc_count,
                count(*) FILTER (WHERE augmented) AS augmented_count,
                count(DISTINCT source_record_id) AS unique_ids,
                count(*) FILTER (WHERE geometry IS NULL)
                    AS missing_geometry,
                count(*) FILTER (WHERE SA4_CODE26 IS NULL)
                    AS missing_sa4
            FROM ev_chargers
            """
        ).fetchone()

        expected = (
            int(summary["input_records"]),
            int(summary["dc_records"]),
            int(summary["augmented_dc_records"]),
        )
        if actual[0:3] != expected:
            raise ValueError(
                "Loaded metrics disagree with augmentation_summary.json: "
                f"loaded={actual[0:3]}, expected={expected}"
            )
        if actual[3] != actual[0]:
            raise ValueError("source_record_id is not unique.")
        if actual[4] != 0:
            raise ValueError(
                f"{actual[4]} charger records have no geometry."
            )
        if actual[5] != 0:
            raise ValueError(
                f"{actual[5]} charger records have no SA4 assignment."
            )

        sa4_count, sa4_spatial_count = connection.execute(
            "SELECT count(*), count(geometry) FROM sa4_regions"
        ).fetchone()
        source_sa4_count, source_sa4_spatial_count = connection.execute(
            f"""
            SELECT count(*), count(geom)
            FROM ST_Read({sql_string(sa4_file)})
            """
        ).fetchone()
        if (
            sa4_count == 0
            or sa4_spatial_count == 0
            or sa4_count != source_sa4_count
            or sa4_spatial_count != source_sa4_spatial_count
        ):
            raise ValueError(
                "SA4 counts do not match the source Shapefile: "
                f"loaded=({sa4_count}, {sa4_spatial_count}), "
                f"source=({source_sa4_count}, {source_sa4_spatial_count})"
            )

        # Check the transformed point by round-tripping it back to WGS84.
        coordinate_errors = connection.execute(
            """
            SELECT count(*)
            FROM ev_chargers
            WHERE abs(
                ST_X(
                    ST_Transform(
                        geometry,
                        'EPSG:7844',
                        'EPSG:4326',
                        always_xy := true
                    )
                ) - Longitude
            ) > 0.00001
            OR abs(
                ST_Y(
                    ST_Transform(
                        geometry,
                        'EPSG:7844',
                        'EPSG:4326',
                        always_xy := true
                    )
                ) - Latitude
            ) > 0.00001
            """
        ).fetchone()[0]
        if coordinate_errors:
            raise ValueError(
                f"{coordinate_errors} transformed geometries failed validation."
            )

        connection.execute(
            """
            INSERT INTO load_manifest (
                loaded_at,
                source_file,
                source_sha256,
                summary_file,
                sa4_file,
                row_count,
                dc_count,
                augmented_dc_count,
                sa4_count,
                sa4_spatial_count,
                geometry_crs,
                duckdb_version
            )
            VALUES (
                current_timestamp,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                'EPSG:7844',
                version()
            )
            """,
            [
                str(input_file),
                sha256_file(input_file),
                str(summary_file),
                str(sa4_file),
                actual[0],
                actual[1],
                actual[2],
                sa4_count,
                sa4_spatial_count,
            ],
        )

        connection.execute("COMMIT")
        transaction_started = False
        build_complete = True

    except Exception:
        if transaction_started:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
        if not build_complete:
            for failed_file in (build_file, build_wal_file):
                if failed_file.exists():
                    failed_file.unlink()

    # os.replace semantics make this an atomic swap on the same filesystem.
    # The previous successful database remains untouched until this point.
    build_file.replace(database_file)

    print(f"DuckDB created: {database_file}")
    print(f"Charger records: {actual[0]}")
    print(f"DC records: {actual[1]}")
    print(f"Augmented DC records: {actual[2]}")
    print(
        f"SA4 records: {sa4_count} "
        f"({sa4_spatial_count} with polygon geometry)"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, duckdb.Error) as exc:
        print(f"Step 06 failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
