"""Regenerate the checked-in, synthetic Specimen CSV example fixtures."""
import csv
from pathlib import Path


ROOT = Path(__file__).parent


def write_csv(folder: str, header: list[str], rows: list[list[str]]) -> None:
    path = ROOT / folder / "data.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


unicode_rows = [
    [f"Synthetic Person {i:04d}", f"person{i:04d}@example.test", f"U{i:04d}"]
    for i in range(1200)
]
unicode_rows[617][0] = "Māra Synthetic"
unicode_rows[617][1] = "mara617@example.test"
write_csv("unicode", ["name", "email", "record_id"], unicode_rows)

relation_rows = [
    [f"Synthetic Customer {i:04d}", f"customer{i:04d}@example.test", f"C{i:04d}"]
    for i in range(1200)
]
# Only this pair is repeated. Either row alone is valid, while both preserve the bug.
relation_rows[901] = ["Synthetic First", "shared@example.test", "C9001"]
relation_rows[1103] = ["Synthetic Second", "shared@example.test", "C9001"]
write_csv("relations", ["name", "email", "customer_id"], relation_rows)

conflict_rows = [
    [f"Synthetic Guest {i:04d}", f"guest{i:04d}@example.test"]
    for i in range(240)
]
conflict_rows[173][0] = "Required Synthetic Name"
write_csv("conflict", ["name", "email"], conflict_rows)
