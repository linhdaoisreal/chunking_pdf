import logging
import time
from collections.abc import Callable
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableLambda
from langsmith import tracing_context

from app.config import Settings
from app.pipelines.state import State
from app.services.chunking import chunk_document
from app.services.pdf import parse_pdf


class StageTiming(BaseCallbackHandler):
    """Log timings and IDs only; never log chain inputs or PDF content."""

    def __init__(self, document_id):
        self.document_id = document_id
        self.started = {}

    def on_chain_start(self, serialized, inputs, *, run_id, name=None, **kwargs):
        self.started[run_id] = (name, time.monotonic())

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        name, started = self.started.pop(run_id, ("unknown", time.monotonic()))
        logging.getLogger(__name__).info(
            "Pipeline stage: document=%s stage=%s seconds=%.2f",
            self.document_id,
            name,
            time.monotonic() - started,
        )


def ingest(state: State, source: Path, settings: Settings, stage: Callable[[str], None]) -> State:
    def parse(value: State) -> State:
        return parse_pdf(value, source, settings, stage)

    def chunk(value: State) -> State:
        stage("chunking")
        return chunk_document(value, settings)

    pipeline = RunnableLambda(parse, name="parse_ocr") | RunnableLambda(
        chunk, name="adaptive_chunk"
    )
    with tracing_context(enabled=False):
        return pipeline.invoke(state, config={"callbacks": [StageTiming(state.document_id)]})
