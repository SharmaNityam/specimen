"""Reproduce a failure whose required sensitive value cannot be changed."""
import csv
import sys


with open(sys.argv[1], "r", encoding="utf-8", newline="") as source:
    rows = csv.DictReader(source)
    for row in rows:
        if row["name"] == "Required Synthetic Name":
            raise ValueError("required sentinel encountered")
