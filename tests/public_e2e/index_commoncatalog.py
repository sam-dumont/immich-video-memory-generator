"""Index the metadata of CommonCatalog CC BY, one parquet per shard, resumable.

    uv run --with duckdb python -m tests.public_e2e.index_commoncatalog [--workers 12]

Reads only the metadata columns of every shard over HTTP (about 12 MB of the 5.5 GB a
shard holds), so the 14.6M-row index costs about 30 minutes, not 14 TB. Each row keeps
its shard and row number, so one picture's bytes can be read back later without
scanning anything.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import duckdb
import httpx

from tests.public_e2e.paths import USER_AGENT, work_root

DATASET = "common-canvas/commoncatalog-cc-by"
RESOLVE = f"https://huggingface.co/datasets/{DATASET}/resolve/main/"
COLUMNS = (
    "photoid, uid, unickname, datetaken, latitude, longitude, accuracy, capturedevice, "
    "usertags, title, licensename, licenseurl, pageurl, downloadurl, width, height"
)


def shard_names() -> list[str]:
    request = urllib.request.Request(
        f"https://huggingface.co/api/datasets/{DATASET}", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=60) as answer:  # noqa: S310 -- fixed https URL
        siblings = json.load(answer)["siblings"]
    return sorted(s["rfilename"] for s in siblings if s["rfilename"].endswith(".parquet"))


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(config={"custom_user_agent": USER_AGENT})
    con.execute("INSTALL httpfs; LOAD httpfs; SET http_retries = 5;")
    # The signed CDN address carries `*` in its policy; it is a literal, not a glob.
    con.execute("SET allow_asterisks_in_http_paths = true;")
    return con


def index_shard(shard: str, out_dir: Path, attempts: int = 6) -> int:
    """Write one shard's metadata rows to `out_dir`; skip a shard already written.

    The Hub answers a burst of range reads with 429s, so a failed shard waits and tries
    again (up to about four minutes in all) before it counts as failed.
    """
    target = out_dir / shard.replace("/", "__")
    if target.exists():
        return -1
    for attempt in range(attempts):
        try:
            return _write_shard(shard, target)
        except duckdb.Error:
            if attempt == attempts - 1:
                raise
            time.sleep(8 * 2**attempt)
    return 0


def _cdn_url(shard: str) -> str:
    """Resolve a shard once to its signed CDN address.

    Every read through `/resolve` counts against the Hub's resolver limit (5,000 per five
    minutes), and one parquet read makes dozens of range requests. Resolving once and
    reading the CDN address directly costs one resolver call per shard.
    """
    headers = {"User-Agent": USER_AGENT}
    token_file = Path.home() / ".cache" / "huggingface" / "token"
    if token_file.exists():
        headers["Authorization"] = f"Bearer {token_file.read_text().strip()}"
    for attempt in range(6):
        answer = httpx.head(RESOLVE + shard, headers=headers, follow_redirects=False, timeout=60)
        if answer.status_code in {301, 302, 307, 308}:
            return answer.headers["location"]
        if answer.status_code != 429:
            answer.raise_for_status()
        time.sleep(15 * (attempt + 1))
    raise duckdb.IOException(f"{shard}: the Hub kept answering 429")


def _write_shard(shard: str, target: Path) -> int:
    con = _connect()
    url = _cdn_url(shard)
    partial = target.with_suffix(".tmp")
    con.execute(
        f"COPY (SELECT {COLUMNS}, '{shard}' AS shard, file_row_number AS row_number "  # noqa: S608 -- local index path and fixed columns, values bound
        f"FROM read_parquet('{url}', file_row_number = true)) TO '{partial}' (FORMAT parquet)"
    )
    rows = con.execute(f"SELECT count(*) FROM read_parquet('{partial}')").fetchone()  # noqa: S608 -- local index path and fixed columns, values bound
    partial.rename(target)
    return int(rows[0]) if rows else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--limit", type=int, default=0, help="index only the first N shards")
    args = parser.parse_args(argv)
    out_dir = work_root() / "commoncatalog-index"
    out_dir.mkdir(parents=True, exist_ok=True)
    shards = shard_names()
    if args.limit:
        shards = shards[: args.limit]
    started, done, rows, failed = time.monotonic(), 0, 0, []
    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(index_shard, shard, out_dir): shard for shard in shards}
        for future in as_completed(futures):
            try:
                rows += max(future.result(), 0)
            except duckdb.Error as exc:
                failed.append(f"{futures[future]}: {exc}")
                print(f"FAILED {futures[future]}: {str(exc)[:200]}", file=sys.stderr, flush=True)
            done += 1
            if done % 100 == 0:
                print(
                    f"{done}/{len(shards)} shards, {len(failed)} failed, {rows} new rows, "
                    f"{time.monotonic() - started:.0f}s",
                    flush=True,
                )
    print(f"indexed {done - len(failed)}/{len(shards)} shards into {out_dir}")
    for line in failed:
        print("FAILED", line, file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
