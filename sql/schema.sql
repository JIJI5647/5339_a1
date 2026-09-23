-- DuckDB schema for the final EV charger dataset.
-- The Python loader installs the extension when necessary. LOAD remains here
-- because extension loading is connection-scoped and the DDL uses GEOMETRY.
LOAD spatial;

DROP VIEW IF EXISTS unresolved_dc_chargers;
DROP VIEW IF EXISTS augmented_dc_chargers;
DROP VIEW IF EXISTS dc_chargers;
DROP TABLE IF EXISTS ev_chargers;
DROP TABLE IF EXISTS sa4_regions;
DROP TABLE IF EXISTS load_manifest;

CREATE TABLE sa4_regions (
    SA4_CODE26 VARCHAR PRIMARY KEY,
    SA4_NAME26 VARCHAR NOT NULL,
    CHG_FLAG26 VARCHAR,
    CHG_LBL26 VARCHAR,
    GCC_CODE26 VARCHAR,
    GCC_NAME26 VARCHAR,
    STE_CODE26 VARCHAR,
    STE_NAME26 VARCHAR,
    AUS_CODE26 VARCHAR,
    AUS_NAME26 VARCHAR,
    AREASQKM26 DOUBLE CHECK (AREASQKM26 >= 0),
    -- ABS also publishes non-spatial categories such as "No usual address"
    -- and "Outside Australia" in the SA4 layer. Their geometry is NULL.
    geometry GEOMETRY
);

CREATE TABLE ev_chargers (
    Station_name VARCHAR,
    Station_address VARCHAR NOT NULL,
    Operator VARCHAR NOT NULL,
    Number_of_plugs INTEGER NOT NULL CHECK (Number_of_plugs >= 0),
    Charger_Type VARCHAR NOT NULL
        CHECK (upper(trim(Charger_Type)) IN ('AC', 'DC', 'UPCOMING')),
    -- NULL where the source gave "AC" instead of a power rating.
    Charger_rating VARCHAR,
    Latitude DOUBLE NOT NULL CHECK (Latitude BETWEEN -44 AND -10),
    Longitude DOUBLE NOT NULL CHECK (Longitude BETWEEN 112 AND 154),
    LGANAME VARCHAR,
    PCODE VARCHAR,
    Source VARCHAR,
    SA4_CODE26 VARCHAR NOT NULL,
    SA4_NAME26 VARCHAR NOT NULL,
    STE_NAME26 VARCHAR NOT NULL,
    source_record_id VARCHAR PRIMARY KEY,
    dc_location_key VARCHAR,
    augmentation_status VARCHAR NOT NULL,
    match_distance_m DOUBLE CHECK (
        match_distance_m IS NULL OR match_distance_m >= 0
    ),
    match_method VARCHAR,
    external_id VARCHAR,
    external_title VARCHAR,
    external_address VARCHAR,
    external_operator VARCHAR,
    external_dc_plug_types VARCHAR,
    external_usage_cost_text VARCHAR,
    external_access_type VARCHAR,
    external_bay_count INTEGER CHECK (
        external_bay_count IS NULL OR external_bay_count >= 0
    ),
    external_number_of_points INTEGER CHECK (
        external_number_of_points IS NULL
        OR external_number_of_points >= 0
    ),
    external_status VARCHAR,
    external_last_verified VARCHAR,
    external_provider VARCHAR,
    external_provider_license VARCHAR,
    external_source_url VARCHAR,
    augmented BOOLEAN NOT NULL,
    augmentation_source VARCHAR,
    matched_sources VARCHAR,
    evie_match_status VARCHAR NOT NULL,
    evie_augmented BOOLEAN NOT NULL,
    ampol_match_status VARCHAR NOT NULL,
    ampol_augmented BOOLEAN NOT NULL,
    ocm_match_status VARCHAR NOT NULL,
    ocm_augmented BOOLEAN NOT NULL,
    osm_match_status VARCHAR NOT NULL,
    osm_augmented BOOLEAN NOT NULL,
    geometry GEOMETRY NOT NULL,
    FOREIGN KEY (SA4_CODE26) REFERENCES sa4_regions (SA4_CODE26),
    CHECK (
        NOT augmented OR upper(trim(Charger_Type)) = 'DC'
    ),
    CHECK (
        (upper(trim(Charger_Type)) = 'DC' AND dc_location_key IS NOT NULL)
        OR
        (upper(trim(Charger_Type)) <> 'DC' AND dc_location_key IS NULL)
    )
);

CREATE TABLE load_manifest (
    loaded_at TIMESTAMPTZ NOT NULL,
    source_file VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL CHECK (length(source_sha256) = 64),
    summary_file VARCHAR NOT NULL,
    sa4_file VARCHAR NOT NULL,
    row_count BIGINT NOT NULL CHECK (row_count >= 0),
    dc_count BIGINT NOT NULL CHECK (dc_count >= 0),
    augmented_dc_count BIGINT NOT NULL CHECK (augmented_dc_count >= 0),
    sa4_count BIGINT NOT NULL CHECK (sa4_count >= 0),
    sa4_spatial_count BIGINT NOT NULL CHECK (sa4_spatial_count >= 0),
    geometry_crs VARCHAR NOT NULL,
    duckdb_version VARCHAR NOT NULL
);

CREATE VIEW dc_chargers AS
SELECT *
FROM ev_chargers
WHERE upper(trim(Charger_Type)) = 'DC';

CREATE VIEW augmented_dc_chargers AS
SELECT *
FROM dc_chargers
WHERE augmented;

CREATE VIEW unresolved_dc_chargers AS
SELECT *
FROM dc_chargers
WHERE NOT augmented;
