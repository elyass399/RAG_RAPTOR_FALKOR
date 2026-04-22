"""
pdf_to_md.py — Convertitore PDF → Markdown
Legge da ./pdfs/  |  Scrive in ./markdowns/

Gestisce:
  - Testo normale con paragrafi
  - Tabelle (via pdfplumber)
  - Elenchi puntati e numerati (rilevamento euristico)
  - Titoli (rilevamento da font size via PyMuPDF)
  - PDF scansionati (fallback a OCR via pytesseract se disponibile)
"""

import os, re, sys
from pathlib import Path

# ── DIPENDENZE ───────────────────────────────────────────────────────────────
try:
    import pdfplumber
except ImportError:
    sys.exit("❌ Installa pdfplumber:  pip install pdfplumber")

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("❌ Installa PyMuPDF:  pip install pymupdf")

# OCR opzionale (solo per PDF scansionati)
try:
    import pytesseract
    from PIL import Image
    import io
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

# ── CONFIGURAZIONE ────────────────────────────────────────────────────────────
INPUT_DIR  = Path("./pdfs")
OUTPUT_DIR = Path("./markdowns")

# Soglie per il rilevamento titoli (in punti tipografici)
TITLE_SIZE_H1   = 18
TITLE_SIZE_H2   = 14
TITLE_SIZE_H3   = 12
BODY_SIZE_MIN   = 8   # sotto questa soglia ignoriamo (numeri pagina, note)

# Pattern per elenchi puntati
BULLET_PATTERNS = [
    r"^[\u2022\-\u2013\u2014\*]\s+",   # bullet unicode, -, trattini, *
    r"^[>\u27a4\u25ba\u25b6\u2713\u2714\u2192]\s+",  # frecce e check
    r"^\d+[\.\)]\s+",                   # 1. oppure 1)
    r"^[a-zA-Z][\.\)]\s+",             # a. oppure a)
    r"^\([a-zA-Z0-9]+\)\s+",           # (a) oppure (1)
]
BULLET_RE = re.compile("|".join(BULLET_PATTERNS))

# ── HELPERS ───────────────────────────────────────────────────────────────────
def is_bullet(line: str) -> bool:
    return bool(BULLET_RE.match(line.strip()))

def normalize_bullet(line: str) -> str:
    """Normalizza qualsiasi stile di bullet in markdown standard."""
    stripped = line.strip()
    if re.match(r"^\d+[\.\)]\s+", stripped):
        num = re.match(r"^(\d+)[\.\)]\s+", stripped).group(1)
        rest = re.sub(r"^\d+[\.\)]\s+", "", stripped)
        return f"{num}. {rest}"
    if re.match(r"^[a-zA-Z][\.\)]\s+", stripped):
        rest = re.sub(r"^[a-zA-Z][\.\)]\s+", "", stripped)
        return f"- {rest}"
    rest = BULLET_RE.sub("", stripped)
    return f"- {rest}"

def clean_text(text: str) -> str:
    """Pulizia base del testo estratto dal PDF."""
    if not text:
        return ""
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"-\n([a-z])", r"\1", text)
    return text.strip()

def table_to_markdown(table: list) -> str:
    """Converte una tabella pdfplumber (lista di liste) in markdown."""
    if not table or not table[0]:
        return ""

    cleaned = []
    for row in table:
        cleaned_row = [str(cell).replace("\n", " ").strip() if cell else "" for cell in row]
        cleaned.append(cleaned_row)

    max_cols = max(len(row) for row in cleaned)
    cleaned = [row + [""] * (max_cols - len(row)) for row in cleaned]

    col_widths = [max(len(row[i]) for row in cleaned) for i in range(max_cols)]
    col_widths = [max(w, 3) for w in col_widths]

    def format_row(row):
        return "| " + " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row)) + " |"

    def separator():
        return "| " + " | ".join("-" * col_widths[i] for i in range(max_cols)) + " |"

    lines = [format_row(cleaned[0]), separator()]
    for row in cleaned[1:]:
        lines.append(format_row(row))

    return "\n".join(lines)

def extract_font_spans(pdf_path: str) -> dict:
    """
    Estrae le dimensioni font per ogni pagina usando PyMuPDF.
    Ritorna: {page_num: [(text, size, flags), ...]}
    flags: bit0=bold, bit1=italic
    """
    doc = fitz.open(pdf_path)
    page_fonts = {}
    for page_num, page in enumerate(doc):
        spans = []
        for block in page.get_text("dict")["blocks"]:
            if block["type"] != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"].strip()
                    size = round(span["size"], 1)
                    flags = span["flags"]
                    if text and size >= BODY_SIZE_MIN:
                        spans.append((text, size, flags))
        page_fonts[page_num] = spans
    doc.close()
    return page_fonts

def classify_line(text: str, size: float, flags: int, median_size: float) -> str:
    """
    Classifica una riga come h1/h2/h3/bullet/body.
    Ritorna il prefisso markdown o 'BULLET'.
    """
    is_bold = bool(flags & 1)

    if size >= TITLE_SIZE_H1 or (size >= TITLE_SIZE_H2 and is_bold and size > median_size * 1.3):
        return "# "
    if size >= TITLE_SIZE_H2 or (size > median_size * 1.2 and is_bold):
        return "## "
    if size >= TITLE_SIZE_H3 and (is_bold or size > median_size * 1.1):
        return "### "
    if is_bullet(text):
        return "BULLET"
    return ""

def ocr_page(fitz_page) -> str:
    """Fallback OCR per pagine scansionate."""
    if not OCR_AVAILABLE:
        return "[Pagina scansionata — installa pytesseract + Pillow per OCR automatico]"
    mat = fitz.Matrix(2, 2)
    pix = fitz_page.get_pixmap(matrix=mat)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img, lang="ita+eng")

# ── CONVERTITORE PRINCIPALE ───────────────────────────────────────────────────
def convert_pdf(pdf_path: Path) -> str:
    """
    Converte un singolo PDF in Markdown.
    Per ogni pagina:
      1. Estrae tabelle (pdfplumber) con bounding box
      2. Raggruppa parole in righe (ordine verticale)
      3. Classifica ogni riga (titolo / bullet / corpo)
      4. Intercala le tabelle nella posizione verticale corretta
    """
    print(f"  📄 {pdf_path.name}")

    md_pages   = []
    font_spans = extract_font_spans(str(pdf_path))
    fitz_doc   = fitz.open(str(pdf_path))

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_num, page in enumerate(pdf.pages):

            # ── TABELLE ───────────────────────────────────────────────────────
            tbl_settings_lines = {
                "vertical_strategy": "lines", "horizontal_strategy": "lines",
                "snap_tolerance": 3, "join_tolerance": 3, "edge_min_length": 10,
            }
            tbl_settings_text = {
                "vertical_strategy": "text", "horizontal_strategy": "text",
                "snap_tolerance": 5,
            }

            found_tables = page.find_tables(tbl_settings_lines)
            if not found_tables:
                found_tables = page.find_tables(tbl_settings_text)

            # Lista: (y_top, markdown_string)
            tables_with_pos = []
            for ft in found_tables:
                tbl_data = ft.extract()
                if tbl_data:
                    y_top = ft.bbox[1]
                    tables_with_pos.append((y_top, table_to_markdown(tbl_data)))

            # Set di y-coordinate occupate da tabelle (per saltare testo sovrapposto)
            table_y_ranges = [(ft.bbox[1], ft.bbox[3]) for ft in found_tables]

            def is_in_table(y: float) -> bool:
                return any(y1 <= y <= y2 for y1, y2 in table_y_ranges)

            # ── CONTROLLO PDF SCANSIONATO ─────────────────────────────────────
            raw_text = page.extract_text() or ""
            if len(raw_text.strip()) < 20 and not found_tables:
                ocr_text = ocr_page(fitz_doc[page_num])
                md_pages.append(f"<!-- Pagina {page_num+1} OCR -->\n{ocr_text}")
                continue

            # ── FONT SIZE MEDIANO ─────────────────────────────────────────────
            spans = font_spans.get(page_num, [])
            if spans:
                sizes = sorted(s for _, s, _ in spans)
                median_size = sizes[len(sizes) // 2]
            else:
                median_size = 11.0

            # ── RAGGRUPPA PAROLE IN RIGHE ─────────────────────────────────────
            words = page.extract_words(keep_blank_chars=False, use_text_flow=True)
            if not words:
                continue

            lines_grouped = []
            current_line  = []
            current_y     = None
            Y_TOLERANCE   = 3

            for w in words:
                if current_y is None or abs(w["top"] - current_y) <= Y_TOLERANCE:
                    current_line.append(w)
                    current_y = w["top"]
                else:
                    if current_line:
                        lines_grouped.append(current_line)
                    current_line = [w]
                    current_y    = w["top"]
            if current_line:
                lines_grouped.append(current_line)

            # ── ASSEMBLAGGIO ──────────────────────────────────────────────────
            md_lines        = []
            prev_was_bullet = False
            tables_queue    = sorted(tables_with_pos, key=lambda x: x[0])
            inserted_tables = set()

            for line_words in lines_grouped:
                line_top  = line_words[0]["top"]
                line_text = " ".join(w["text"] for w in line_words)
                line_text = clean_text(line_text)

                if not line_text:
                    continue

                # Salta testo che cade dentro una tabella
                if is_in_table(line_top):
                    continue

                # Inserisci tabelle che vengono prima di questa riga
                for i, (ty, tmd) in enumerate(tables_queue):
                    if i not in inserted_tables and ty < line_top:
                        md_lines.append("")
                        md_lines.append(tmd)
                        md_lines.append("")
                        inserted_tables.add(i)

                # Match font size per questa riga
                matched_size  = median_size
                matched_flags = 0
                for span_text, sz, fl in spans:
                    if span_text and len(span_text) > 2 and span_text[:10] in line_text:
                        matched_size  = sz
                        matched_flags = fl
                        break

                classification = classify_line(line_text, matched_size, matched_flags, median_size)

                if classification == "BULLET":
                    md_lines.append(normalize_bullet(line_text))
                    prev_was_bullet = True
                elif classification in ("# ", "## ", "### "):
                    if prev_was_bullet:
                        md_lines.append("")
                    md_lines.append(f"\n{classification}{line_text}")
                    prev_was_bullet = False
                else:
                    if prev_was_bullet:
                        md_lines.append("")
                    md_lines.append(line_text)
                    prev_was_bullet = False

            # Tabelle rimaste a fine pagina
            for i, (_, tmd) in enumerate(tables_queue):
                if i not in inserted_tables:
                    md_lines.append("")
                    md_lines.append(tmd)
                    md_lines.append("")

            page_md = "\n".join(md_lines)
            page_md = re.sub(r"\n{3,}", "\n\n", page_md)
            if page_md.strip():
                md_pages.append(page_md)

    fitz_doc.close()

    full_md = "\n\n---\n\n".join(md_pages)
    return re.sub(r"\n{3,}", "\n\n", full_md).strip()

# ── POST-PROCESSING ────────────────────────────────────────────────────────────
def post_process(md: str) -> str:
    """Pulizia finale: rimuove numeri pagina isolati, linee decorative, elenchi spezzati."""
    lines   = md.split("\n")
    cleaned = []
    for line in lines:
        s = line.strip()
        if re.match(r"^\d{1,4}$", s):          # numero pagina isolato
            continue
        if re.match(r"^[-_=]{5,}$", s):         # linea decorativa
            continue
        cleaned.append(line)

    result = "\n".join(cleaned)
    # Unisci continuazioni di elenchi spezzati su piu righe
    result = re.sub(r"(^- .+)\n([^-\n#\|>])", r"\1 \2", result, flags=re.MULTILINE)
    # Normalizza separatori multipli
    result = re.sub(r"(\n---\n){2,}", "\n---\n", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result

# ── RUNNER ─────────────────────────────────────────────────────────────────────
def run():
    if not INPUT_DIR.exists():
        INPUT_DIR.mkdir(parents=True)
        print(f"📁 Creata cartella '{INPUT_DIR}/' — inserisci i PDF e rilancia lo script.")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(INPUT_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"⚠️  Nessun .pdf trovato in '{INPUT_DIR}/'")
        return

    print(f"\n🚀 PDF trovati: {len(pdfs)}\n")
    ok, errors = 0, []

    for pdf_path in pdfs:
        try:
            md       = convert_pdf(pdf_path)
            md       = post_process(md)
            title    = pdf_path.stem.replace("_", " ").replace("-", " ")
            header   = f"# {title}\n\n> Convertito da: `{pdf_path.name}`\n\n---\n\n"
            final_md = header + md

            out_path = OUTPUT_DIR / (pdf_path.stem + ".md")
            out_path.write_text(final_md, encoding="utf-8")

            kb = out_path.stat().st_size // 1024
            print(f"  ✅ {pdf_path.name} → {out_path.name} ({kb} KB)")
            ok += 1

        except Exception as e:
            print(f"  ❌ {pdf_path.name} → {e}")
            errors.append((pdf_path.name, str(e)))

    print(f"\n{'='*50}")
    print(f"Convertiti: {ok}/{len(pdfs)}")
    if errors:
        print(f"Errori ({len(errors)}):")
        for name, err in errors:
            print(f"  - {name}: {err}")
    print(f"Output: {OUTPUT_DIR.resolve()}")

if __name__ == "__main__":
    run()