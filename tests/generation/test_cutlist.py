"""The cut list export: spec values formatted, never recomputed."""

import csv
import io
from pathlib import Path

from shop_drawings import FurnitureSpec
from shop_drawings.generation.config import GenerationConfig
from shop_drawings.generation.cutlist import (
    COLUMNS,
    build_rows,
    fmt_dimension,
    render_csv,
    write_cut_list,
)
from shop_drawings.generation.geometry import resolve_part


def test_whole_millimeters_render_without_a_decimal_point() -> None:
    assert fmt_dimension(720.0) == "720"
    assert fmt_dimension(18.5) == "18.5"


def test_the_fixture_cut_list_has_one_row_per_part_in_spec_order(
    approved_spec: FurnitureSpec,
) -> None:
    plans = [resolve_part(part) for part in approved_spec.parts]

    rows = build_rows(approved_spec, plans)

    assert len(rows) == 1
    row = rows[0]
    assert row.part_id == "side_left"
    assert row.part_type == "panel"
    assert row.quantity == 1
    assert row.material == "18mm birch plywood"
    assert (row.length, row.width, row.thickness) == ("720", "380", "18")
    assert row.edge_banding == "front"
    assert row.features == "21 drill ⌀5"
    assert row.source_page == "1"


def test_csv_is_quote_all_with_crlf_and_a_stable_column_order(
    approved_spec: FurnitureSpec,
) -> None:
    plans = [resolve_part(part) for part in approved_spec.parts]

    text = render_csv(build_rows(approved_spec, plans))

    assert text.endswith("\r\n")
    parsed = list(csv.reader(io.StringIO(text)))
    assert parsed[0] == list(COLUMNS)
    assert all(len(row) == len(COLUMNS) for row in parsed)
    assert text.startswith('"part_id","type","quantity"')


def test_the_written_cut_list_round_trips_through_csv(
    approved_spec: FurnitureSpec, default_config: GenerationConfig, tmp_path: Path
) -> None:
    plans = [resolve_part(part) for part in approved_spec.parts]

    path = write_cut_list(approved_spec, plans, default_config, tmp_path)

    assert path.name == "entry-cabinet-001_cutlist.csv"
    parsed = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    header, data = parsed[0], parsed[1:]
    assert header == list(COLUMNS)
    assert len(data) == 1
    row = dict(zip(header, data[0], strict=True))
    assert row["part_id"] == "side_left"
    assert row["length"] == "720"
