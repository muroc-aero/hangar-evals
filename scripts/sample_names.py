#!/usr/bin/env python3
"""Sample given names from three public name datasets — one small CLI.

The three sources trade off differently; pick by what "diverse" has to mean:

``names-dataset``
    philipperemy/names-dataset — ~730K first names across 105 countries with
    per-country popularity rank and gender. Best coverage, and the only source
    here with enough volume to *stratify*: take K names per country and the
    sample is uniform over geography instead of population-weighted. Caveats:
    it is derived from the 2021 Facebook leak (skewed toward internet-using
    cohorts, and the provenance may disqualify it for some uses) and the
    package wants ~3.2GB of RAM. Install with ``pip install names-dataset``.

``sigpwned``
    sigpwned/popular-names-by-country-dataset — CC0, ~2.4K forenames from 106
    countries, with both native-script and romanized forms plus popularity
    index. Small, but clean licensing and real non-Latin scripts. Downloaded
    as a single CSV (cached under ``--cache``); ``--csv-path`` reads a local
    copy and never touches the network.

``wikidata``
    A SPARQL count of how often each given-name item (P735) is attached to a
    human (Q5). That is frequency among globally *notable* people across all
    eras — a different diversity axis than living-population data, and heavily
    skewed toward European and historical figures. Needs egress to
    query.wikidata.org.

Every source normalizes to the same ``Name`` record, so ``--format csv/json``
output is comparable across sources.

Examples::

    # 20 names per country from every country in names-dataset, drawn from
    # each country's top 200 (the stratified case — the reason to use source 1)
    python scripts/sample_names.py --source names-dataset --per-country 20 --seed 7

    # 100 CC0 names, native script, as JSON
    python scripts/sample_names.py --source sigpwned --n 100 --script localized \\
        --format json --seed 7

    # 5 names per country from the CC0 set, women only, from a local CSV
    python scripts/sample_names.py --source sigpwned --per-country 5 --gender F \\
        --csv-path common-forenames-by-country.csv

    # the 500 most-attached given names among notable humans
    python scripts/sample_names.py --source wikidata --n 500 --seed 7

``--seed`` makes any sample reproducible; without it the draw is random.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

SIGPWNED_CSV_URL = (
    "https://raw.githubusercontent.com/sigpwned/popular-names-by-country-dataset"
    "/main/common-forenames-by-country.csv"
)
WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
WIKIDATA_QUERY = """\
SELECT ?nameLabel (COUNT(?p) AS ?c) WHERE {
  ?p wdt:P31 wd:Q5 ; wdt:P735 ?name .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
} GROUP BY ?nameLabel ORDER BY DESC(?c) LIMIT %d"""
# Wikidata's endpoint rejects generic agents; identify the caller (their policy).
USER_AGENT = "hangar-evals-name-sampler/0.1 (+https://github.com/muroc-aero/hangar-evals)"
DEFAULT_CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "name-sampler"

FIELDS = ("name", "source", "country", "gender", "romanized", "rank", "count")


@dataclass(frozen=True)
class Name:
    """One sampled given name, normalized across sources.

    ``rank`` is popularity *within a country* (1 = most common) where the
    source provides it; ``count`` is an absolute frequency (wikidata only).
    """

    name: str
    source: str
    country: str | None = None
    gender: str | None = None
    romanized: str | None = None
    rank: int | None = None
    count: int | None = None


# --------------------------------------------------------------------------
# generic sampling helpers
# --------------------------------------------------------------------------

def take(pool: list, k: int, rng: random.Random) -> list:
    """``rng.sample`` that yields the whole pool instead of raising when k > len."""
    return rng.sample(pool, k) if k < len(pool) else rng.sample(pool, len(pool))


def stratify(names: list[Name], per_country: int, rng: random.Random) -> list[Name]:
    """Sample ``per_country`` names from EACH country present, not from the pool.

    This is the knob that keeps a big country from swamping the sample: every
    country contributes the same number of names regardless of how many
    candidates it has (short pools contribute all they have).
    """
    by_country: dict[str | None, list[Name]] = {}
    for n in names:
        by_country.setdefault(n.country, []).append(n)
    out: list[Name] = []
    for cc in sorted(by_country, key=lambda c: (c is None, c or "")):
        out.extend(take(by_country[cc], per_country, rng))
    return out


def filter_gender(names: list[Name], gender: str | None) -> list[Name]:
    if not gender or gender == "any":
        return names
    return [n for n in names if (n.gender or "").upper() == gender.upper()]


# --------------------------------------------------------------------------
# source 1: names-dataset (philipperemy)
# --------------------------------------------------------------------------

def load_names_dataset():  # pragma: no cover - exercised only with the real package
    try:
        from names_dataset import NameDataset
    except ImportError as exc:  # keep the base repo dependency-free
        raise SystemExit(
            "source 'names-dataset' needs the names-dataset package "
            "(pip install names-dataset; ~3.2GB RAM to load)"
        ) from exc
    return NameDataset()


def collect_names_dataset(
    nd,
    top: int = 200,
    countries: list[str] | None = None,
) -> list[Name]:
    """Pull each country's top-``top`` first names off a NameDataset instance.

    ``nd`` is injected rather than constructed so this stays testable without
    the 3.2GB load; anything with ``get_country_codes``/``get_top_names`` works.
    """
    codes = countries or nd.get_country_codes(alpha_2=True)
    out: list[Name] = []
    for cc in codes:
        try:
            block = nd.get_top_names(n=top, country_alpha2=cc)[cc]
        except Exception as exc:  # a country with no first-name data
            print(f"warning: skipping {cc}: {exc}", file=sys.stderr)
            continue
        for gender, entries in sorted(block.items()):
            for rank, name in enumerate(entries, start=1):
                out.append(
                    Name(name=name, source="names-dataset", country=cc,
                         gender=gender, rank=rank)
                )
    return out


# --------------------------------------------------------------------------
# source 2: sigpwned/popular-names-by-country-dataset (CC0)
# --------------------------------------------------------------------------

def fetch_sigpwned(cache_dir: Path | None = DEFAULT_CACHE, refresh: bool = False) -> str:
    """Return the CC0 CSV text, downloading once and caching it on disk."""
    cached = (cache_dir / "common-forenames-by-country.csv") if cache_dir else None
    if cached and cached.exists() and not refresh:
        return cached.read_text(encoding="utf-8")
    text = http_get(SIGPWNED_CSV_URL).decode("utf-8-sig")
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(text, encoding="utf-8")
    return text


def parse_sigpwned(text: str, script: str = "romanized") -> list[Name]:
    """Parse the CC0 CSV into ``Name`` records.

    ``script='localized'`` takes the native-script form (Han, Cyrillic, Arabic,
    ...) as the name and keeps the romanization alongside; ``'romanized'``
    takes the Latin form. Rows whose chosen form is blank are dropped.
    """
    rows = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    out: list[Name] = []
    for row in rows:
        localized = (row.get("Localized Name") or "").strip()
        romanized = (row.get("Romanized Name") or "").strip()
        name = localized if script == "localized" else romanized
        if not name:
            continue
        try:
            rank = int(row["Index"])
        except (KeyError, TypeError, ValueError):
            rank = None
        out.append(
            Name(name=name, source="sigpwned",
                 country=(row.get("Country") or "").strip() or None,
                 gender=(row.get("Gender") or "").strip() or None,
                 romanized=romanized or None, rank=rank)
        )
    return out


# --------------------------------------------------------------------------
# source 3: Wikidata P735 counts
# --------------------------------------------------------------------------

def fetch_wikidata(limit: int = 5000) -> dict:
    url = WIKIDATA_ENDPOINT + "?" + urllib.parse.urlencode({"query": WIKIDATA_QUERY % limit})
    raw = http_get(url, accept="application/sparql-results+json")
    return json.loads(raw.decode("utf-8"))


def parse_wikidata(payload: dict) -> list[Name]:
    """Turn a SPARQL results JSON payload into ``Name`` records.

    Unlabeled name items come back as bare Q-ids; those are dropped rather
    than emitted as names.
    """
    out: list[Name] = []
    for binding in payload.get("results", {}).get("bindings", []):
        label = binding.get("nameLabel", {}).get("value", "").strip()
        if not label or (label.startswith("Q") and label[1:].isdigit()):
            continue
        try:
            count = int(binding.get("c", {}).get("value", ""))
        except ValueError:
            count = None
        out.append(Name(name=label, source="wikidata", count=count))
    return out


# --------------------------------------------------------------------------
# io
# --------------------------------------------------------------------------

def endpoint(url: str) -> str:
    """The URL without its query string — a SPARQL query is not an error message."""
    return url.split("?", 1)[0]


def http_get(url: str, accept: str | None = None, timeout: int = 120) -> bytes:
    """GET with the identifying UA Wikidata's endpoint requires.

    Network failures become a one-line SystemExit rather than a traceback —
    a blocked egress (proxy, offline laptop) is an expected outcome here, not
    a bug. ``--csv-path`` is the offline route for the CC0 source.
    """
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https hosts
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{endpoint(url)} returned HTTP {exc.code} {exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SystemExit(f"could not reach {endpoint(url)}: {exc}") from exc


def render(names: list[Name], fmt: str) -> str:
    if fmt == "text":
        return "\n".join(n.name for n in names)
    if fmt == "json":
        return json.dumps([asdict(n) for n in names], ensure_ascii=False, indent=2)
    if fmt == "jsonl":
        return "\n".join(json.dumps(asdict(n), ensure_ascii=False) for n in names)
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for n in names:
            writer.writerow(asdict(n))
        return buf.getvalue().rstrip("\n")
    raise ValueError(f"unknown format: {fmt}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sample_names.py",
        description="Sample given names from names-dataset, the CC0 "
                    "popular-names-by-country set, or Wikidata P735 counts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="With --per-country the sample is stratified: every country "
               "contributes the same number of names, so no region is "
               "over-represented. Without it, --n draws from the flat pool.",
    )
    p.add_argument("--source", choices=("names-dataset", "sigpwned", "wikidata"),
                   default="sigpwned", help="dataset to sample (default: sigpwned)")
    p.add_argument("--n", type=int, default=None,
                   help="total names to draw (default: 100, or all with --per-country)")
    p.add_argument("--per-country", type=int, default=None,
                   help="stratified draw: this many names from EACH country "
                        "(ignored for wikidata, which has no country axis)")
    p.add_argument("--top", type=int, default=200,
                   help="names-dataset: candidate pool depth per country/gender "
                        "(default: 200)")
    p.add_argument("--countries", default=None,
                   help="comma-separated ISO alpha-2 codes to restrict to "
                        "(default: every country in the source)")
    p.add_argument("--gender", choices=("M", "F", "any"), default="any",
                   help="keep only this gender (default: any)")
    p.add_argument("--script", choices=("romanized", "localized"), default="romanized",
                   help="sigpwned: native-script or Latin form (default: romanized)")
    p.add_argument("--limit", type=int, default=5000,
                   help="wikidata: SPARQL result LIMIT to sample from (default: 5000)")
    p.add_argument("--seed", type=int, default=None,
                   help="RNG seed — same seed + same source = same sample")
    p.add_argument("--format", choices=("text", "csv", "json", "jsonl"), default="text",
                   help="output format (default: text — one name per line)")
    p.add_argument("--out", type=Path, default=None, help="write here instead of stdout")
    p.add_argument("--csv-path", type=Path, default=None,
                   help="sigpwned: read this local CSV instead of downloading")
    p.add_argument("--cache", type=Path, default=DEFAULT_CACHE,
                   help=f"download cache directory (default: {DEFAULT_CACHE})")
    p.add_argument("--refresh", action="store_true",
                   help="re-download even if a cached copy exists")
    return p


def collect(args, rng: random.Random) -> list[Name]:
    """Load the chosen source, then filter + sample it."""
    countries = (
        [c.strip().upper() for c in args.countries.split(",") if c.strip()]
        if args.countries else None
    )

    if args.source == "names-dataset":
        pool = collect_names_dataset(load_names_dataset(), top=args.top, countries=countries)
    elif args.source == "sigpwned":
        text = (args.csv_path.read_text(encoding="utf-8-sig") if args.csv_path
                else fetch_sigpwned(args.cache, refresh=args.refresh))
        pool = parse_sigpwned(text, script=args.script)
        if countries:
            # the CC0 set covers ~106 countries; say so when one is missing
            # instead of silently returning a smaller sample
            present = {n.country for n in pool}
            for cc in countries:
                if cc not in present:
                    print(f"warning: {cc} is not in this dataset", file=sys.stderr)
            pool = [n for n in pool if n.country in countries]
    else:
        pool = parse_wikidata(fetch_wikidata(args.limit))

    pool = filter_gender(pool, args.gender)
    if not pool:
        raise SystemExit("no names matched — loosen --countries/--gender")

    if args.per_country and args.source != "wikidata":
        sample = stratify(pool, args.per_country, rng)
        if args.n:  # cap the stratified draw without losing its country balance
            sample = take(sample, args.n, rng)
    else:
        sample = take(pool, args.n or 100, rng)
    return sample


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rng = random.Random(args.seed)
    names = collect(args, rng)
    text = render(names, args.format)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {len(names)} names to {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
