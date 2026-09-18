# COMP5339 Assignment 1

This project builds a reproducible data pipeline for integrating NSW electric
vehicle charger locations with ABS SA4 geographic regions. The completed
pipeline will also augment DC fast-charger records with information from an
external web source and store the final data in DuckDB.

## Project structure

```text
assignment1/
├── data/              # Downloaded and generated data (not tracked by Git)
├── notebooks/         # Exploratory analysis
├── sql/schema.sql     # DuckDB schema
├── src/               # Pipeline scripts, run in numerical order
├── .env.example       # API-key template
├── requirements.txt   # Python dependencies
└── README.md
```

## Setup

Python 3.10 or later is recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If an external API is used for data augmentation, copy `.env.example` to
`.env` and add the required API key. Do not commit `.env`.

## Running the project

Run all commands from the `assignment1` directory:

```bash
python src/01_acquire_ev_chargers.py
python src/02_acquire_asgs_sa4.py
python src/03_clean_data.py
```

The first two scripts download the December 2025 NSW EV charger dataset and
the ABS ASGS Edition 4 SA4 boundary files into `data/raw/`. The third script
currently reads and inspects the SA4 shapefile.

The remaining pipeline stages are still under development:

```bash
python src/04_spatial_join_sa4.py
python src/05_augment_chargers.py
python src/06_load_duckdb.py
```

They will perform the SA4 spatial join, augment DC charger attributes, and
create the final DuckDB database using `sql/schema.sql`.

## Data sources

- Transport for NSW: EV charging locations, December 2025 release.
- Australian Bureau of Statistics: ASGS Edition 4 SA4 digital boundaries,
  GDA2020, 2026–2031.

Downloaded datasets and generated databases are excluded from Git because
they can be recreated by the pipeline. They must still be included in the
final Canvas submission where required by the assignment specification.
