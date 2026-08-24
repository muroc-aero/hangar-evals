"""Name-sampler tests — pure logic only, never the network.

``scripts/sample_names.py`` is a standalone stdlib script (it deliberately
imports nothing from ``hangar.evals``), so it is loaded here by path. Every
test feeds it fixture text or a fake NameDataset: the download and SPARQL
paths are the only untested lines, and they are one ``urlopen`` each.
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sample_names.py"


def _load():
    spec = importlib.util.spec_from_file_location("sample_names", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # register before exec: @dataclass resolves annotations via sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sn = _load()

HEADER = ("Country,Country Group,Region,Population,Note,Year,Romanization,"
          "Index,Name Group,Gender,Localized Name,Romanized Name")
CSV_TEXT = "﻿" + "\n".join([
    HEADER,
    "FR,1,,,,2018,N,1,N-FR-1-F-1,F,Emma,Emma",
    "FR,1,,,,2018,N,2,N-FR-1-M-1,M,Louis,Louis",
    "FR,1,,,,2018,N,3,N-FR-1-M-2,M,Gabriel,Gabriel",
    "JP,1,,,,2018,Y,1,N-JP-1-F-1,F,陽菜,Hina",
    "JP,1,,,,2018,Y,2,N-JP-1-M-1,M,蓮,Ren",
    "CN,1,,,,2018,Y,1,N-CN-1-M-1,M,,Mùchén",
    "",
])


class FakeNameDataset:
    """Stands in for names_dataset.NameDataset — same two methods, no 3.2GB."""

    def __init__(self, data):
        self.data = data

    def get_country_codes(self, alpha_2=True):
        return list(self.data)

    def get_top_names(self, n=10, country_alpha2=None):
        block = self.data[country_alpha2]
        if block is None:
            raise ValueError("no first-name data")
        return {country_alpha2: {g: names[:n] for g, names in block.items()}}


# --- sigpwned CSV ----------------------------------------------------------

def test_parse_sigpwned_romanized_keeps_latin_forms():
    names = sn.parse_sigpwned(CSV_TEXT)
    assert [n.name for n in names] == ["Emma", "Louis", "Gabriel", "Hina", "Ren", "Mùchén"]
    assert names[0].country == "FR" and names[0].gender == "F" and names[0].rank == 1
    assert names[0].source == "sigpwned"


def test_parse_sigpwned_localized_prefers_native_script_and_drops_blanks():
    names = sn.parse_sigpwned(CSV_TEXT, script="localized")
    # the CN row has no localized form, so it drops out entirely
    assert [n.name for n in names] == ["Emma", "Louis", "Gabriel", "陽菜", "蓮"]
    hina = next(n for n in names if n.name == "陽菜")
    assert hina.romanized == "Hina"  # romanization is kept alongside


def test_parse_sigpwned_tolerates_the_utf8_bom():
    # the BOM must not become part of the first header key ("Country")
    assert sn.parse_sigpwned(CSV_TEXT)[0].country == "FR"


# --- stratification --------------------------------------------------------

def test_stratify_gives_every_country_the_same_count():
    names = sn.parse_sigpwned(CSV_TEXT)
    sample = sn.stratify(names, 2, random.Random(0))
    per_country = {}
    for n in sample:
        per_country[n.country] = per_country.get(n.country, 0) + 1
    assert per_country == {"CN": 1, "FR": 2, "JP": 2}  # CN only has one name to give


def test_same_seed_gives_the_same_sample_and_a_different_seed_usually_does_not():
    names = sn.parse_sigpwned(CSV_TEXT)
    a = [n.name for n in sn.stratify(names, 1, random.Random(7))]
    b = [n.name for n in sn.stratify(names, 1, random.Random(7))]
    assert a == b
    seeds = {tuple(n.name for n in sn.stratify(names, 1, random.Random(s)))
             for s in range(20)}
    assert len(seeds) > 1


def test_take_returns_the_whole_pool_when_k_exceeds_it():
    pool = list(range(3))
    assert sorted(sn.take(pool, 10, random.Random(0))) == pool


# --- names-dataset ---------------------------------------------------------

def test_collect_names_dataset_ranks_by_position_and_keeps_gender():
    nd = FakeNameDataset({"FR": {"M": ["Louis", "Gabriel"], "F": ["Emma"]}})
    names = sn.collect_names_dataset(nd, top=200)
    assert {(n.name, n.gender, n.rank) for n in names} == {
        ("Emma", "F", 1), ("Louis", "M", 1), ("Gabriel", "M", 2),
    }
    assert all(n.source == "names-dataset" and n.country == "FR" for n in names)


def test_collect_names_dataset_truncates_each_country_to_top():
    nd = FakeNameDataset({"FR": {"M": ["Louis", "Gabriel", "Raphaël"]}})
    assert len(sn.collect_names_dataset(nd, top=2)) == 2


def test_collect_names_dataset_skips_countries_without_data(capsys):
    nd = FakeNameDataset({"FR": {"M": ["Louis"]}, "XX": None})
    names = sn.collect_names_dataset(nd)
    assert [n.name for n in names] == ["Louis"]
    assert "skipping XX" in capsys.readouterr().err


def test_collect_names_dataset_honours_an_explicit_country_list():
    nd = FakeNameDataset({"FR": {"M": ["Louis"]}, "JP": {"M": ["Ren"]}})
    assert [n.country for n in sn.collect_names_dataset(nd, countries=["JP"])] == ["JP"]


# --- wikidata --------------------------------------------------------------

def test_parse_wikidata_keeps_labels_with_counts_and_drops_bare_qids():
    payload = {"results": {"bindings": [
        {"nameLabel": {"value": "John"}, "c": {"value": "182000"}},
        {"nameLabel": {"value": "Q12345"}, "c": {"value": "12"}},  # unlabeled item
        {"nameLabel": {"value": ""}, "c": {"value": "3"}},
    ]}}
    names = sn.parse_wikidata(payload)
    assert [(n.name, n.count, n.source) for n in names] == [("John", 182000, "wikidata")]


def test_parse_wikidata_handles_an_empty_result_set():
    assert sn.parse_wikidata({"results": {"bindings": []}}) == []


# --- network failure handling ----------------------------------------------

def test_http_get_reports_an_unreachable_host_without_a_traceback(monkeypatch):
    import urllib.error

    def boom(*a, **kw):
        raise urllib.error.URLError("Tunnel connection failed: 403 Forbidden")

    monkeypatch.setattr(sn.urllib.request, "urlopen", boom)
    with pytest.raises(SystemExit, match="could not reach https://query.wikidata.org/sparql"):
        sn.fetch_wikidata(limit=5)  # the SPARQL query must not land in the message


def test_http_get_reports_an_http_status(monkeypatch):
    import urllib.error

    def boom(*a, **kw):
        raise urllib.error.HTTPError(sn.SIGPWNED_CSV_URL, 404, "Not Found", {}, None)

    monkeypatch.setattr(sn.urllib.request, "urlopen", boom)
    with pytest.raises(SystemExit, match="HTTP 404"):
        sn.fetch_sigpwned(cache_dir=None)


# --- filtering + rendering -------------------------------------------------

def test_filter_gender_is_a_no_op_for_any():
    names = sn.parse_sigpwned(CSV_TEXT)
    assert sn.filter_gender(names, "any") == names
    assert {n.gender for n in sn.filter_gender(names, "F")} == {"F"}


@pytest.mark.parametrize("fmt", ["text", "csv", "json", "jsonl"])
def test_render_round_trips_every_format(fmt):
    names = sn.parse_sigpwned(CSV_TEXT, script="localized")[:2]
    out = sn.render(names, fmt)
    if fmt == "text":
        assert out.splitlines() == ["Emma", "Louis"]
    elif fmt == "csv":
        assert out.splitlines()[0] == ",".join(sn.FIELDS)
        assert len(out.splitlines()) == 3
    elif fmt == "json":
        assert [r["name"] for r in json.loads(out)] == ["Emma", "Louis"]
    else:
        assert [json.loads(line)["name"] for line in out.splitlines()] == ["Emma", "Louis"]


def test_render_keeps_non_latin_names_unescaped():
    names = sn.parse_sigpwned(CSV_TEXT, script="localized")
    assert "陽菜" in sn.render([n for n in names if n.name == "陽菜"], "json")


# --- CLI -------------------------------------------------------------------

def test_cli_samples_a_local_csv_to_a_file(tmp_path):
    csv_path = tmp_path / "forenames.csv"
    csv_path.write_text(CSV_TEXT, encoding="utf-8")
    out = tmp_path / "nested" / "sample.jsonl"
    rc = sn.main(["--source", "sigpwned", "--csv-path", str(csv_path),
                  "--n", "3", "--seed", "7", "--format", "jsonl", "--out", str(out)])
    assert rc == 0
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3 and all(r["source"] == "sigpwned" for r in rows)


def test_cli_stratifies_and_caps_without_losing_country_balance(tmp_path, capsys):
    csv_path = tmp_path / "forenames.csv"
    csv_path.write_text(CSV_TEXT, encoding="utf-8")
    sn.main(["--source", "sigpwned", "--csv-path", str(csv_path),
             "--per-country", "1", "--n", "2", "--seed", "7", "--format", "csv"])
    rows = capsys.readouterr().out.strip().splitlines()
    assert len(rows) == 3  # header + the 2 the cap allowed
    assert len({r.split(",")[2] for r in rows[1:]}) == 2  # from 2 distinct countries


def test_cli_restricts_to_requested_countries(tmp_path, capsys):
    csv_path = tmp_path / "forenames.csv"
    csv_path.write_text(CSV_TEXT, encoding="utf-8")
    sn.main(["--source", "sigpwned", "--csv-path", str(csv_path),
             "--countries", "jp", "--n", "5", "--seed", "1", "--format", "csv"])
    countries = {r.split(",")[2] for r in capsys.readouterr().out.strip().splitlines()[1:]}
    assert countries == {"JP"}


def test_cli_warns_about_countries_the_dataset_does_not_cover(tmp_path, capsys):
    csv_path = tmp_path / "forenames.csv"
    csv_path.write_text(CSV_TEXT, encoding="utf-8")
    sn.main(["--source", "sigpwned", "--csv-path", str(csv_path),
             "--countries", "FR,NG", "--n", "2", "--seed", "1"])
    assert "NG is not in this dataset" in capsys.readouterr().err


def test_cli_fails_loudly_when_the_filters_match_nothing(tmp_path):
    csv_path = tmp_path / "forenames.csv"
    csv_path.write_text(CSV_TEXT, encoding="utf-8")
    with pytest.raises(SystemExit, match="no names matched"):
        sn.main(["--source", "sigpwned", "--csv-path", str(csv_path),
                 "--countries", "ZZ", "--n", "5"])
