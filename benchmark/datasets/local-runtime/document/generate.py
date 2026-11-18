#!/usr/bin/env python3
"""Generate the 15 synthetic documents for the document parsing benchmark.

Every document comes from this file, so the content and the gold markers are
reproducible and can be checked by reading this file alone. The bytes are not
byte-reproducible: the Office writers embed timestamps, so cases.jsonl records
the hash of the file that was actually produced. Nothing here contains real
personal data -- the company, people and addresses are invented.

Run:
    /data/home/fjd/marketops-lab/envs/mineru/bin/python \
        benchmark/datasets/local-runtime/document/generate.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import PageBreak, SimpleDocTemplate, Table, TableStyle, Paragraph

OUT = pathlib.Path(__file__).with_name("generated")
OUT.mkdir(parents=True, exist_ok=True)

COMPANY = "Northwind Marine Fittings Ltd"
ADDRESS = "14 Harbour Road, Rotterdam, 3011 AA"
EMAIL = "info@northwind.example"
PHONE = "+31 10 555 0123"


# --------------------------------------------------------------------------
# text PDFs
# --------------------------------------------------------------------------

def plain_one_page(path: pathlib.Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"<b>{COMPANY}</b>", styles["Title"]),
        Paragraph("Company profile", styles["Heading2"]),
        Paragraph(
            "Northwind Marine Fittings Ltd assembles marine fittings in Rotterdam. "
            "The company was founded in 1987 and supplies eleven shipyards in the "
            "Netherlands, Belgium and Germany.",
            styles["BodyText"],
        ),
        Paragraph(
            f"Registered office: {ADDRESS}<br/>Email: {EMAIL}<br/>Phone: {PHONE}",
            styles["BodyText"],
        ),
        Paragraph("Products", styles["Heading2"]),
        Paragraph("Bronze valves, deck fittings, custom manifolds.", styles["BodyText"]),
    ]
    doc.build(story)


def plain_two_pages(path: pathlib.Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"{COMPANY} — Quality manual", styles["Title"]),
        Paragraph("Section 1. Scope", styles["Heading2"]),
        Paragraph(
            "This manual covers the inspection of cast bronze valve bodies at every "
            "stage between casting and despatch. It applies to all production lines.",
            styles["BodyText"],
        ),
        PageBreak(),
        Paragraph("Section 2. Inspection intervals", styles["Heading2"]),
        Paragraph(
            "Inspection happens at three points: after casting, after machining, and "
            "before packing. Records are kept for seven years at the Rotterdam site.",
            styles["BodyText"],
        ),
        Paragraph("Contact for quality questions: quality@northwind.example", styles["BodyText"]),
    ]
    doc.build(story)


def plain_list(path: pathlib.Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    styles = getSampleStyleSheet()
    rows = [
        ["Step", "Owner", "Record"],
        ["Casting", "Foundry", "Batch log"],
        ["Machining", "Workshop", "Dimension sheet"],
        ["Pressure test", "Quality", "Test certificate"],
    ]
    table = Table(rows, colWidths=[45 * mm, 45 * mm, 60 * mm])
    table.setStyle(
        TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ])
    )
    story = [
        Paragraph(f"{COMPANY} — Production steps", styles["Title"]),
        Paragraph("The table lists each step, who owns it and the record it leaves.", styles["BodyText"]),
        table,
    ]
    doc.build(story)


# --------------------------------------------------------------------------
# scanned PDFs (image only, no text layer)
# --------------------------------------------------------------------------

def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def scanned_page(path: pathlib.Path, lines: list[str], rotate: int = 0, contrast: int = 255) -> None:
    img = Image.new("RGB", (1654, 2339), "white")  # A4 at 200 dpi
    draw = ImageDraw.Draw(img)
    font = _font(38)
    y = 220
    for line in lines:
        draw.text((150, y), line, fill=(20, 20, 20), font=font)
        y += 80
    if contrast < 255:
        # wash the page out so OCR has to work for it
        overlay = Image.new("RGB", img.size, "white")
        img = Image.blend(img, overlay, 1.0 - contrast / 255)
    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor="white")
    img.save(path, "PDF", resolution=200)


def scanned_letter(path: pathlib.Path) -> None:
    scanned_page(path, [
        COMPANY,
        "14 Harbour Road, Rotterdam",
        "",
        "Dear customer,",
        "Please find the revised price list for bronze valves.",
        "Prices are valid until 31 March 2027.",
        "Contact: info@northwind.example",
        "Phone: +31 10 555 0123",
    ])


def scanned_table(path: pathlib.Path) -> None:
    """A ruled table, not space-aligned text.

    Without the ruling lines this page would only test text OCR; the lines are
    what make table-structure recovery a real question.
    """
    img = Image.new("RGB", (1654, 2339), "white")
    draw = ImageDraw.Draw(img)
    font = _font(34)
    cells = [
        ["Item", "Size", "Price"],
        ["Bronze valve", "1/2 in", "EUR 42"],
        ["Bronze valve", "3/4 in", "EUR 55"],
        ["Deck fitting", "standard", "EUR 78"],
        ["Manifold", "custom", "on request"],
    ]
    x0, y0, x1, y1 = 140, 200, 1500, 800
    row_h = (y1 - y0) / len(cells)
    col_w = (x1 - x0) / 3
    for r, row in enumerate(cells):
        for c, text in enumerate(row):
            cx, cy = x0 + c * col_w, y0 + r * row_h
            draw.rectangle([cx, cy, cx + col_w, cy + row_h], outline=(20, 20, 20), width=3)
            draw.text((cx + 18, cy + row_h / 2 - 18), text, fill=(20, 20, 20), font=font)
    draw.text((140, 130), "PRICE LIST 2027", fill=(20, 20, 20), font=_font(42))
    img.save(path, "PDF", resolution=200)


def scanned_skewed(path: pathlib.Path) -> None:
    scanned_page(path, [
        "INSPECTION CERTIFICATE",
        "",
        "Batch: NW-2027-0042",
        "Inspector: J. van Dijk",
        "Result: PASS",
        "Pressure: 16 bar",
        "Notes: surface finish within tolerance.",
    ], rotate=-2, contrast=150)


# --------------------------------------------------------------------------
# multi-column PDFs
# --------------------------------------------------------------------------

def _columns(path: pathlib.Path, columns: int, pages: int = 1, banner: str = "") -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    for page in range(pages):
        if banner:
            c.setFont("Helvetica-Bold", 16)
            c.drawString(20 * mm, height - 25 * mm, banner)
        gutter = 8 * mm
        col_width = (width - 40 * mm - gutter * (columns - 1)) / columns
        top = height - (45 * mm if banner else 25 * mm)
        for col in range(columns):
            x = 20 * mm + col * (col_width + gutter)
            c.setFont("Helvetica", 10)
            text = c.beginText(x, top)
            for i in range(18):
                text.textLine(
                    f"Column {col + 1}, line {i + 1}: the quick brown fox jumps over "
                    f"the lazy dog while the assembly line keeps moving."
                )
            c.drawText(text)
            c.setFont("Helvetica-Bold", 11)
            c.drawString(x, top - 15, f"HEAD-{col + 1}")
        c.showPage()
    c.save()


def multicol_two(path: pathlib.Path) -> None:
    _columns(path, 2, banner="Northwind quarterly review")


def multicol_three(path: pathlib.Path) -> None:
    _columns(path, 3, banner="Northwind technical bulletin")


def multicol_banner(path: pathlib.Path) -> None:
    _columns(path, 2, pages=2, banner="Northwind annual report 2026")


# --------------------------------------------------------------------------
# table PDFs
# --------------------------------------------------------------------------

def table_simple(path: pathlib.Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    styles = getSampleStyleSheet()
    header = ["Part", "Material", "Diameter", "Pressure", "Price"]
    rows = [header] + [
        [f"NW-{100 + i}", "Bronze", f"{10 + i} mm", f"{16 + i} bar", f"EUR {40 + i * 7}"]
        for i in range(12)
    ]
    t = Table(rows, colWidths=[30 * mm, 30 * mm, 30 * mm, 30 * mm, 30 * mm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
    ]))
    doc.build([
        Paragraph("Northwind parts catalogue", styles["Title"]),
        Paragraph("Twelve parts with dimensions and list prices.", styles["BodyText"]),
        t,
    ])


def table_merged(path: pathlib.Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    styles = getSampleStyleSheet()
    rows = [
        ["Region", "Country", "", "Contact"],
        ["Benelux", "Netherlands", "Rotterdam", "benelux@northwind.example"],
        ["", "Belgium", "Antwerp", "benelux@northwind.example"],
        ["DACH", "Germany", "Hamburg", "dach@northwind.example"],
        ["", "Austria", "Linz", "dach@northwind.example"],
    ]
    t = Table(rows, colWidths=[30 * mm, 35 * mm, 35 * mm, 60 * mm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("SPAN", (0, 0), (1, 0)),
        ("SPAN", (0, 1), (0, 2)),
        ("SPAN", (0, 3), (0, 4)),
    ]))
    doc.build([
        Paragraph("Northwind regional contacts", styles["Title"]),
        Paragraph("Merged cells group countries under one region.", styles["BodyText"]),
        t,
    ])


def table_spanning(path: pathlib.Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    styles = getSampleStyleSheet()
    header = ["Order", "Customer", "City", "Items", "Total"]
    rows = [header] + [
        [f"SO-{2000 + i}", f"Customer {i}", "Rotterdam", str(3 + i % 4), f"EUR {500 + i * 11}"]
        for i in range(45)
    ]
    t = Table(rows, colWidths=[28 * mm, 40 * mm, 30 * mm, 20 * mm, 30 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
    ]))
    doc.build([
        Paragraph("Northwind order book", styles["Title"]),
        Paragraph("The header row repeats on every page.", styles["BodyText"]),
        t,
    ])


# --------------------------------------------------------------------------
# Office documents
# --------------------------------------------------------------------------

def make_docx(path: pathlib.Path) -> None:
    import docx

    d = docx.Document()
    d.add_heading(COMPANY, level=1)
    d.add_paragraph(
        "This document is a synthetic sample for the MarketOps document benchmark. "
        "It contains a heading, body text and a table."
    )
    d.add_heading("Contacts", level=2)
    table = d.add_table(rows=3, cols=2)
    table.cell(0, 0).text = "Department"
    table.cell(0, 1).text = "Email"
    table.cell(1, 0).text = "Sales"
    table.cell(1, 1).text = "sales@northwind.example"
    table.cell(2, 0).text = "Support"
    table.cell(2, 1).text = "support@northwind.example"
    d.save(str(path))


def make_pptx(path: pathlib.Path) -> None:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = COMPANY
    slide.placeholders[1].text = "Annual review 2026"
    slide.placeholders[1].text += "\nRotterdam · 42 employees · 11 shipyards served"

    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Products"
    slide.placeholders[1].text = "Bronze valves\nDeck fittings\nCustom manifolds"

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(2))
    box.text_frame.text = "Contact: info@northwind.example · +31 10 555 0123"
    prs.save(str(path))


def make_xlsx(path: pathlib.Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Orders"
    ws.append(["Order", "Customer", "City", "Items", "Total"])
    for i in range(10):
        ws.append([f"SO-{3000 + i}", f"Customer {i}", "Rotterdam", 2 + i, 400 + i * 9])
    ws2 = wb.create_sheet("Contacts")
    ws2.append(["Department", "Email"])
    ws2.append(["Sales", "sales@northwind.example"])
    ws2.append(["Support", "support@northwind.example"])
    wb.save(str(path))


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------

CASES = [
    # (case_id, doc_type, filename, generator, difficulty, expected)
    ("DOC-PDF-PLAIN-001", "pdf_text", "plain-01.pdf", plain_one_page, "easy",
     {"page_count": 1, "min_text_chars": 200,
      "text_markers": [COMPANY, "Rotterdam", "1987"],
      "expected_emails": [EMAIL], "expected_phones": [PHONE]}),
    ("DOC-PDF-PLAIN-002", "pdf_text", "plain-02.pdf", plain_two_pages, "easy",
     {"page_count": 2, "min_text_chars": 250,
      "text_markers": [COMPANY, "Scope", "Inspection intervals"],
      "reading_order": ["Section 1. Scope", "Section 2. Inspection intervals"],
      "expected_emails": ["quality@northwind.example"]}),
    ("DOC-PDF-PLAIN-003", "pdf_text", "plain-03.pdf", plain_list, "easy",
     {"page_count": 1, "min_text_chars": 120,
      "text_markers": [COMPANY, "Casting", "Pressure test"],
      "tables": [{"rows": 4, "columns": 3,
                  "cell_markers": ["Machining", "Dimension sheet", "Test certificate"]}]}),

    ("DOC-PDF-SCAN-001", "pdf_scanned", "scanned-01.pdf", scanned_letter, "medium",
     {"page_count": 1, "min_text_chars": 150,
      "text_markers": [COMPANY, "price list", "Harbour Road"],
      "expected_emails": [EMAIL], "expected_phones": [PHONE]}),
    ("DOC-PDF-SCAN-002", "pdf_scanned", "scanned-02.pdf", scanned_table, "hard",
     {"page_count": 1, "min_text_chars": 80,
      "text_markers": ["PRICE LIST", "Bronze valve", "EUR 55"],
      "tables": [{"rows": 5, "columns": 3, "cell_markers": ["Deck fitting", "EUR 78"]}]}),
    ("DOC-PDF-SCAN-003", "pdf_scanned", "scanned-03.pdf", scanned_skewed, "hard",
     {"page_count": 1, "min_text_chars": 80,
      "text_markers": ["INSPECTION CERTIFICATE", "NW-2027-0042", "PASS"]}),

    ("DOC-PDF-MULTICOL-001", "pdf_multicolumn", "multicol-01.pdf", multicol_two, "medium",
     {"page_count": 1, "min_text_chars": 800,
      "text_markers": ["Column 1, line 1", "Column 2, line 1", "Northwind quarterly review"],
      "reading_order": ["Column 1, line 1", "Column 1, line 18", "Column 2, line 1"]}),
    ("DOC-PDF-MULTICOL-002", "pdf_multicolumn", "multicol-02.pdf", multicol_three, "hard",
     {"page_count": 1, "min_text_chars": 1000,
      "text_markers": ["Column 3, line 1", "Northwind technical bulletin"],
      "reading_order": ["Column 1, line 1", "Column 2, line 1", "Column 3, line 1"]}),
    ("DOC-PDF-MULTICOL-003", "pdf_multicolumn", "multicol-03.pdf", multicol_banner, "hard",
     {"page_count": 2, "min_text_chars": 1600,
      "text_markers": ["Northwind annual report", "Column 2, line 18"]}),

    ("DOC-PDF-TABLE-001", "pdf_table", "table-01.pdf", table_simple, "medium",
     {"page_count": 1, "min_text_chars": 300,
      "text_markers": ["NW-100", "NW-111", "EUR 117"],
      "tables": [{"rows": 13, "columns": 5,
                  "cell_markers": ["Material", "Pressure", "Bronze"]}]}),
    ("DOC-PDF-TABLE-002", "pdf_table", "table-02.pdf", table_merged, "hard",
     {"page_count": 1, "min_text_chars": 140,
      "text_markers": ["Benelux", "DACH", "benelux@northwind.example"],
      "tables": [{"rows": 5, "columns": 4,
                  "cell_markers": ["Rotterdam", "Linz", "dach@northwind.example"]}]}),
    ("DOC-PDF-TABLE-003", "pdf_table", "table-03.pdf", table_spanning, "hard",
     {"page_count": 2, "min_text_chars": 900,
      "text_markers": ["SO-2000", "SO-2044", "Northwind order book"],
      "tables": [{"rows": 46, "columns": 5, "cell_markers": ["Customer 12", "EUR 632"]}]}),

    ("DOC-OFFICE-DOCX-001", "docx", "sample.docx", make_docx, "easy",
     {"page_count": 1, "min_text_chars": 150,
      "text_markers": [COMPANY, "Sales", "support@northwind.example"],
      "expected_emails": ["sales@northwind.example", "support@northwind.example"]}),
    ("DOC-OFFICE-PPTX-001", "pptx", "sample.pptx", make_pptx, "easy",
     {"page_count": 3, "min_text_chars": 100,
      "text_markers": [COMPANY, "Annual review 2026", "Bronze valves"],
      "expected_emails": [EMAIL]}),
    ("DOC-OFFICE-XLSX-001", "xlsx", "sample.xlsx", make_xlsx, "easy",
     {"page_count": 2, "min_text_chars": 100,
      "text_markers": ["Orders", "SO-3000", "sales@northwind.example"],
      "expected_emails": ["sales@northwind.example"]}),
]


def main() -> None:
    lines = []
    for case_id, doc_type, filename, fn, difficulty, expected in CASES:
        path = OUT / filename
        fn(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        case = {
            "case_id": case_id,
            "schema_version": "marketops.benchmark.document.v1",
            "split": "development",
            "doc_type": doc_type,
            "path": f"generated/{filename}",
            "sha256": digest,
            "difficulty": difficulty,
            "timeout_seconds": 180,
            "language": "en",
            "expected": expected,
            "label_status": "draft",
            "validation_timestamp": "2026-11-18T17:00:00+08:00",
            "notes": (
                "synthetic_fixture: generated by document/generate.py. Gold markers are "
                "the strings this script actually prints; they were not taken from any "
                "parser output. Human verification still decides whether the parse "
                "quality is acceptable, so the case stays draft."
            ),
        }
        lines.append(json.dumps(case, ensure_ascii=False, sort_keys=True))
        print(f"{filename:22} {path.stat().st_size:>9} B  {digest[:16]}  {case_id}")

    (pathlib.Path(__file__).with_name("cases.jsonl")).write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {len(lines)} cases -> cases.jsonl")


if __name__ == "__main__":
    main()
