"""Bounded, source-preserving adaptive splitting without remote model calls."""

import logging
import re
from dataclasses import replace
from importlib.metadata import version

import tiktoken

from app.config import Settings
from app.pipelines.state import Block, Chunk, ProcessingError, SourceSpan, State


def prefix_end(text: str, budget: int, count) -> int:
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if count(text[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    if low == 0:
        raise ProcessingError("token_budget", "Token budget cannot fit one Unicode character")
    return low


def make_chunk(
    block: Block, start: int, end: int, count, strategy: str, flags: list[str], header_end: int = 0
) -> Chunk:
    spans = [SourceSpan(block.block_id, start, end)]
    content = block.text[start:end]
    if header_end and start >= header_end:
        content = block.text[:header_end] + content
        spans.insert(0, SourceSpan(block.block_id, 0, header_end))
        flags = [*flags, "repeated_table_header"]
    reason = {
        "table_rows": "Preserve rows and repeat table header",
        "list_items": "Preserve list item boundaries",
        "section": "Preserve section boundaries",
        "recursive": "Semantic provider not configured; use paragraph/sentence boundaries",
    }[strategy]
    return Chunk(
        content,
        count(content),
        spans,
        [block.page_number],
        [block.block_id],
        block.section_path,
        block.block_type,
        strategy,
        reason,
        [block.source_method],
        list(dict.fromkeys([*block.quality_flags, *flags])),
    )


def split_block(block: Block, settings: Settings, count) -> list[Chunk]:
    strategy = (
        "table_rows"
        if block.block_type == "table"
        else (
            "list_items"
            if block.block_type == "list"
            else "section"
            if block.section_path
            else "recursive"
        )
    )
    chunks = []
    start = 0
    header_end = block.text.find("\n") + 1 if block.block_type == "table" else 0
    header_too_large = bool(
        header_end and count(block.text[:header_end]) >= settings.chunk_max_tokens
    )
    if header_too_large:
        header_end = 0
    while start < len(block.text):
        prefix = block.text[:header_end] if header_end and start >= header_end else ""
        remaining = block.text[start:]
        flags = ["table_header_over_budget"] if header_too_large else []
        if count(prefix + remaining) <= settings.chunk_max_tokens:
            end = len(block.text)
        else:
            budget = settings.chunk_target_tokens - count(prefix)
            if budget <= 0:
                budget = settings.chunk_max_tokens - count(prefix)
            fit = prefix_end(remaining, max(1, budget), count)
            # Larger boundaries win; do not split a table row merely to hit target_tokens.
            patterns = (
                [r"\n"]
                if block.block_type in ("table", "list")
                else [r"\n\s*\n", r"[.!?。！？](?:\s+|$)", r"\n", r"\s+"]
            )
            boundary = 0
            for pattern in patterns:
                candidates = [match.end() for match in re.finditer(pattern, remaining[:fit])]
                if candidates:
                    boundary = candidates[-1]
                    break
            if not boundary and block.block_type in ("table", "list"):
                maximum = prefix_end(
                    remaining, settings.chunk_max_tokens, lambda text: count(prefix + text)
                )
                newline = remaining.find("\n", 0, maximum)
                if newline >= 0:
                    boundary = newline + 1
            end = start + (boundary or fit)
            if not boundary:
                flags.append("structure_cut")
        # Whitespace is retained in spans/content; avoid emitting an empty semantic unit.
        if not block.text[start:end].strip():
            next_word = re.search(r"\S+", block.text[end:])
            if next_word:
                end += next_word.end()
        while count(prefix + block.text[start:end]) > settings.chunk_max_tokens and end > start:
            end -= 1
        if end <= start or not block.text[start:end].strip():
            raise ProcessingError("token_budget", "Unable to preserve content within token budget")
        chunks.append(make_chunk(block, start, end, count, strategy, flags, header_end))
        if len(chunks) > settings.pdf_max_chunks:
            raise ProcessingError("chunk_limit", "PDF exceeds chunk limit")
        if end == len(block.text):
            break
        next_start = end
        if settings.chunk_overlap_tokens and block.block_type not in ("table", "list"):
            # Overlap stays inside this source block; char boundaries preserve UTF-8 text.
            low = start + 1
            high = end
            overlap_budget = min(settings.chunk_overlap_tokens, count(block.text[start:end]) // 2)
            while low < high:
                middle = (low + high) // 2
                if count(block.text[middle:end]) <= overlap_budget:
                    high = middle
                else:
                    low = middle + 1
            next_start = low
        start = next_start
    return chunks


def evaluate(state: State, settings: Settings, count) -> dict:
    by_id = {block.block_id: block for block in state.blocks}
    intervals: dict[str, list[tuple[int, int]]] = {key: [] for key in by_id}
    for chunk in state.chunks:
        if not chunk.content.strip() or not 0 < count(chunk.content) <= settings.chunk_max_tokens:
            raise ProcessingError("invalid_chunk", "Chunk is empty or exceeds token budget")
        if chunk.token_count != count(chunk.content):
            raise ProcessingError("invalid_chunk", "Chunk token count is inconsistent")
        reconstructed = []
        for span in chunk.source_spans:
            block = by_id[span.block_id]
            if not 0 <= span.start < span.end <= len(block.text):
                raise ProcessingError("source_mapping", "Invalid source span")
            intervals[span.block_id].append((span.start, span.end))
            reconstructed.append(block.text[span.start : span.end])
        # Merged chunks insert a newline between original pieces.
        if chunk.content not in ("".join(reconstructed), "\n".join(reconstructed)):
            raise ProcessingError("source_mapping", "Chunk content does not match source spans")
    total = sum(len(block.text) for block in state.blocks)
    duplicated = 0
    for key, spans in intervals.items():
        covered = 0
        for start, end in sorted(spans):
            if start > covered:
                raise ProcessingError("coverage", "Chunk coverage is incomplete")
            duplicated += max(0, min(covered, end) - start)
            covered = max(covered, end)
        if covered != len(by_id[key].text):
            raise ProcessingError("coverage", "Chunk coverage is incomplete")
    if not state.chunks:
        raise ProcessingError("empty_chunks", "No usable chunks")
    return {
        "coverage": 1.0,
        "source_chars": total,
        "repeated_chars": duplicated,
        "short_chunks": sum(
            chunk.token_count < settings.chunk_min_tokens for chunk in state.chunks
        ),
        "structure_cuts": sum("structure_cut" in chunk.quality_flags for chunk in state.chunks),
        "repair_rounds": state.repair_rounds,
        "semantic_fallback": "provider_not_configured",
    }


def chunk_document(state: State, settings: Settings) -> State:
    logger = logging.getLogger(__name__)
    logger.info("Chunking started: document=%s blocks=%s", state.document_id, len(state.blocks))
    encoder = tiktoken.get_encoding(settings.tokenizer_name)

    def count(text: str) -> int:
        return len(encoder.encode_ordinary(text))

    state.processing_config.update(
        {"tokenizer_name": settings.tokenizer_name, "tokenizer_version": version("tiktoken")}
    )
    state.chunks = []
    for index, block in enumerate(state.blocks, 1):
        state.chunks.extend(split_block(block, settings, count))
        if len(state.chunks) > settings.pdf_max_chunks:
            raise ProcessingError("chunk_limit", "PDF exceeds chunk limit")
        logger.info(
            "Chunking progress: document=%s block=%s/%s chunks=%s",
            state.document_id,
            index,
            len(state.blocks),
            len(state.chunks),
        )
    for _ in range(settings.chunk_max_repair_rounds):
        merged = []
        changed = False
        for chunk in state.chunks:
            previous = merged[-1] if merged else None
            if (
                previous
                and min(previous.token_count, chunk.token_count) < settings.chunk_min_tokens
            ):
                same_region = (
                    previous.section_path == chunk.section_path
                    and (
                        bool(chunk.section_path)
                        or previous.page_numbers[-1] == chunk.page_numbers[0]
                    )
                    and previous.block_type == chunk.block_type == "text"
                    and previous.source_block_ids[-1] != chunk.source_block_ids[0]
                )
                content = previous.content + "\n" + chunk.content
                if same_region and count(content) <= settings.chunk_max_tokens:
                    merged[-1] = replace(
                        previous,
                        content=content,
                        token_count=count(content),
                        source_spans=previous.source_spans + chunk.source_spans,
                        source_block_ids=previous.source_block_ids + chunk.source_block_ids,
                        page_numbers=list(
                            dict.fromkeys(previous.page_numbers + chunk.page_numbers)
                        ),
                        source_methods=list(
                            dict.fromkeys(previous.source_methods + chunk.source_methods)
                        ),
                        quality_flags=list(
                            dict.fromkeys(previous.quality_flags + chunk.quality_flags)
                        ),
                    )
                    changed = True
                    continue
            merged.append(chunk)
        if not changed:
            break
        state.chunks = merged
        state.repair_rounds += 1
    logger.info(
        "Chunk evaluation: document=%s chunks=%s repair_rounds=%s",
        state.document_id,
        len(state.chunks),
        state.repair_rounds,
    )
    state.quality_summary = evaluate(state, settings, count)
    logger.info(
        "Chunking completed: document=%s chunks=%s warnings=%s",
        state.document_id,
        len(state.chunks),
        len(state.warnings),
    )
    return state
