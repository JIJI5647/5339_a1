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
# OBJECTID
# Empty in 1837 of 1958 rows (93.8%), so it cannot serve
# as a record key. It is kept while cleaning because it
# is the only field that identifies the 121 rows with a
# different format, and it is dropped before saving.
# --------------------------------------------------

print("Rows with OBJECTID:", df["OBJECTID"].notna().sum())


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
# Station_name
# Empty in 1438 rows (432 of 433 DC rows). Missing names
# stay NULL: they are not filled from the address or
# replaced by a placeholder such as "Unknown".
# Surrounding spaces (20 values) are removed by the text
# cleaning above; the name itself is not changed.
# --------------------------------------------------

df["Station_name"] = df["Station_name"].replace("", pd.NA)

print("Rows without Station_name:", df["Station_name"].isna().sum())


# --------------------------------------------------
# Station_address
# 733 addresses separate their parts with line breaks,
# e.g. "697 Wollombi Rd\nBroke NSW 2330\nAustralia", while
# the others use commas. Only the formatting is
# standardised; no part of an address is added or removed.
#   - line breaks become ", "
#   - repeated spaces become one space
#   - spaces before a comma are removed ("Dubbo , 2830")
#   - repeated commas become one ("Street,, Dapto")
# Addresses with an empty street part, no postcode or an
# unusual postcode are kept as they are, because there is
# no reliable source to correct them.
# --------------------------------------------------

address_before = df["Station_address"].copy()

df["Station_address"] = (
    df["Station_address"]
    .str.replace(r"\s*\n\s*", ", ", regex=True)
    .str.replace(r"\s+", " ", regex=True)
    .str.replace(r"\s+,", ",", regex=True)
    .str.replace(r",(\s*,)+", ",", regex=True)
    .str.strip()
)

print("Addresses reformatted:",
      (df["Station_address"] != address_before).sum())


# --------------------------------------------------
# Operator
# The raw file has 50 distinct operator values, several
# of which are spellings of the same operator.
#   1. Surrounding spaces are removed by the text cleaning
#      above ("BP Australia ", "Tesla Motors ").
#   2. All values are lower-cased, which merges
#      "Non-networked" and "Non-Networked".
#   3. "charge hub" is written without the space, as in
#      the more frequent "chargehub".
#   4. Short and long forms of the same operator are
#      unified to the long form (e.g. "tesla" becomes
#      "tesla motors"). Two exceptions:
#      - "plus es manag" is incomplete, so both forms
#        become "plus es".
#      - "bp australia" becomes "bp", because "bp" is the
#        operator name used by the external sources in
#        step 05. With "bp australia", 17 BP DC chargers
#        lost their external match.
# Values that look incomplete but have no complete form
# in the file ("fast cities a", "energy austra",
# "university of") are kept unchanged. Operators with
# similar names but no evidence of being the same company
# (e.g. "eve australia" and "evie") are not merged.
# The cleaned value overwrites the original column.
# --------------------------------------------------

OPERATOR_NAMES = {
    "charge hub": "chargehub",
    "tesla": "tesla motors",
    "bp australia": "bp",
    "evie": "evie networks",
    "nrma": "nrma electric",
    "plus es manag": "plus es",
    "viva energy a": "viva energy australia",
}

operators_before = df["Operator"].nunique()

df["Operator"] = df["Operator"].str.lower().replace(OPERATOR_NAMES)

print("Distinct operators:", operators_before, "->", df["Operator"].nunique())


# --------------------------------------------------
# Charger_Type
# Three values without spelling variants: "AC" (1427),
# "DC" (433) and "Upcoming" (98). "Upcoming" describes the
# build status rather than the current type, but the values
# are kept unchanged: step 05 augments only "DC" rows and
# the schema accepts exactly these three values.
# --------------------------------------------------

print("Charger types:", df["Charger_Type"].value_counts().to_dict())


# --------------------------------------------------
# Charger_rating
# The documented meaning is the power rating of a plug,
# stored as text. Four formats occur in the raw file:
#   - "N kW" (1315 rows), e.g. "22 kW": kept.
#   - A number without unit (22 rows), e.g. "22": " kW" is
#     added, since every other rating in the file is in kW.
#   - The text "AC" (522 rows, all AC chargers): this repeats
#     Charger_Type and gives no power, so it becomes NULL.
#   - Combined values (99 rows), e.g. "2x350kW & 2x175kW":
#     kept unchanged.
# No numeric rating column is added.
# --------------------------------------------------

number_only = df["Charger_rating"].str.fullmatch(r"\d+(\.\d+)?", na=False)
rating_is_type = df["Charger_rating"].eq("AC").fillna(False)

df.loc[number_only, "Charger_rating"] = df.loc[number_only, "Charger_rating"] + " kW"
df.loc[rating_is_type, "Charger_rating"] = pd.NA

print("Ratings given a kW unit:", number_only.sum())
print("Ratings 'AC' set to NULL:", rating_is_type.sum())


# --------------------------------------------------
# LGANAME
# Empty in the 121 rows that have an OBJECTID. These stay
# NULL: the file has no reliable source to fill them, and
# regional analysis uses the SA4 region added in step 04.
# Names are kept as published, including both styles such
# as "Inner West Council" and "Sydney, Council of the City of".
# --------------------------------------------------

print("Rows without LGANAME:", df["LGANAME"].isna().sum())


# --------------------------------------------------
# PCODE
# Issues in the raw file:
#   - 10 values have a "NSW " prefix, e.g. "NSW 2500".
#   - 121 values are empty, but 120 of these addresses
#     contain a postcode.
#   - 26 values differ from the postcode in the address,
#     e.g. PCODE 2350 for "1 - 7 Ross St, Wilcannia NSW 2836".
# Rule: an existing PCODE is kept as published (only the
# "NSW " prefix is removed), so the 26 differing values are
# not changed. An empty PCODE is filled with the postcode
# written in the address, taken as the last 4-digit number
# (street numbers come first, so they are not picked up).
# If the address has no postcode either, PCODE stays NULL.
# --------------------------------------------------

pcode_before = df["PCODE"].astype("string").str.strip()

address_postcode = (
    df["Station_address"]
    .str.findall(r"\b(\d{4})\b")
    .str[-1]
    .astype("string")
)

df["PCODE"] = (
    pcode_before
    .str.replace(r"^NSW\s+", "", regex=True)
    .fillna(address_postcode)
)

print("PCODE 'NSW ' prefix removed:",
      pcode_before.str.startswith("NSW", na=False).sum())
print("PCODE filled from address:",
      (pcode_before.isna() & df["PCODE"].notna()).sum())
print("Rows without PCODE:", df["PCODE"].isna().sum())


# --------------------------------------------------
# Remove exact duplicate records
# --------------------------------------------------

before = len(df)

df = df.drop_duplicates().copy()

print("Duplicate rows removed:", before - len(df))


# --------------------------------------------------
# Rows with identical coordinates
# After the cleaning above there are still no exact
# duplicates, but 18 coordinate pairs occur in more than
# one row. Three cases:
#   1. Two rows, one with and one without an OBJECTID,
#      with the same operator, charger type, rating and
#      number of plugs (7 pairs). One row only lacks values
#      the other has (Station_name, LGANAME, Source). The
#      pair is merged: the row without an OBJECTID is kept
#      because it has more fields filled, and its empty
#      Station_name is taken from the other row.
#   2. Pairs or triples that include a row with an OBJECTID
#      but differ in operator, type, rating or plugs
#      (7 groups, e.g. "engie" DC vs "tesla motors" Upcoming
#      at the same point). The file gives no basis to choose
#      one row, so all rows are kept.
#   3. Rows without an OBJECTID that differ in operator,
#      type or plugs (4 groups). These are kept as well.
# --------------------------------------------------

CORE_COLUMNS = ["Operator", "Charger_Type", "Charger_rating", "Number_of_plugs"]

rows_to_drop = []

for _, group in df.groupby(["Latitude", "Longitude"]):

    if len(group) != 2 or group["OBJECTID"].notna().sum() != 1:
        continue

    # Compare the core attributes; two NULLs count as equal
    core = group[CORE_COLUMNS].astype("string").fillna("<NA>")
    if not (core.iloc[0] == core.iloc[1]).all():
        continue

    keep_idx = group.index[group["OBJECTID"].isna()][0]
    drop_idx = group.index[group["OBJECTID"].notna()][0]

    if pd.isna(df.at[keep_idx, "Station_name"]):
        df.at[keep_idx, "Station_name"] = df.at[drop_idx, "Station_name"]

    rows_to_drop.append(drop_idx)

df = df.drop(index=rows_to_drop)

print("Rows merged into a matching row at the same coordinates:",
      len(rows_to_drop))


# --------------------------------------------------
# Drop columns that are only needed during cleaning
# --------------------------------------------------

df = df.drop(columns=["OBJECTID"])


# --------------------------------------------------
# Save cleaned data
# --------------------------------------------------

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

df.to_csv(OUTPUT_FILE, index=False)

print()
print("Cleaning completed.")
print("Final rows:", len(df))
print("Saved to:", OUTPUT_FILE)
