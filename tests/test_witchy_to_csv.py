"""WitchyBND param XML -> CSV conversion."""

import csv
from pathlib import Path

import pytest

from pipeline.witchy_to_csv import convert, read_param_xml

XML = """<?xml version="1.0" encoding="utf-8"?>
<param WitchyVersion="2150000">
<filename>TestParam.param</filename>
<fields>
<field name="eventflagId" type="u32" arraylength="1" bitsize="-1" defaultValue="76936" />
<field name="pad4" type="dummy8" arraylength="2" bitsize="-1" defaultValue="[0|0]" />
<field name="posX" type="f32" arraylength="1" bitsize="-1" defaultValue="0" />
</fields>
<rows>
<row id="0" eventflagId="200" posX="0.39" />
<row id="100001" paramdexName="[Stormhill] Margit, the Fell Omen" posX="-28.55" />
</rows>
</param>
"""


def write_xml(tmp_path: Path, body: str = XML, name: str = "TestParam") -> Path:
    path = tmp_path / f"{name}.param.xml"
    path.write_text(body, encoding="utf-8")
    return path


def test_defaults_filled_padding_dropped_names_kept(tmp_path: Path) -> None:
    columns, rows = read_param_xml(write_xml(tmp_path))
    assert columns == ["ID", "Name", "eventflagId", "posX"]
    assert rows[0] == {"ID": "0", "Name": "", "eventflagId": "200", "posX": "0.39"}
    # eventflagId left out of the row -> field default.
    assert rows[1]["eventflagId"] == "76936"
    assert rows[1]["Name"] == "[Stormhill] Margit, the Fell Omen"


def test_convert_writes_csv(tmp_path: Path) -> None:
    write_xml(tmp_path)
    out = tmp_path / "out"
    assert convert(tmp_path, ["TestParam"], out) == {"TestParam": 2}
    with (out / "TestParam.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[1]["Name"] == "[Stormhill] Margit, the Fell Omen"


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    body = XML.replace('<row id="100001"', '<row id="0"')
    with pytest.raises(ValueError, match="duplicate"):
        read_param_xml(write_xml(tmp_path, body))


def test_non_param_xml_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no <field>"):
        read_param_xml(write_xml(tmp_path, "<param><rows /></param>"))
