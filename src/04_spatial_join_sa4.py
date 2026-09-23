from pathlib import Path
import pandas as pd
import geopandas as gpd


# --------------------------------------------------
# File paths
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EV_FILE = PROJECT_ROOT / "data" / "processed" / "ev_clean.csv"

SA4_FILE = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "SA4_2026_AUST_SHP_GDA2020"
    / "SA4_2026_AUST_GDA2020.shp"
)

OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "ev_with_sa4.csv"
)


# --------------------------------------------------
# Load data
# --------------------------------------------------

print("Loading cleaned EV charger data...")
ev = pd.read_csv(EV_FILE)

print("Loading SA4 spatial data...")
sa4 = gpd.read_file(SA4_FILE)

print("EV rows:", len(ev))
print("SA4 regions:", len(sa4))
print("SA4 CRS:", sa4.crs)


# --------------------------------------------------
# Convert EV coordinates to spatial points
# --------------------------------------------------

ev_gdf = gpd.GeoDataFrame(
    ev,
    geometry=gpd.points_from_xy(
        ev["Longitude"],
        ev["Latitude"]
    ),
    crs="EPSG:4326"
)


# --------------------------------------------------
# Match CRS with SA4 polygons
# --------------------------------------------------

ev_gdf = ev_gdf.to_crs(sa4.crs)

print("EV CRS after conversion:", ev_gdf.crs)


# --------------------------------------------------
# Spatial join
# Each EV charger is assigned to an SA4 region
# --------------------------------------------------

joined = gpd.sjoin(
    ev_gdf,
    sa4[
        [
            "SA4_CODE26",
            "SA4_NAME26",
            "STE_NAME26",
            "geometry"
        ]
    ],
    how="left",
    predicate="within"
)

print()
print("Spatial join completed.")
print("Joined rows:", len(joined))

unmatched = joined["SA4_CODE26"].isna()

print("Direct SA4 matches:", (~unmatched).sum())
print("Unmatched chargers:", unmatched.sum())


# --------------------------------------------------
# Handle very small boundary mismatches
# --------------------------------------------------

if unmatched.any():

    print()
    print("Checking unmatched chargers...")

    # Use projected CRS so distance is measured in metres
    projected_crs = "EPSG:7856"

    sa4_projected = sa4.to_crs(projected_crs)

    for idx in joined[unmatched].index:

        point = gpd.GeoSeries(
            [joined.loc[idx, "geometry"]],
            crs=sa4.crs
        ).to_crs(projected_crs).iloc[0]

        distances = sa4_projected.geometry.distance(point)

        nearest_idx = distances.idxmin()
        nearest_distance = distances.loc[nearest_idx]

        print(
            f"Row {idx}: nearest SA4 = "
            f"{sa4.loc[nearest_idx, 'SA4_NAME26']}, "
            f"distance = {nearest_distance:.2f} metres"
        )

        # Only repair very small boundary differences
        if nearest_distance <= 10:

            joined.loc[idx, "SA4_CODE26"] = (
                sa4.loc[nearest_idx, "SA4_CODE26"]
            )

            joined.loc[idx, "SA4_NAME26"] = (
                sa4.loc[nearest_idx, "SA4_NAME26"]
            )

            joined.loc[idx, "STE_NAME26"] = (
                sa4.loc[nearest_idx, "STE_NAME26"]
            )

            print("Assigned to nearest SA4.")


# --------------------------------------------------
# Final validation
# --------------------------------------------------

remaining_unmatched = joined["SA4_CODE26"].isna().sum()

print()
print("Final validation:")
print("Original EV rows:", len(ev))
print("Final joined rows:", len(joined))
print("Remaining unmatched:", remaining_unmatched)

if remaining_unmatched:
    raise ValueError(
        f"{remaining_unmatched} records still have no SA4 assignment."
    )


# --------------------------------------------------
# Save result
# Geometry is not needed in the CSV output because
# latitude and longitude are already retained.
# --------------------------------------------------

output = joined.drop(
    columns=["geometry", "index_right"],
    errors="ignore"
)

output.to_csv(OUTPUT_FILE, index=False)

print()
print("Spatial join completed successfully.")
print("Saved to:", OUTPUT_FILE)
