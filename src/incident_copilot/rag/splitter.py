"""感知标题且具有有界重叠的确定性 Markdown 切分。"""

import re
from dataclasses import dataclass

from incident_copilot.domain.evidence import Citation
from incident_copilot.rag.schemas import (
    SEARCH_TOKEN_PATTERN,
    KnowledgeChunk,
    KnowledgeDocument,
    content_sha256,
    normalize_document_text,
)

# 识别 Markdown 一到六级标题及其标题文字。
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
DEFAULT_MAX_TOKENS = 120
DEFAULT_OVERLAP_TOKENS = 20


def tokenize(value: str) -> tuple[str, ...]:
    """确定性切分英文标识符和单个中日韩字符。"""
    return tuple(match.group(0).casefold() for match in SEARCH_TOKEN_PATTERN.finditer(value))


@dataclass(frozen=True, slots=True)
class _Section:
    # 当前正文所属的 Markdown 标题层级路径。
    path: tuple[str, ...]
    # 当前标题下、下一个标题前的正文内容。
    body: str


class MarkdownSplitter:
    """在 Markdown 章节内切分并保留引用和元数据血缘。"""

    def __init__(
        self,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    ) -> None:
        if max_tokens < 20 or max_tokens > 2_000:
            raise ValueError("max_tokens must be between 20 and 2000")
        if overlap_tokens < 0 or overlap_tokens >= max_tokens // 2:
            raise ValueError("overlap_tokens must be non-negative and less than half max_tokens")
        self._max_tokens = max_tokens
        self._overlap_tokens = overlap_tokens

    def split_documents(
        self, documents: tuple[KnowledgeDocument, ...]
    ) -> tuple[KnowledgeChunk, ...]:
        """切分稳定文档序列并生成全局稳定的 Chunk 顺序。"""
        chunks: list[KnowledgeChunk] = []
        for document in sorted(documents, key=lambda item: item.document_id):
            chunks.extend(self.split(document))
        return tuple(chunks)

    def split(self, document: KnowledgeDocument) -> tuple[KnowledgeChunk, ...]:
        """切分单份文档,但不跨越明确的 Markdown 标题边界。"""
        chunk_texts: list[tuple[tuple[str, ...], str]] = []
        # 先建立标题章节, 再在章节内部按 Token 切分, 让每个 Chunk 保留语义路径。
        for section in self._sections(document):
            for text in self._split_section(section):
                chunk_texts.append((section.path, text))

        chunks: list[KnowledgeChunk] = []
        for ordinal, (section_path, text) in enumerate(chunk_texts):
            # ordinal 与内容哈希共同构成稳定 ID, 同一文档重复摄取不会制造重复 Chunk。
            content_hash = content_sha256(text)
            stable_suffix = (
                f"{document.document_id.removeprefix('doc_')}_{ordinal}_{content_hash[:12]}"
            )
            chunk_id = f"chunk_{stable_suffix}"
            # Citation 在切分时绑定原文位置; 检索阶段只转发, 不重新生成来源信息。
            citation = Citation.for_content(
                content=text,
                citation_id=f"cit_{stable_suffix}",
                uri=document.source_uri,
                locator=f"section={' > '.join(section_path)};chunk={ordinal}",
                display_name=f"{document.title} - {' > '.join(section_path)}",
                retrieved_at=document.ingested_at,
            )
            chunks.append(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    document_id=document.document_id,
                    document_type=document.document_type,
                    document_title=document.title,
                    ordinal=ordinal,
                    text=text,
                    token_count=len(tokenize(text)),
                    section_path=section_path,
                    service_tags=document.service_tags,
                    environment_tags=document.environment_tags,
                    version=document.version,
                    effective_at=document.effective_at,
                    metadata=document.metadata,
                    content_hash=content_hash,
                    citation=citation,
                )
            )
        return tuple(chunks)

    @staticmethod
    def _sections(document: KnowledgeDocument) -> tuple[_Section, ...]:
        heading_stack: list[str] = []
        current_path: tuple[str, ...] = (document.title,)
        body_lines: list[str] = []
        sections: list[_Section] = []

        def flush() -> None:
            body = "\n".join(body_lines).strip()
            if body:
                # 遇到新标题时先保存旧章节, 空章节不进入索引。
                sections.append(_Section(current_path, body))

        for line in document.content.split("\n"):
            heading = HEADING_PATTERN.match(line)
            if heading is None:
                body_lines.append(line)
                continue
            flush()
            body_lines.clear()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            # 用标题级别截断栈, 例如从 ### 返回 ## 时会丢弃旧的三级路径。
            heading_stack[level - 1 :] = [title]
            current_path = tuple(heading_stack)
        flush()
        return tuple(sections)

    def _split_section(self, section: _Section) -> tuple[str, ...]:
        prefix = f"Section: {' > '.join(section.path)}"
        prefix_tokens = len(tokenize(prefix))
        available = self._max_tokens - prefix_tokens
        if available < 5:
            raise ValueError("section path leaves no room for chunk content")

        body_tokens = tuple(SEARCH_TOKEN_PATTERN.finditer(section.body))
        if len(body_tokens) <= available:
            return (normalize_document_text(f"{prefix}\n{section.body}"),)

        # step 小于窗口大小, 相邻 Chunk 保留配置的上下文重叠。
        step = available - self._overlap_tokens
        chunks: list[str] = []
        start = 0
        while start < len(body_tokens):
            end = min(start + available, len(body_tokens))
            # 使用正则匹配的字符偏移切原文, 不用 Token 重新拼接, 从而保留标点与格式。
            start_offset = 0 if start == 0 else body_tokens[start].start()
            end_offset = (
                len(section.body) if end == len(body_tokens) else body_tokens[end - 1].end()
            )
            body = section.body[start_offset:end_offset].strip()
            chunks.append(normalize_document_text(f"{prefix}\n{body}"))
            if end == len(body_tokens):
                break
            start += step
        return tuple(chunks)
