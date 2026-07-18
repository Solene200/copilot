"""初始化确定性本地知识索引并输出实际统计数量。"""

import argparse
import json
from pathlib import Path

from incident_copilot.rag.bootstrap import build_fixture_retriever
from incident_copilot.rag.loader import MarkdownDocumentLoader


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--knowledge-root",
        type=Path,
        default=None,
        help="Optional directory containing TOML-frontmatter Markdown documents.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    retriever, first = build_fixture_retriever(knowledge_root=args.knowledge_root)
    documents = MarkdownDocumentLoader(
        args.knowledge_root
        if args.knowledge_root is not None
        else Path(__file__).parents[1] / "data" / "knowledge"
    ).load()
    repeated = retriever.ingest(documents)
    payload = {
        "status": "ok",
        "backend": "in-memory",
        "embedding": "fake-signed-hash-v1",
        "input_document_count": first.input_document_count,
        "indexed_document_count": first.indexed_document_count,
        "indexed_chunk_count": first.indexed_chunk_count,
        "repeated_ingest_same_counts": (
            first.indexed_document_count == repeated.indexed_document_count
            and first.indexed_chunk_count == repeated.indexed_chunk_count
        ),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
