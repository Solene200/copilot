"""运行离线混合检索演示并输出保留引用的结果。"""

import argparse
import json

from incident_copilot.rag import (
    DocumentType,
    MetadataFilter,
    SearchQuery,
    build_fixture_retriever,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--query",
        default="database connection pool timeout",
        help="Natural-language query to rewrite and retrieve.",
    )
    parser.add_argument("--service", default="payment-service")
    parser.add_argument(
        "--document-type",
        choices=[item.value for item in DocumentType],
        default=None,
    )
    parser.add_argument("--top-k", type=int, default=3)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    retriever, ingest = build_fixture_retriever()
    document_types = (DocumentType(args.document_type),) if args.document_type is not None else ()
    result = retriever.search(
        SearchQuery(
            query=args.query,
            top_k=args.top_k,
            metadata_filter=MetadataFilter(
                services=(args.service,),
                document_types=document_types,
            ),
        )
    )
    payload = {
        "status": "ok",
        "backend": "bm25+fake-vector+rrf",
        "indexed_document_count": ingest.indexed_document_count,
        "indexed_chunk_count": ingest.indexed_chunk_count,
        "original_query": result.original_query,
        "rewritten_query": result.rewritten_query,
        "hits": [
            {
                "rank": hit.rank,
                "score": hit.score,
                "document_id": hit.chunk.document_id,
                "document_type": hit.chunk.document_type.value,
                "section_path": list(hit.chunk.section_path),
                "matched_by": list(hit.matched_by),
                "citation": hit.chunk.citation.model_dump(mode="json"),
                "text": hit.chunk.text,
            }
            for hit in result.hits
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
