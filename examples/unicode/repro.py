"""Reproduce an ASCII export failure from a synthetic Unicode name."""
import csv
import io
import sys


with open(sys.argv[1], "r", encoding="utf-8", newline="") as source:
    rows = csv.DictReader(source)
    sink = io.TextIOWrapper(io.BytesIO(), encoding="ascii", newline="")
    writer = csv.DictWriter(sink, fieldnames=rows.fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
