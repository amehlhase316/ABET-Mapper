from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile


ABET_OUTCOMES = {
    "ABET-1": "An ability to identify, formulate, and solve complex engineering problems by applying principles of engineering, science, and mathematics.",
    "ABET-2": "An ability to apply engineering design to produce solutions that meet specified needs with consideration of public health, safety, and welfare, as well as global, cultural, social, environmental, and economic factors.",
    "ABET-3": "An ability to communicate effectively with a range of audiences.",
    "ABET-4": "An ability to recognize ethical and professional responsibilities in engineering situations and make informed judgments, which must consider the impact of engineering solutions in global, economic, environmental, and societal contexts.",
    "ABET-5": "An ability to function effectively on a team whose members together provide leadership, create a collaborative environment, establish goals, plan tasks, and meet objectives.",
    "ABET-6": "An ability to develop and conduct appropriate experimentation, analyze and interpret data, and use engineering judgment to draw conclusions.",
    "ABET-7": "An ability to acquire and apply new knowledge as needed, using appropriate learning strategies.",
    "SER-1": "",
    "SER-2": "",
}

PROGRAM_ATTAINMENT_TARGET = 70


def write_attainment_report(
    output_path: Path,
    course: dict[str, Any],
    outcomes: list[dict[str, Any]],
    results: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> None:
    """Write an anonymized ABET attainment workbook.

    This function is intentionally isolated from Canvas API and calculation logic.
    The calling code passes already-calculated results and course/outcome metadata.
    """
    sheets = report_sheets(course, outcomes, results, metadata or {})

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output_path, "w", ZIP_DEFLATED) as archive:
        write_static_workbook_files(archive, sheets)


def write_attainment_html(
    output_path: Path,
    course: dict[str, Any],
    outcomes: list[dict[str, Any]],
    results: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> None:
    """Write the same report content as a standalone, collapsible HTML file."""
    sheets = report_sheets(course, outcomes, results, metadata or {})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_html_document(sheets), encoding="utf-8")


def report_sheets(
    course: dict[str, Any],
    outcomes: list[dict[str, Any]],
    results: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> list[tuple[str, list[list[Any]]]]:
    """Build the shared logical report used by every output formatter."""
    sheets = [
        ("Methodology", methodology_rows(course, outcomes, results, metadata or {})),
        ("Evidence Overview", evidence_overview_rows(outcomes, results)),
    ]
    for index, result in enumerate(results, start=1):
        name = result.get("outcome_name") or f"Outcome {index}"
        sheets.append((safe_sheet_name(f"{name} Detail"), outcome_detail_rows(result)))
    return sheets


def methodology_rows(course: dict[str, Any], outcomes: list[dict[str, Any]], results: list[dict[str, Any]], metadata: dict[str, Any]) -> list[list[Any]]:
    rows = [
        ["ABET Attainment Report"],
        ["Report Context"],
        ["Course", course.get("name") or ""],
        ["Course Code", course.get("course_code") or ""],
        ["Canvas Course ID", course.get("id") or ""],
        ["Section", (course.get("section") or {}).get("name") or "Overall"],
        ["Term", (course.get("term") or {}).get("name") or ""],
        ["Generated", datetime.now(timezone.utc).isoformat()],
        ["Program Target", f"{PROGRAM_ATTAINMENT_TARGET}% overall attainment"],
        ["Student Data", "Anonymized as S1, S2, S3, etc. Student names are not included in this workbook."],
        ["Attainment Method"],
        ["Calculation Summary", "Each selected Canvas evidence item counts as one KPI. Evidence can be a whole assessment score or a rubric criterion."],
        ["Item Attainment", "Each evidence item is classified as Attains, Does Not Meet, or Unknown. An item uses its own Attains threshold when specified; otherwise it uses the outcome threshold."],
        ["Thresholds", "Attains thresholds are inclusive, so a score equal to the threshold is counted as Attains. Instructors may lower an individual evidence item's threshold when appropriate."],
        ["Evidence Status Rules"],
        ["Missing or Excused", "When an assessment has no points and Canvas marks it Missing or Excused, including through a late-policy status, the evidence is Unknown."],
        ["No Submitted Work", "When Canvas contains no evidence that the student submitted the assessment, no points or a recorded zero are treated as Unknown."],
        ["Unavailable Scoring Data", "When required assessment or rubric scoring information is unavailable, the evidence is Unknown."],
        ["Zero With Submitted Work", "When Canvas records a zero for work the student actually submitted, the evidence is included as 0% and classified as Does Not Meet."],
        ["Student Rollup When Evidence Is Unknown"],
        ["Attains", "If the student reaches the outcome threshold even when every Unknown item is treated as not attained, the result is Attains."],
        ["Does Not Meet", "If the student remains below the outcome threshold even when every Unknown item is treated as attained, the result is Does Not Meet."],
        ["Unknown", "If the Unknown items could change whether the student reaches the outcome threshold, the result remains Unknown."],
        ["Course Outcome Summary", "The report counts students as Attains, Does Not Meet, or Unknown. The overall attainment percentage is Attains divided by Attains plus Does Not Meet; Unknown students are reported separately."],
        ["Attainment Strategy", report_strategy_label(metadata, results)],
        ["Strategy Question", report_strategy_question(metadata, results)],
        ["Decision Rule", report_strategy_formula(metadata, results)],
        ["Executive Summary"],
        ["Outcome", "Official ABET Wording", "Overall Attained", "Target", "Attains", "Does Not Meet", "Unknown"],
    ]
    outcome_by_name = {normal_outcome_name(outcome.get("name") or ""): outcome for outcome in outcomes}
    for result in results:
        outcome_name = normal_outcome_name(result.get("outcome_name") or "")
        outcome = outcome_by_name.get(outcome_name, {})
        row = [
            outcome_name,
            outcome.get("description") or ABET_OUTCOMES.get(outcome_name, ""),
            attained_text(result),
            f"{PROGRAM_ATTAINMENT_TARGET}%",
        ]
        row.extend(
            [
                kpi_attained_count(result),
                result.get("counts", {}).get("does_not_meet", 0),
                result.get("counts", {}).get("unknown", 0),
            ]
        )
        rows.append(row)
    return rows


def evidence_overview_rows(outcomes: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[list[Any]]:
    rows = [["Evidence Overview"]]
    outcome_by_name = {normal_outcome_name(outcome.get("name") or ""): outcome for outcome in outcomes}
    for result in results:
        outcome_name = normal_outcome_name(result.get("outcome_name") or "")
        outcome = outcome_by_name.get(outcome_name, {})
        summary_counts = [
            "Attains",
            kpi_attained_count_percent(result),
        ]
        summary_counts.extend(
            [
                "Does Not Meet",
                count_percent(result, "does_not_meet"),
                "Unknown",
                count_percent(result, "unknown"),
            ]
        )
        rows.extend(
            [
                [],
                [f"{outcome_name} Evidence Overview"],
                [
                    "Outcome",
                    outcome_name,
                    outcome.get("description") or ABET_OUTCOMES.get(outcome_name, ""),
                    "Overall Attainment",
                    attained_text(result),
                ] + summary_counts,
                [],
                [
                    "Assessment",
                    "Evidence Item",
                    "Attains Threshold",
                    "Attains",
                    "Does Not Meet",
                    "Unknown",
                ],
            ]
        )
        for item in result.get("criterion_stats") or []:
            rows.append(
                [
                    item.get("assignment_name") or "",
                    evidence_item_description(item),
                    item.get("meets_threshold") or result.get("meets_threshold"),
                    item_count_percent(item, "meets"),
                    item_count_percent(item, "does_not_meet"),
                    item_count_percent(item, "unknown"),
                ]
            )
    return rows


def outcome_detail_rows(result: dict[str, Any]) -> list[list[Any]]:
    rows = [
        [result.get("outcome_name") or ""],
        ["Attainment Strategy", "KPI count attainment"],
        ["Attains Threshold", result.get("meets_threshold")],
        ["Overall Attained", attained_text(result)],
        [],
        ["Evidence Summary"],
        ["Assessment", "Evidence Item", "Attains Threshold", "Attains", "Does Not Meet", "Unknown"],
    ]
    for item in result.get("criterion_stats") or []:
        rows.append(
            [
                item.get("assignment_name") or "",
                evidence_item_description(item),
                item.get("meets_threshold") or result.get("meets_threshold"),
                item_count_percent(item, "meets"),
                item_count_percent(item, "does_not_meet"),
                item_count_percent(item, "unknown"),
            ]
        )

    criteria = result.get("criterion_stats") or []
    rows.extend([[], ["Anonymized Student Evidence"]])
    rows.append(["Student", "KPI Attainment", "Category"] + [evidence_item_description(criterion) for criterion in criteria])
    for index, student in enumerate(result.get("students") or [], start=1):
        detail_by_key = {
            str(detail.get("criterion_id") or f"{detail.get('assignment_id')}:assignment-score"): detail
            for detail in student.get("details") or []
        }
        rows.append(
            [
                f"S{index}",
                student_attainment_value(student),
                student_category_label(student),
            ]
            + [student_detail_value(detail_by_key.get(criterion.get("criterion_key"))) for criterion in criteria]
        )
    return rows


def student_detail_value(detail: dict[str, Any] | None) -> str:
    if not detail:
        return ""
    if not detail.get("included"):
        return "X"
    if detail.get("score_percent") is None:
        return ""
    return f"{detail.get('score_percent')}% ({category_symbol(detail.get('category_key'))})"


def student_attainment_value(student: dict[str, Any]) -> str:
    minimum = student.get("attainment_min_percent")
    maximum = student.get("attainment_max_percent")
    if minimum is not None and maximum is not None:
        if minimum == maximum:
            return f"{minimum}%"
        return f"{minimum}–{maximum}%"
    score = student.get("score_percent")
    return "" if score is None else f"{score}%"


def evidence_item_description(item: dict[str, Any]) -> str:
    description = str(item.get("description") or "")
    if description.lower() == "whole assignment score":
        return "Whole assessment score"
    return description


def count_percent(result: dict[str, Any], key: str) -> str:
    return f"{result.get('counts', {}).get(key, 0)} ({result.get('percentages', {}).get(key, 0)}%)"


def kpi_attained_count(result: dict[str, Any]) -> int:
    counts = result.get("counts", {})
    return int(counts.get("meets", 0) or 0)


def kpi_attained_count_percent(result: dict[str, Any]) -> str:
    percentages = result.get("percentages", {})
    percent = round(float(percentages.get("meets", 0) or 0), 1)
    return f"{kpi_attained_count(result)} ({percent}%)"


def item_count_percent(item: dict[str, Any], key: str) -> str:
    return f"{item.get('counts', {}).get(key, 0)} ({item.get('percentages', {}).get(key, 0)}%)"


def student_category_label(student: dict[str, Any]) -> str:
    key = student.get("category_key")
    if key == "meets":
        return "Attains"
    return student.get("category") or ""


def attained_text(result: dict[str, Any]) -> str:
    return f"{result.get('overall_attained_count', 0)}/{result.get('overall_known_count', 0)} ({result.get('overall_attained_percent', 0)}%)"


def report_strategy_label(metadata: dict[str, Any], results: list[dict[str, Any]]) -> str:
    return "KPI count attainment"


def report_strategy_question(metadata: dict[str, Any], results: list[dict[str, Any]]) -> str:
    return "Is attainment certain, non-attainment certain, or is the result still indeterminate because of Unknown evidence?"


def report_strategy_formula(metadata: dict[str, Any], results: list[dict[str, Any]]) -> str:
    return "Attains if the threshold is already reached; Does Not Meet if the threshold cannot be reached; otherwise Unknown."


def category_symbol(category_key: Any) -> str:
    return {
        "meets": "A",
        "does_not_meet": "D",
        "unknown": "X",
    }.get(str(category_key or ""), "")


def normal_outcome_name(value: str) -> str:
    ser_match = re.search(r"SER\s*-?\s*([12])", value, re.IGNORECASE)
    if ser_match:
        return f"SER-{ser_match.group(1)}"
    match = re.search(r"ABET\s*-?\s*([1-7])", value, re.IGNORECASE)
    if match:
        return f"ABET-{match.group(1)}"
    stripped = value.strip()
    if stripped in {str(number) for number in range(1, 8)}:
        return f"ABET-{stripped}"
    return stripped


def safe_sheet_name(value: str) -> str:
    value = re.sub(r"[][\\\\/*?:]", "", value)[:31].strip()
    return value or "Outcome Detail"


def report_html_document(sheets: list[tuple[str, list[list[Any]]]]) -> str:
    rendered_sections = []
    for index, (name, rows) in enumerate(sheets):
        content = rows_html(rows, name)
        if index < 2:
            rendered_sections.append(f'<section class="report-section">{content}</section>')
        else:
            rendered_sections.append(
                f'<details class="detail-section"><summary>{xml_escape(name)}</summary>{content}</details>'
            )
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ABET Attainment Report</title>
<style>
:root {{ font-family: Arial, Helvetica, sans-serif; color: #1f2933; background: #f4f7fa; }}
body {{ margin: 0; }}
main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
.report-section, .detail-section {{ margin: 0 0 24px; padding: 22px; border: 1px solid #d9e2ec; border-radius: 10px; background: white; box-shadow: 0 2px 8px rgba(31, 95, 139, .06); }}
h1 {{ margin: 0 0 20px; color: #1f5f8b; font-size: 30px; }}
h2 {{ margin: 24px 0 10px; color: #1f5f8b; font-size: 20px; }}
h2.outcome-heading {{ margin: 30px -22px 12px; padding: 14px 22px; color: white; background: #1f5f8b; font-size: 23px; }}
table {{ width: 100%; margin: 0 0 16px; border-collapse: collapse; }}
th, td {{ padding: 9px 10px; border: 1px solid #d9e2ec; text-align: left; vertical-align: top; }}
th {{ color: white; background: #1f5f8b; }}
tr.outcome-summary th {{ color: #1f2933; background: #eaf4fb; }}
td.label {{ width: 205px; font-weight: 700; background: #eaf4fb; }}
.attains {{ background: #d9ead3; }}
.does-not-meet {{ background: #f4cccc; }}
.unknown {{ background: #d9d9d9; }}
.detail-section > summary {{ cursor: pointer; color: #1f5f8b; font-size: 20px; font-weight: 700; }}
.detail-section[open] > summary {{ margin-bottom: 18px; }}
@media (max-width: 760px) {{ main {{ padding: 12px; }} .report-section, .detail-section {{ padding: 14px; overflow-x: auto; }} h2.outcome-heading {{ margin-left: -14px; margin-right: -14px; }} }}
@media print {{ :root {{ background: white; }} main {{ max-width: none; padding: 0; }} .report-section, .detail-section {{ box-shadow: none; break-inside: avoid; }} .detail-section {{ display: block; }} .detail-section > summary {{ display: none; }} .detail-section:not([open]) > *:not(summary) {{ display: block; }} }}
</style>
</head>
<body><main>{''.join(rendered_sections)}</main></body>
</html>'''


def rows_html(rows: list[list[Any]], section_name: str) -> str:
    rendered: list[str] = []
    table_rows: list[str] = []
    active_header: list[Any] = []

    def flush_table() -> None:
        if table_rows:
            rendered.append(f"<table>{''.join(table_rows)}</table>")
            table_rows.clear()

    for index, row in enumerate(rows):
        if not row:
            flush_table()
            continue
        if len(row) == 1:
            flush_table()
            text = str(row[0] or "")
            if index == 0:
                rendered.append(f"<h1>{xml_escape(text)}</h1>")
            else:
                css_class = ' class="outcome-heading"' if is_evidence_outcome_heading(row) else ""
                rendered.append(f"<h2{css_class}>{xml_escape(text)}</h2>")
            continue
        header_row = is_table_header(row) or is_outcome_block_header(row)
        if is_table_header(row):
            active_header = row
        row_class = ' class="outcome-summary"' if is_outcome_block_header(row) else ""
        cells = []
        for column_index, value in enumerate(row):
            text = str(value if value is not None else "")
            tag = "th" if header_row else "td"
            classes = []
            if not header_row and len(row) == 2 and column_index == 0:
                classes.append("label")
            header = str(active_header[column_index] or "") if column_index < len(active_header) else ""
            category_class = html_category_class(text, header)
            if category_class:
                classes.append(category_class)
            class_attr = f' class="{" ".join(classes)}"' if classes else ""
            cells.append(f"<{tag}{class_attr}>{xml_escape(text)}</{tag}>")
        table_rows.append(f"<tr{row_class}>{''.join(cells)}</tr>")
    flush_table()
    return "".join(rendered)


def html_category_class(text: str, header: str) -> str:
    if header in {"Attains", "Overall Attained"} or text == "Attains" or text.endswith("(A)"):
        return "attains"
    if header == "Does Not Meet" or text == "Does Not Meet" or text.endswith("(D)"):
        return "does-not-meet"
    if header == "Unknown" or text in {"Unknown", "X"}:
        return "unknown"
    return ""


def write_static_workbook_files(archive: ZipFile, sheets: list[tuple[str, list[list[Any]]]]) -> None:
    archive.writestr("[Content_Types].xml", content_types_xml(len(sheets)))
    archive.writestr("_rels/.rels", root_rels_xml())
    archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml(len(sheets)))
    archive.writestr("xl/workbook.xml", workbook_xml([name for name, _rows in sheets]))
    archive.writestr("xl/styles.xml", styles_xml())
    for index, (_name, rows) in enumerate(sheets, start=1):
        archive.writestr(f"xl/worksheets/sheet{index}.xml", sheet_xml(rows))


def content_types_xml(sheet_count: int) -> str:
    sheet_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, sheet_count + 1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
{sheet_overrides}
</Types>'''


def root_rels_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''


def workbook_rels_xml(sheet_count: int) -> str:
    relationships = "".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, sheet_count + 1)
    )
    relationships += f'<Relationship Id="rId{sheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{relationships}</Relationships>'''


def workbook_xml(sheet_names: list[str]) -> str:
    sheets = "".join(
        f'<sheet name="{xml_escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>{sheets}</sheets>
</workbook>'''


def styles_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="6">
<font><sz val="11"/><color rgb="FF1F2933"/><name val="Calibri"/></font>
<font><b/><sz val="20"/><color rgb="FF1F5F8B"/><name val="Calibri"/></font>
<font><b/><sz val="12"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><color rgb="FF1F2933"/><name val="Calibri"/></font>
<font><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
<font><b/><sz val="16"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
</fonts>
<fills count="8">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF1F5F8B"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFEAF4FB"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFD9EAD3"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFFFF2CC"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFF4CCCC"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFD9D9D9"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="3">
<border><left/><right/><top/><bottom/><diagonal/></border>
<border><left style="thin"><color rgb="FFD9E2EC"/></left><right style="thin"><color rgb="FFD9E2EC"/></right><top style="thin"><color rgb="FFD9E2EC"/></top><bottom style="thin"><color rgb="FFD9E2EC"/></bottom><diagonal/></border>
<border><bottom style="medium"><color rgb="FF1F5F8B"/></bottom></border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="11">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="1" fillId="0" borderId="2" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment vertical="center"/></xf>
<xf numFmtId="0" fontId="2" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="3" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment vertical="center"/></xf>
<xf numFmtId="0" fontId="3" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
<xf numFmtId="0" fontId="3" fillId="5" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
<xf numFmtId="0" fontId="3" fillId="6" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
<xf numFmtId="0" fontId="3" fillId="7" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
<xf numFmtId="0" fontId="5" fillId="2" borderId="2" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def sheet_xml(rows: list[list[Any]]) -> str:
    rendered_rows = []
    merged_cells = []
    active_header: list[Any] = []
    for row_index, row in enumerate(rows, start=1):
        if is_table_header(row):
            active_header = row
        cells = "".join(
            cell_xml(row_index, column_index, value, cell_style(row, row_index, column_index, value, active_header))
            for column_index, value in enumerate(row, start=1)
        )
        height = row_height(row)
        rendered_rows.append(f'<row r="{row_index}"{height}>{cells}</row>')
        if is_evidence_outcome_heading(row):
            merged_cells.append(f'<mergeCell ref="A{row_index}:G{row_index}"/>')
    column_count = max((len(row) for row in rows), default=1)
    merges = f'<mergeCells count="{len(merged_cells)}">{"".join(merged_cells)}</mergeCells>' if merged_cells else ""
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
{column_widths_xml(column_count)}
<sheetData>{''.join(rendered_rows)}</sheetData>{merges}
</worksheet>'''


def cell_xml(row: int, column: int, value: Any, style: int = 0) -> str:
    reference = f"{column_name(column)}{row}"
    if value is None:
        value = ""
    style_attr = f' s="{style}"' if style else ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}"{style_attr}><v>{value}</v></c>'
    return f'<c r="{reference}"{style_attr} t="inlineStr"><is><t>{xml_escape(str(value))}</t></is></c>'


def cell_style(row: list[Any], row_index: int, column_index: int, value: Any, active_header: list[Any]) -> int:
    text = str(value or "")
    first = str(row[0] or "") if row else ""
    if row_index == 1 and len(row) == 1:
        return 1
    if is_evidence_outcome_heading(row):
        return 10
    if is_outcome_block_header(row):
        if column_index == 5:
            return 6 if outcome_attainment_is_ok(text) else 8
        if text in {"Attains", "Does Not Meet", "Unknown"}:
            return 2
        if column_index in {7, 9, 11}:
            return category_column_style(str(row[column_index - 2] or ""))
        return 3
    if is_table_header(row):
        return 2
    if len(row) == 1 and first:
        return 5
    header = str(active_header[column_index - 1] or "") if column_index <= len(active_header) else ""
    if header == "Category":
        return category_style(text)
    if header == "Overall Attained" and outcome_attainment_percent(text) is not None:
        return 7 if outcome_attainment_is_ok(text) else 8
    if header in {"Attains", "Overall Attained"}:
        return 7
    if header == "Does Not Meet":
        return 8
    if header == "Unknown":
        return 9
    category = category_style(text)
    if category:
        return category
    if len(row) == 2 and column_index == 1:
        return 3
    if row:
        return 4
    return 0


def is_outcome_block_header(row: list[Any]) -> bool:
    return len(row) >= 5 and row[0] == "Outcome" and row[3] == "Overall Attainment"


def is_evidence_outcome_heading(row: list[Any]) -> bool:
    return len(row) == 1 and str(row[0] or "").endswith(" Evidence Overview")


def outcome_attainment_is_ok(value: str) -> bool:
    percent = outcome_attainment_percent(value)
    return percent is not None and percent >= PROGRAM_ATTAINMENT_TARGET


def outcome_attainment_percent(value: str) -> float | None:
    match = re.search(r"\(([\d.]+)%\)", value)
    if not match:
        return None
    return float(match.group(1))


def category_column_style(label: str) -> int:
    return {
        "Attains": 7,
        "Does Not Meet": 8,
        "Unknown": 9,
    }.get(label, 4)


def category_style(text: str) -> int:
    if text == "Attains" or text.startswith("Attains ") or "Attains:" in text or text.endswith("(A)"):
        return 7
    if text in {"Does Not Meet", "Not Attained"} or text.startswith("Does Not Meet ") or "Does Not Meet:" in text or text.endswith("(D)"):
        return 8
    if text in {"Unknown", "X"} or text.startswith("Unknown ") or "Unknown:" in text:
        return 9
    return 0


def is_table_header(row: list[Any]) -> bool:
    if not row:
        return False
    first = str(row[0] or "")
    return not is_outcome_block_header(row) and (
        first in {"Outcome", "Assessment", "Student"}
    )


def row_height(row: list[Any]) -> str:
    if not row:
        return ""
    if is_evidence_outcome_heading(row):
        return ' ht="30" customHeight="1"'
    longest = max((len(str(value or "")) for value in row), default=0)
    if len(row) == 1 and longest > 40:
        return ' ht="34" customHeight="1"'
    if longest > 120:
        return ' ht="54" customHeight="1"'
    if longest > 70:
        return ' ht="38" customHeight="1"'
    return ""


def column_widths_xml(column_count: int) -> str:
    widths = [22, 34, 46, 18, 20, 18, 18, 18, 18, 18, 18, 18, 18]
    columns = []
    for index in range(1, column_count + 1):
        width = widths[index - 1] if index <= len(widths) else 14
        columns.append(f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>')
    return f"<cols>{''.join(columns)}</cols>"


def column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def xml_escape(value: str) -> str:
    return html.escape(value, quote=True)
