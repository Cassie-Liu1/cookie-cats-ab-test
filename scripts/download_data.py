"""Validate a local dataset without distributing a private copy."""
from pathlib import Path
import csv

SOURCE = "https://www.kaggle.com/datasets/yufengsui/mobile-games-ab-testing"
OUT = Path(__file__).parents[1] / "data" / "cookie_cats.csv"

def main():
    if not OUT.is_file():
        raise SystemExit(
            f"Obtain cookie_cats.csv from {SOURCE} after reviewing its terms, "
            "then place it in data/cookie_cats.csv. "
            "This helper does not download or redistribute the dataset."
        )
    with OUT.open(newline="", encoding="utf-8-sig") as source:
        fields = set(csv.DictReader(source).fieldnames or [])
    required = {"userid", "version", "sum_gamerounds", "retention_1", "retention_7"}
    if not required <= fields:
        raise SystemExit(f"Missing required columns: {sorted(required - fields)}")
    print("Local CSV has the required columns; run src/analyze.py next.")

if __name__ == "__main__":
    main()
