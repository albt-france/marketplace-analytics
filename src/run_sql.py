"""Expose the clean Parquet tables to DuckDB and run every query in sql/, saving results to data/output/."""
import pathlib
import duckdb

con = duckdb.connect()
for f in sorted(pathlib.Path("data/clean").glob("*.parquet")):
    con.execute(f"CREATE VIEW {f.stem} AS SELECT * FROM read_parquet('{f.as_posix()}')")

out = pathlib.Path("data/output")
out.mkdir(exist_ok=True)
for q in sorted(pathlib.Path("sql").glob("*.sql")):
    df = con.execute(q.read_text(encoding="utf-8")).df()
    df.to_csv(out / (q.stem + ".csv"), index=False)
    print(f"== {q.name}: {len(df)} rows")
    print(df.head(10).to_string(index=False))
