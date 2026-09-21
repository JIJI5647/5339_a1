# COMP5339 Assignment 1

This project builds a reproducible data pipeline that integrates NSW electric
vehicle (EV) charger locations with ABS SA4 geographic regions, augments DC
fast-charger records with attributes from external web sources, and stores the
result in DuckDB.

## Project structure

```text
assignment1/
├── data/
│   ├── raw/                  # Downloaded source data (steps 01-02)
│   ├── processed/            # Cleaned and spatially joined data (steps 03-04)
│   └── external/             # External source snapshots and augmentation outputs (step 05)
├── db/                       # DuckDB database file (step 06)
├── notebooks/
│   └── data_analyze.ipynb    # Exploratory analysis
├── sql/
│   └── schema.sql            # DuckDB DDL script
├── src/                      # Pipeline scripts, run in numerical order
│   ├── 01_acquire_ev_chargers.py
│   ├── 02_acquire_asgs_sa4.py
│   ├── 03_clean_data.py
│   ├── 04_spatial_join_sa4.py
│   ├── 05_augment_chargers.py
│   └── 06_load_duckdb.py
├── .env.example              # API-key template
├── requirements.txt          # Python dependencies
└── README.md
```

## Setup

Python 3.10 or later is required (tested with Python 3.12). Run all commands
from the `assignment1` directory.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### API key

Only step 05 uses an API key, and only when it has to download external data.
The external snapshots are included in `data/external/`, so step 05 runs
offline without a key. A key is needed only to re-download them with
`--refresh`.

To use a key, register a free application at
<https://openchargemap.org/site/profile/applications>, then:

```bash
cp .env.example .env               # then set OCM_API_KEY=<your key> in .env
set -a; source .env; set +a        # export the variables to the current shell
```

`.env` is excluded from Git. The other variables in `.env.example` are not used.

## Execution

```bash
python src/01_acquire_ev_chargers.py
python src/02_acquire_asgs_sa4.py
python src/03_clean_data.py
python src/04_spatial_join_sa4.py
python src/05_augment_chargers.py
python src/06_load_duckdb.py
```

Each step reads the output of the previous step. Steps 01 and 02 use relative
paths, so they must be run from the `assignment1` directory.

Options for step 05:

| Option | Effect |
|---|---|
| (none) | Use the cached snapshots in `data/external/` if present, otherwise download them |
| `--refresh` | Re-download all four external sources (requires `OCM_API_KEY`) |
| `--ocm-only` | Use Open Charge Map only |

## Pipeline steps and outputs

| Step | What it does | Output |
|---|---|---|
| 01 | Downloads the December 2025 NSW EV charger dataset from Transport for NSW | `data/raw/ev_20251216.csv` |
| 02 | Downloads and extracts the ABS ASGS Edition 4 SA4 boundaries (GDA2020) | `data/raw/SA4_2026_AUST_SHP_GDA2020/` |
| 03 | Converts coordinates to numbers, drops rows with missing or out-of-Australia coordinates, trims text, removes exact duplicates | `data/processed/ev_clean.csv` |
| 04 | Spatially joins each charger to the SA4 region containing it; unmatched points within 10 m of a region are assigned to the nearest one | `data/processed/ev_with_sa4.csv` |
| 05 | Matches DC chargers to external charger sites and adds plug types, pricing text, access type and bay count | `data/external/` (see below) |
| 06 | Creates the schema in `sql/schema.sql` and loads the data into DuckDB | `db/` |

### Step 05 outputs (`data/external/`)

| File | Contents |
|---|---|
| `ev_augmented.csv` | Final charger dataset: every record from step 04 plus the augmentation columns. This is the input for step 06 |
| `augmentation_matches.csv` | Accepted matches, one row per charger and source |
| `augmentation_candidates.csv` | Every external site considered for each DC charger, with match evidence |
| `augmentation_review.csv` | DC chargers that could not be augmented |
| `augmentation_summary.json` | Coverage figures, matching rules, source provenance and limitations |
| `ocm_au.json`, `evie_sites.json`, `ampol_sites.json`, `osm_chargers.json` | Raw snapshots of the four external sources, with retrieval time and query |

## Data sources

| Source | Used by | Access |
|---|---|---|
| Transport for NSW, EV charging locations (December 2025) | 01 | Direct file download |
| ABS, ASGS Edition 4 SA4 digital boundaries, GDA2020 | 02 | Direct file download |
| Open Charge Map | 05 | Public REST API (API key required) |
| Evie Networks | 05 | Station locator endpoint on the official website |
| Ampol AmpCharge | 05 | Station locator API on the official website |
| OpenStreetMap | 05 | Overpass API |

The licence of each external record is stored in the
`external_provider_license` column of `ev_augmented.csv`.

## Assumptions

- Coordinates outside latitude −44 to −10 and longitude 112 to 154 are treated
  as invalid.
- A charger up to 10 m outside every SA4 polygon is a boundary precision issue
  and is assigned to the nearest SA4.
- Only records with `Charger_Type` equal to `DC` are augmented.
- An external site is accepted as the same location only when the operator
  matches, postcodes do not conflict, and the distance, address or name
  evidence meets the thresholds recorded in `augmentation_summary.json`.
- Coordinates rounded to six decimal places are used as a proxy for a physical
  charging location when calculating coverage.
- External snapshots reflect the retrieval date, not December 2025.
