"""Typed pipeline payloads; spans refer to normalized block text, end exclusive."""

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID


@dataclass(frozen=True)
class SourceSpan:
    block_id: str
    start: int
    end: int


@dataclass
class Block:
    block_id: str
    page_number: int
    text: str
    block_type: Literal["text", "heading", "table", "list", "image"]
    source_method: Literal["text", "ocr"]
    section_path: list[str] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)


@dataclass
class Chunk:
    content: str
    token_count: int
    source_spans: list[SourceSpan]
    page_numbers: list[int]
    source_block_ids: list[str]
    section_path: list[str]
    block_type: str
    strategy: str
    strategy_reason: str
    source_methods: list[str]
    quality_flags: list[str] = field(default_factory=list)


@dataclass
class State:
    document_id: UUID
    blocks: list[Block] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    repair_rounds: int = 0
    page_count: int = 0
    quality_summary: dict[str, Any] = field(default_factory=dict)
    processing_config: dict[str, Any] = field(default_factory=dict)


class ProcessingError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
