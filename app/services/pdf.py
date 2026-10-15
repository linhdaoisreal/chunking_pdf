"""Local parsing and OCR, called only inside the bounded ingestion process."""

import csv
import io
import logging
import math
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import closing
from importlib.metadata import version
from pathlib import Path

import pypdfium2 as pdfium
from PIL import ImageDraw
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.config import Settings
from app.pipelines.state import Block, ProcessingError, State

logger = logging.getLogger(__name__)


def tesseract_args(settings: Settings) -> list[str]:
    args = [settings.tesseract_command]
    if settings.tesseract_data_dir:
        args += ["--tessdata-dir", str(settings.tesseract_data_dir)]
    return args


def check_ocr(settings: Settings) -> str:
    try:
        result = subprocess.run(
            tesseract_args(settings) + ["--list-langs"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        languages = set(result.stdout.splitlines()[1:])
        if not set(settings.ocr_languages.split("+")).issubset(languages):
            raise ProcessingError("ocr_unavailable", "Configured OCR languages are missing", True)
        result = subprocess.run(
            [settings.tesseract_command, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        return result.stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError):
        raise ProcessingError("ocr_unavailable", "Tesseract is unavailable", True) from None


def recognize(image, settings: Settings) -> tuple[str, float]:
    with tempfile.TemporaryDirectory(prefix="pdf-ocr-") as directory:
        path = Path(directory) / "page.png"
        image.save(path)
        try:
            result = subprocess.run(
                tesseract_args(settings)
                + [str(path), "stdout", "-l", settings.ocr_languages, "--psm", "3", "tsv"],
                capture_output=True,
                timeout=settings.ocr_timeout_seconds,
                check=True,
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            )
        except subprocess.TimeoutExpired:
            raise ProcessingError("ocr_timeout", "OCR exceeded its deadline", True) from None
        except (OSError, subprocess.CalledProcessError):
            raise ProcessingError("ocr_failed", "Local OCR failed", True) from None
    lines: dict[tuple[str, ...], list[str]] = {}
    confidence = []
    for row in csv.DictReader(io.StringIO(result.stdout.decode("utf-8")), delimiter="\t"):
        text = row.get("text", "").strip()
        if row.get("level") == "5" and text:
            key = tuple(row[name] for name in ("block_num", "par_num", "line_num"))
            lines.setdefault(key, []).append(text)
            confidence.append(float(row["conf"]))
    return "\n".join(" ".join(words) for words in lines.values()), (
        sum(confidence) / len(confidence) if confidence else 0
    )


def add_blocks(state: State, text: str, page: int, method: str, flags: list[str]) -> None:
    # Preserve every normalized character, including spacing used by layout extraction.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        text = text.replace("\x00", "\ufffd")
        state.warnings.append(f"page:{page}:null_char_replaced")
    if not text.strip():
        return
    section: list[str] = []
    for part in re.findall(r"[\s\S]+?(?:\n\n+|$)", text):
        if part != part.strip():
            state.warnings.append(f"page:{page}:boundary_whitespace_trimmed")
        part = part.strip()
        if not part:
            continue
        lines = [line for line in part.splitlines() if line.strip()]
        kind = "text"
        if len(lines) > 1 and all(re.search(r"\S[ \t]{2,}\S|\|", line) for line in lines):
            kind = "table"
        elif lines and all(re.match(r"\s*(?:[-*•]|\d+[.)])\s", line) for line in lines):
            kind = "list"
        elif (
            len(lines) == 1
            and len(lines[0]) < 120
            and (lines[0].isupper() or re.match(r"^\s*\d+(?:\.\d+)*\s+\S", lines[0]))
        ):
            kind = "heading"
            section = [lines[0].strip()]
        state.blocks.append(
            Block(
                block_id=f"p{page}-b{len(state.blocks)}",
                page_number=page,
                text=part,
                block_type=kind,
                source_method=method,
                section_path=list(section),
                quality_flags=list(flags),
            )
        )


def parse_pdf(state: State, path: Path, settings: Settings, stage: Callable[[str], None]) -> State:
    stage("parsing")
    state.processing_config.update(
        {
            "parser_version": version("pypdf"),
            "renderer_version": version("pypdfium2"),
            "ocr_version": check_ocr(settings),
        }
    )
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            raise ProcessingError("encrypted_pdf", "Password-protected PDFs are unsupported")
        state.page_count = len(reader.pages)
        if not 0 < state.page_count <= settings.pdf_max_pages:
            raise ProcessingError("page_limit", "PDF is empty or exceeds the page limit")
        with pdfium.PdfDocument(path) as rendered:
            for index, source in enumerate(reader.pages):
                page_number = index + 1
                started = time.monotonic()
                logger.info(
                    "PDF page started: document=%s page=%s/%s",
                    state.document_id,
                    page_number,
                    state.page_count,
                )
                try:
                    native = (
                        (
                            source.extract_text(
                                extraction_mode="layout", layout_mode_strip_rotated=False
                            )
                            or ""
                        )
                        if "/Contents" in source
                        else ""
                    )
                    page = rendered[index]
                    try:
                        has_images = any(page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]))
                        add_blocks(state, native, page_number, "text", [])
                        if not native.strip() or has_images:
                            stage("ocr")
                            logger.info(
                                "OCR started: document=%s page=%s/%s",
                                state.document_id,
                                page_number,
                                state.page_count,
                            )
                            width, height = page.get_size()
                            scale = settings.ocr_dpi / 72
                            if (
                                math.ceil(width * scale) * math.ceil(height * scale)
                                > settings.pdf_max_render_pixels
                            ):
                                raise ProcessingError(
                                    "render_limit", "Page exceeds render pixel limit"
                                )
                            bitmap = page.render(scale=scale)
                            try:
                                image = bitmap.to_pil().convert("RGB")
                                try:
                                    if native.strip():
                                        # Keep image text between native glyphs visible to OCR.
                                        with closing(page.get_textpage()) as textpage:
                                            draw = ImageDraw.Draw(image)
                                            for char in range(textpage.count_chars()):
                                                left, bottom, right, top = textpage.get_charbox(
                                                    char
                                                )
                                                if right > left and top > bottom:
                                                    draw.rectangle(
                                                        (
                                                            left * scale,
                                                            (height - top) * scale,
                                                            right * scale,
                                                            (height - bottom) * scale,
                                                        ),
                                                        fill="white",
                                                    )
                                    text, confidence = recognize(image, settings)
                                finally:
                                    image.close()
                            finally:
                                bitmap.close()
                            logger.info(
                                "OCR completed: document=%s page=%s confidence=%.2f",
                                state.document_id,
                                page_number,
                                confidence,
                            )
                            flags = []
                            if confidence < settings.ocr_min_confidence:
                                flags.append("low_ocr_confidence")
                            if native.strip():
                                flags.append("mixed_reading_order_approximate")
                            if not text.strip():
                                flags.append("ocr_no_text")
                            state.warnings.extend(f"page:{page_number}:{flag}" for flag in flags)
                            add_blocks(state, text, page_number, "ocr", flags)
                        stage("parsing")
                    finally:
                        page.close()
                    if sum(len(block.text) for block in state.blocks) > settings.pdf_max_text_chars:
                        raise ProcessingError(
                            "text_limit", "Extracted text exceeds character limit"
                        )
                    logger.info(
                        "PDF page completed: document=%s page=%s/%s blocks=%s seconds=%.2f",
                        state.document_id,
                        page_number,
                        state.page_count,
                        len(state.blocks),
                        time.monotonic() - started,
                    )
                except Exception:
                    logger.warning(
                        "PDF page failed: document=%s page=%s/%s",
                        state.document_id,
                        page_number,
                        state.page_count,
                    )
                    state.warnings.append(f"page:{page_number}:processing_failed")
                    raise
    except ProcessingError:
        raise
    except (PdfReadError, pdfium.PdfiumError, ValueError, KeyError, IndexError):
        raise ProcessingError("invalid_pdf", "PDF could not be read completely") from None
    if not any(block.text.strip() for block in state.blocks):
        raise ProcessingError("empty_pdf", "PDF has no usable content after OCR")
    return state
