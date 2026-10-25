"""The query builder is the only thing allowed to produce ADQL, and it cannot be talked into injection."""
import pytest
from pydantic import ValidationError

from storyteller.query import Filter, SearchIntent, build_adql, parse_rules, run_local

COLUMNS = ["pl_name", "pl_eqt", "disc_year", "discoverymethod", "sy_dist", "pl_rade", "pl_orbper"]


def test_hottest_planet_of_2020():
    intent = parse_rules("the hottest planet discovered in 2020")
    assert any(f.field == "disc_year" and f.op == "=" and f.value == 2020 for f in intent.filters)
    assert intent.sort_field == "pl_eqt" and intent.sort_dir == "desc"
    sql = build_adql(intent, COLUMNS)
    assert "disc_year = 2020" in sql
    assert "order by pl_eqt desc" in sql
    assert " top " not in sql.lower()
    assert "drop" not in sql.lower()


def test_liquid_water_is_a_labelled_proxy():
    intent = parse_rules("a world that might hold liquid water")
    fields = {f.field: f for f in intent.filters}
    assert fields["pl_eqt"].op == "between"
    assert fields["pl_eqt"].value == 180 and fields["pl_eqt"].value2 == 310
    assert fields["pl_rade"].op == "<="
    assert any("no water data" in n.lower() for n in intent.notes)


def test_light_years_are_converted_only_inside_the_query():
    intent = parse_rules("a Jupiter-sized world within 50 light-years")
    dist = next(f for f in intent.filters if f.field == "sy_dist")
    assert dist.op == "<"
    assert 15.0 < dist.value < 15.5  # 50 / 3.26156
    assert any("parsec" in n.lower() for n in intent.notes)
    sql = build_adql(intent, COLUMNS)
    assert "light" not in sql


def test_user_text_cannot_become_sql():
    nasty = "hottest planet discovered in 2020; drop table pscomppars; select * from secrets --"
    intent = parse_rules(nasty)
    sql = build_adql(intent, COLUMNS)
    for forbidden in ("drop", "secrets", "select *", "--", ";"):
        assert forbidden not in sql.lower()
    assert "disc_year = 2020" in sql


def test_schema_rejects_unknown_fields_and_operators():
    with pytest.raises(ValidationError):
        Filter(field="pl_name; drop table", op="=", value=1)
    with pytest.raises(ValidationError):
        Filter(field="pl_eqt", op="!=", value=1)
    with pytest.raises(ValidationError):
        SearchIntent(method="Imagination")
    with pytest.raises(ValidationError):
        Filter(field="pl_eqt", op=">", value=float("inf"))


def test_columns_are_allowlisted():
    intent = SearchIntent(sort_field="pl_eqt", sort_dir="desc", limit=3)
    with pytest.raises(ValueError):
        build_adql(intent, ["pl_eqt", "pl_name; drop table pscomppars"])


def test_between_is_ordered_and_local_runner_matches():
    intent = parse_rules("planets discovered between 2018 and 2020")
    year = next(f for f in intent.filters if f.field == "disc_year")
    assert year.op == "between" and year.value == 2018 and year.value2 == 2020
    catalog = [
        {"pl_name": "A", "disc_year": 2017, "pl_eqt": 100, "discoverymethod": "Transit"},
        {"pl_name": "B", "disc_year": 2019, "pl_eqt": 900, "discoverymethod": "Transit"},
        {"pl_name": "C", "disc_year": 2020, "pl_eqt": 400, "discoverymethod": "Imaging"},
        {"pl_name": "D", "disc_year": 2021, "pl_eqt": 100, "discoverymethod": "Transit"},
    ]
    names = [r["pl_name"] for r in run_local(intent, catalog)]
    assert names == ["C", "B"]  # newest discovery year first


def test_method_filter_is_a_quoted_enum_value():
    intent = parse_rules("the nearest planet found by direct imaging")
    assert intent.method == "Imaging"
    assert intent.sort_field == "sy_dist" and intent.sort_dir == "asc"
    sql = build_adql(intent, COLUMNS)
    assert "discoverymethod = 'Imaging'" in sql
    assert "order by sy_dist asc" in sql
