"""`qualify config import` на синтетических файлах той же разметки, что и внутренние.

Числа здесь ВЫМЫШЛЕННЫЕ — настоящие файлы компании в репозиторий не кладутся.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

openpyxl = pytest.importorskip("openpyxl")

from qualifier import cli  # noqa: E402
from qualifier.config import load_cpa, load_criteria, load_volume_criteria  # noqa: E402
from qualifier.importer import (  # noqa: E402
    ImportProblem,
    import_cpa,
    import_guidelines,
    import_roi,
    parse_amount,
    tier_mismatches,
)


def make_cpa_xlsx(path: Path) -> Path:
    wb = openpyxl.Workbook()
    old = wb.active
    old.title = "Q1 CP1T"
    old.append([None, "Country", "Organic New User", "Affiliates CPA"])
    old.append([None, "EG", 40, 20])
    new = wb.create_sheet("Q2 CP1T")
    new.append([])
    new.append([None, None, None, "FIRST TRADE", None])
    new.append([None, "Country", "Region", "CPA Retail", "CPA Referral Pro (Affiliates)"])
    new.append([None, "EG", "MENA", 18, 13])
    new.append([None, "KZ", "CIS", 25, 17])
    new.append([None, "XYZ", "bad", 1, 1])          # не ISO2 — пропуск
    new.append([None, "BR", "LATAM", 9, None])       # нет ставки — пропуск
    wb.save(path)
    return path


def make_roi_xlsx(path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A3"] = "Affiliate Tier based on 3 Month Evaluation"
    for col, title in zip("ABCDEF", ["Spot Tier", "Spot Trade Volume", "New Traders",
                                     "Futures Tier", "Futures Trade Volume", "New Traders"]):
        ws[f"{col}4"] = title
    rows = [(0.25, 300000, 4, 0.25, 2000000, 4), (0.35, 3000000, 12, 0.35, 20000000, 12),
            (0.45, 24000000, 20, 0.45, 300000000, 20)]
    for i, row in enumerate(rows, start=5):
        for col, value in zip("ABCDEF", row):
            ws[f"{col}{i}"] = value
    ws["A10"], ws["B10"] = "Spot Reached Tier", "=IF(B22*3>=B7,A7,IF(B22*3>=B6,A6,IF(B22*3>=B5,A5,0.15)))"
    ws["A11"], ws["B11"] = "Futures Reached Tier", "=IF(B23*3>=E7,D7,IF(B23*3>=E6,D6,IF(B23*3>=E5,D5,0.08)))"
    ws["A16"], ws["B16"] = "Avg Spot Taker Volume Percentage", 0.55
    ws["A17"], ws["B17"] = "Avg Futures Taker Volume Percentage", 0.65
    ours = [("Spot Taker Rate", 0.0006), ("Spot Mater Rate", 0.0003),     # опечатка как в шаблоне
            ("Futures Taker Rate", 0.0004), ("Futures Maker Rate", 0.00015)]
    theirs = [("Spot Taker Rate", 0.0009), ("Spot Mater Rate", 0.0007),
              ("Futures Taker Rate", 0.0005), ("Futures Maker Rate", 0.00025)]
    for i, ((l1, v1), (l2, v2)) in enumerate(zip(ours, theirs), start=16):
        ws[f"C{i}"], ws[f"D{i}"], ws[f"F{i}"], ws[f"G{i}"] = l1, v1, l2, v2
    wb.save(path)
    return path


def _cell(text: str) -> str:
    return f"<w:tc><w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:tc>"


def _table(rows: list[list[str]]) -> str:
    return "<w:tbl>" + "".join("<w:tr>" + "".join(_cell(c) for c in row) + "</w:tr>" for row in rows) + "</w:tbl>"


def make_guidelines_docx(path: Path) -> Path:
    auto = _table([
        ["Tiers", "Spot", "And/Or", "Futures"],
        ["", "3 Months New Traders", "3_Month_Trading_Vol($)", "Rate", "Or", "3 Months New Trades", "3_Month_Trading_Vol($)", "Rate"],
        ["Tier 1", "4", "0.3M", "25%", "", "4", "2M", "25%"],
        ["Tier 2", "12", "3M", "35%", "", "12", "20M", "35%"],
        ["Tier 3", "20", "24M", "45%", "", "20", "300M", "45%"],
        ["Invitee VIP Limits", "VIP < 6", "", "VIP < 5"],
    ])
    rules = _table([
        ["Criteria", "Achievement", "Eligibility"],
        ["Trading Volume", "100% Achievement of Trading Volume Criteria and 70% of new traders criteria",
         "Eligible for up to 3 months of the relevant commissions tier."],
        ["", "100% Achievement of Trading Volume criteria, although the new traders criteria have not been met; but top 10% FTT of its region", ""],
        ["", "≥ 85% Achievement of Trading Volume Criteria and 55% of new traders criteria.",
         "Eligible for up to 1 month of the relevant commissions tier."],
        ["New Trader", "100% Achievement of New Trader Criteria and 70% of Trading Volume criteria",
         "Eligible for up to 3 months of the relevant commissions tier."],
    ])
    social = _table([
        ["Affiliate Tier", "Affiliate Type", "Profile Criteria"],
        ["45%", "Institutional Affiliate (website, agencies)", "Social media account with 25,000+ followers or subscribers on one or more platforms"],
        ["", "Individual Affiliates", "Social media account with 12,000+ followers or subscribers on one or more platforms"
                                      "Community of 9,000+ members on one or more community groups"
                                      "400k+ followers with 40k average views per post"],
        ["35%", "Institutional Affiliate", "Social media account with 14,000+ followers or subscribers"],
        ["", "Individual Affiliates", "Social media account with 7,000+ followers or subscribers"
                                      "Community of 5,000+ members on one or more community groups"],
    ])
    body = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            f"{auto}{rules}{social}</w:body></w:document>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", body)
    return path


@pytest.fixture
def files(tmp_path):
    return {
        "cpa": make_cpa_xlsx(tmp_path / "cpa.xlsx"),
        "roi": make_roi_xlsx(tmp_path / "roi.xlsx"),
        "guidelines": make_guidelines_docx(tmp_path / "guidelines.docx"),
    }


def test_parse_amount():
    assert parse_amount("0.3M") == 300000
    assert parse_amount("500M") == 5e8
    assert parse_amount("$1,000,000") == 1e6
    assert parse_amount("40k") == 40000
    assert parse_amount(None) is None


def test_import_cpa_picks_latest_quarter(files):
    result = import_cpa(files["cpa"])
    assert result.sheet == "Q2 CP1T"
    assert result.column == "CPA Referral Pro (Affiliates)"
    assert {r["country_code"]: r["cpa"] for r in result.rows} == {"EG": 13, "KZ": 17}
    assert result.rows[0]["region"] == "MENA" and result.rows[0]["event"] == "FTT"
    assert result.skipped == 1
    older = import_cpa(files["cpa"], sheet="Q1 CP1T")
    assert older.column == "Affiliates CPA" and older.rows[0]["cpa"] == 20


def test_import_cpa_bad_sheet(files):
    with pytest.raises(ImportProblem, match="нет листа"):
        import_cpa(files["cpa"], sheet="Q9")


def test_import_roi(files):
    roi = import_roi(files["roi"])
    assert roi["evaluation_months"] == 3
    spot = roi["markets"]["spot"]
    assert spot["default_rate"] == 15 and roi["markets"]["futures"]["default_rate"] == 8
    assert spot["tiers"][1] == {"rate": 35, "volume": 3000000, "new_traders": 12}
    assert roi["taker_share"] == {"spot": 0.55, "futures": 0.65}
    assert roi["fees"]["ours"]["spot_maker"] == 0.0003           # «Spot Mater Rate» тоже понят
    assert roi["fees"]["competitor"]["futures_maker"] == 0.00025


def test_import_guidelines(files):
    g = import_guidelines(files["guidelines"])
    assert g.invitee_limits == {"spot": "VIP < 6", "futures": "VIP < 5"}
    assert g.volume_tiers["futures"][2] == {"rate": 45, "volume": 3e8, "new_traders": 20}
    assert {"primary_pct": 100, "secondary_pct": 70, "months": 3} in g.eligibility
    assert {"primary_pct": 100, "secondary_pct": 0, "months": 3, "requires_top_ftt": True} in g.eligibility
    assert {"primary_pct": 85, "secondary_pct": 55, "months": 1} in g.eligibility
    assert len(g.eligibility) == 3                                 # правила для объёма и трейдеров совпадают
    individual = g.social["individual"]["tiers"]
    assert individual[0] == {"rate": 45, "followers_single_platform": 12000, "community_members": 9000,
                             "followers_with_views": {"followers": 400000, "avg_views": 40000}}
    assert g.social["institutional"]["tiers"][1] == {"rate": 35, "followers_single_platform": 14000}


def test_mismatch_between_roi_and_guidelines(files):
    roi = import_roi(files["roi"])
    g = import_guidelines(files["guidelines"])
    assert tier_mismatches(roi, g) == []
    g.volume_tiers["spot"][0]["volume"] = 999
    assert "spot 25%" in tier_mismatches(roi, g)[0]


def test_cli_import_writes_gitignored_configs(files, tmp_path, capsys):
    config_dir = tmp_path / "cfg"
    args = ["--env", str(tmp_path / "none.env"), "--config-dir", str(config_dir), "config", "import",
            "--cpa", str(files["cpa"]), "--roi", str(files["roi"]), "--guidelines", str(files["guidelines"])]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "2 стран" in out and "тиров spot 3, futures 3" in out

    vc = load_volume_criteria(config_dir)
    assert not vc.is_example and vc.markets["spot"].invitee_limit == "VIP < 6"
    assert [r.months for r in vc.eligibility] == [3, 3, 1]
    criteria = load_criteria(config_dir)
    assert not criteria.is_example
    assert criteria.affiliate_types["individual"].tiers[0].views_combo == (400000, 40000)
    cpa = load_cpa(config_dir)
    assert cpa.lookup("EG", None)[0].rate == 13
    assert "ВНУТРЕННИЕ ДАННЫЕ" in (config_dir / "volume_criteria.yaml").read_text(encoding="utf-8")

    # без --force существующие файлы не трогаем
    assert cli.main(args) == 1
    assert "не трогаю" in capsys.readouterr().err
    assert cli.main(args + ["--force"]) == 0


def test_cli_import_needs_files(tmp_path, capsys):
    assert cli.main(["--config-dir", str(tmp_path), "config", "import"]) == 2
    assert "Укажите хотя бы один файл" in capsys.readouterr().err


def test_gitignore_covers_internal_files():
    text = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    for name in ("config/qualifier/criteria.yaml", "config/qualifier/cpa_by_country.csv",
                 "config/qualifier/volume_criteria.yaml"):
        assert name in text
