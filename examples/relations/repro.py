"""Reproduce a duplicate relationship failure from a synthetic CSV."""
import csv
import sys


with open(sys.argv[1], "r", encoding="utf-8", newline="") as source:
    rows = csv.DictReader(source)
    seen = set()
    for row in rows:
        relationship = (row["email"], row["customer_id"])
        if relationship in seen:
            raise ValueError("duplicate relationship")
        seen.add(relationship)
