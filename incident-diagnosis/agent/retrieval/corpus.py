from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .bm25 import BM25Index
from .models import RetrievalDocument


class CorpusUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ApprovedCorpusSnapshot:
    version: int
    documents: tuple[RetrievalDocument, ...]
    exact_by_trigger: Mapping[str, tuple[RetrievalDocument, ...]]
    bm25: BM25Index


def _join_text(*parts: str) -> str:
    return " ".join(str(part).strip() for part in parts if part and str(part).strip())


def build_snapshot(rdb, version: int) -> ApprovedCorpusSnapshot:
    # Import lazily: memory's compatibility incident search reuses BM25Index.
    import memory

    knowledge_entries = {
        entry.get("key"): entry
        for entry in memory.list_knowledge_entries(rdb)
        if entry.get("key")
    }
    documents: list[RetrievalDocument] = []
    for key in sorted(knowledge_entries):
        entry = knowledge_entries[key]
        root = str(entry.get("root_cause_pattern") or "").strip()
        fix = str(entry.get("fix_action") or "").strip()
        trigger = str(entry.get("trigger_waiting_reason") or "").strip()
        documents.append(RetrievalDocument(
            knowledge_key=key,
            source="knowledge",
            source_id=key,
            trigger=trigger,
            conclusive=bool(entry.get("conclusive")),
            root_cause_pattern=root,
            fix_action=fix,
            document_text=_join_text(trigger, root),
        ))

    histories = sorted(
        memory.list_all_history_entries(rdb),
        key=lambda item: (
            str(item.get("knowledge_key") or ""),
            str(item.get("symptom") or ""),
            str(item.get("context_notes") or ""),
            str(item.get("id") or ""),
        ),
    )
    for history in histories:
        key = history.get("knowledge_key")
        parent = knowledge_entries.get(key)
        if not parent:
            continue
        root = str(parent.get("root_cause_pattern") or "").strip()
        fix = str(parent.get("fix_action") or "").strip()
        documents.append(RetrievalDocument(
            knowledge_key=key,
            source="history",
            source_id=str(history.get("id") or ""),
            trigger=str(parent.get("trigger_waiting_reason") or "").strip(),
            conclusive=bool(parent.get("conclusive")),
            root_cause_pattern=root,
            fix_action=fix,
            document_text=_join_text(
                history.get("symptom", ""),
                history.get("context_notes", ""),
                root,
            ),
            context_notes=str(history.get("context_notes") or ""),
        ))

    exact: dict[str, list[RetrievalDocument]] = {}
    for document in documents:
        if (
            document.source == "knowledge" and document.conclusive and document.trigger
            and document.root_cause_pattern and document.fix_action
        ):
            exact.setdefault(document.trigger, []).append(document)
    exact_by_trigger = MappingProxyType({
        trigger: tuple(items) for trigger, items in exact.items()
    })
    frozen_documents = tuple(documents)
    return ApprovedCorpusSnapshot(
        version=version,
        documents=frozen_documents,
        exact_by_trigger=exact_by_trigger,
        bm25=BM25Index(frozen_documents),
    )


class CorpusProvider:
    def __init__(self, rdb):
        self.rdb = rdb
        self._lock = threading.RLock()
        self._snapshot: ApprovedCorpusSnapshot | None = None
        self._rebuild_count = 0

    def get_snapshot(self) -> tuple[ApprovedCorpusSnapshot, bool]:
        import memory

        try:
            version = memory.get_corpus_version(self.rdb)
        except Exception as exc:
            if self._snapshot is not None:
                return self._snapshot, True
            raise CorpusUnavailable("corpus version unavailable") from exc

        if self._snapshot is not None and self._snapshot.version == version:
            return self._snapshot, False

        with self._lock:
            try:
                version = memory.get_corpus_version(self.rdb)
            except Exception as exc:
                if self._snapshot is not None:
                    return self._snapshot, True
                raise CorpusUnavailable("corpus version unavailable") from exc
            if self._snapshot is not None and self._snapshot.version == version:
                return self._snapshot, False

            started = time.perf_counter()
            try:
                replacement = build_snapshot(self.rdb, version)
            except Exception as exc:
                raise CorpusUnavailable("corpus rebuild failed") from exc
            self._snapshot = replacement
            self._rebuild_count += 1
            elapsed_ms = (time.perf_counter() - started) * 1000
            print(
                f"[retrieval] corpus_rebuild count={self._rebuild_count} "
                f"version={version} documents={len(replacement.documents)} "
                f"duration_ms={elapsed_ms:.1f}",
                flush=True,
            )
            return replacement, False

    def invalidate(self) -> None:
        with self._lock:
            self._snapshot = None
