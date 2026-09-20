from pathlib import Path
import pandas as pd


# --------------------------------------------------
# File paths
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

INPUT_FILE = PROJECT_ROOT / "data" / "raw" / "ev_20251216.csv"
OUTPUT_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT_FILE = OUTPUT_DIR / "ev_clean.csv"


# --------------------------------------------------
# Load raw EV charger data
# --------------------------------------------------

print("Loading EV charger data...")

df = pd.read_csv(INPUT_FILE)

print("Original rows:", len(df))
print("Original columns:", len(df.columns))


# --------------------------------------------------
# Clean column names
# --------------------------------------------------

df.columns = df.columns.str.strip()


# --------------------------------------------------
# Convert coordinates to numeric values
# Invalid values will become NaN
# --------------------------------------------------

df["Latitude"] = pd.to_numeric(df["Latitude"], errors="coerce")
df["Longitude"] = pd.to_numeric(df["Longitude"], errors="coerce")


# --------------------------------------------------
# Remove records without usable coordinates
# --------------------------------------------------

before = len(df)

df = df.dropna(subset=["Latitude", "Longitude"]).copy()

print("Rows removed because coordinates are missing:",
      before - len(df))


# --------------------------------------------------
# Keep coordinates inside a reasonable
# Australian geographic range
# --------------------------------------------------

before = len(df)

df = df[
    df["Latitude"].between(-44, -10)
    & df["Longitude"].between(112, 154)
].copy()

print("Rows removed because coordinates are outside Australia:",
      before - len(df))


# --------------------------------------------------
# Clean text columns
# --------------------------------------------------

text_columns = [
    "Station_name",
    "Station_address",
    "Operator",
    "Charger_Type",
    "Charger_rating",
    "LGANAME",
    "Source"
]

for column in text_columns:
    if column in df.columns:
        df[column] = df[column].astype("string").str.strip()


# --------------------------------------------------
# Remove exact duplicate records
# --------------------------------------------------

before = len(df)

df = df.drop_duplicates().copy()

print("Duplicate rows removed:", before - len(df))


# --------------------------------------------------
# Save cleaned data
# --------------------------------------------------

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

df.to_csv(OUTPUT_FILE, index=False)

print()
print("Cleaning completed.")
print("Final rows:", len(df))
print("Saved to:", OUTPUT_FILE)