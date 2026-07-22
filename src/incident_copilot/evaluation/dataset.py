"""在仓库边界内加载评估数据集并解析可信本地 Evidence。"""

import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from pydantic import JsonValue

from incident_copilot.domain.evidence import (
    Citation,
    EvidenceResolutionError,
    EvidenceResolver,
)
from incident_copilot.evaluation.schemas import EvaluationDataset
from incident_copilot.rag.loader import MarkdownDocumentLoader
from incident_copilot.rag.splitter import MarkdownSplitter

FIXTURE_LOCATOR_PATTERN = re.compile(r"^evidence\[(?P<index>0|[1-9][0-9]*)\](?P<suffix>.*)$")
LOCATOR_FIELD_PATTERN = re.compile(r"^\.(?P<field>[A-Za-z_][A-Za-z0-9_]*)")
LOCATOR_INDEX_PATTERN = re.compile(r"^\[(?P<index>0|[1-9][0-9]*)\]")
MAX_RESOLVER_SOURCE_BYTES = 5_000_000


class RepositoryEvidenceResolver(EvidenceResolver):
    """只解析仓库内不可变 fixture 和 knowledge citation。"""

    def __init__(self, root: Path | None = None) -> None:
        self._root = (root or repository_root()).resolve()
        self._incident_root = (self._root / "data" / "incidents").resolve()
        self._knowledge_root = (self._root / "data" / "knowledge").resolve()

    def resolve(self, citation: Citation) -> JsonValue:
        """根据受控 URI 和 locator 重新读取原始内容。"""
        parsed = urlsplit(citation.uri)
        # Resolver 只支持仓库内两种 scheme, 不会因评估引用触发网络访问。
        if parsed.query or parsed.fragment:
            raise EvidenceResolutionError("local citation URI must not contain query or fragment")
        if parsed.scheme == "fixture" and parsed.netloc == "incidents":
            return self._resolve_fixture(unquote(parsed.path.lstrip("/")), citation.locator)
        if parsed.scheme == "internal" and parsed.netloc == "knowledge":
            return self._resolve_knowledge(citation)
        raise EvidenceResolutionError(
            f"resolver does not support citation source: {parsed.scheme}://{parsed.netloc}"
        )

    def _resolve_fixture(self, relative_path: str, locator: str) -> JsonValue:
        # 路径安全检查先于文件读取, locator 再限制为 evidence[index] 子树。
        path = self._safe_source_path(self._incident_root, relative_path, suffix=".json")
        match = FIXTURE_LOCATOR_PATTERN.fullmatch(locator)
        if match is None:
            raise EvidenceResolutionError("fixture locator must use evidence[index]")
        payload = self._read_json(path)
        evidence = payload.get("evidence")
        index = int(match.group("index"))
        if not isinstance(evidence, list) or index >= len(evidence):
            raise EvidenceResolutionError("fixture locator is outside the evidence collection")
        selected = evidence[index]
        if not isinstance(selected, dict) or "content" not in selected:
            raise EvidenceResolutionError("fixture locator does not resolve to evidence content")
        self._validate_locator_suffix(selected, match.group("suffix"))
        return selected["content"]

    @staticmethod
    def _validate_locator_suffix(selected: object, suffix: str) -> None:
        """验证受控 JSON 子路径存在,内容完整性仍覆盖完整 Evidence content。"""
        current = selected
        remaining = suffix
        while remaining:
            # 每轮只消费一个明确的 .field 或 [index], 不使用 eval 或通用 JSONPath。
            field_match = LOCATOR_FIELD_PATTERN.match(remaining)
            if field_match is not None:
                field = field_match.group("field")
                if not isinstance(current, dict) or field not in current:
                    raise EvidenceResolutionError("fixture locator field does not exist")
                current = current[field]
                remaining = remaining[field_match.end() :]
                continue
            index_match = LOCATOR_INDEX_PATTERN.match(remaining)
            if index_match is not None:
                index = int(index_match.group("index"))
                if not isinstance(current, list) or index >= len(current):
                    raise EvidenceResolutionError("fixture locator index does not exist")
                current = current[index]
                remaining = remaining[index_match.end() :]
                continue
            raise EvidenceResolutionError("fixture locator contains unsupported syntax")

    def _resolve_knowledge(self, citation: Citation) -> JsonValue:
        relative_path = unquote(urlsplit(citation.uri).path.lstrip("/"))
        # 先确认 URI 指向真实仓库文件, 再用与生产索引相同的 Loader/Splitter 重建 Chunk。
        expected_path = self._safe_source_path(self._knowledge_root, relative_path, suffix=".md")
        if expected_path.stat().st_size > MAX_RESOLVER_SOURCE_BYTES:
            raise EvidenceResolutionError("knowledge source exceeds resolver size limit")
        try:
            documents = MarkdownDocumentLoader(self._knowledge_root).load()
            chunks = MarkdownSplitter().split_documents(documents)
        except (OSError, UnicodeError, ValueError) as exc:
            raise EvidenceResolutionError("knowledge source cannot be reconstructed") from exc
        for chunk in chunks:
            # URI 与 locator 必须同时一致, 仅文档相同不能证明定位到了同一段内容。
            if chunk.citation.uri == citation.uri and chunk.citation.locator == citation.locator:
                return chunk.text
        raise EvidenceResolutionError("knowledge locator does not resolve to a chunk")

    @staticmethod
    def _safe_source_path(root: Path, relative_path: str, *, suffix: str) -> Path:
        if not relative_path or Path(relative_path).suffix != suffix:
            raise EvidenceResolutionError(f"citation source must reference a {suffix} file")
        candidate = (root / relative_path).resolve()
        # resolve 后同时验证父目录和文件存在性, 阻止 .. 与符号链接逃逸。
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise EvidenceResolutionError("citation source is outside the configured repository")
        return candidate

    @staticmethod
    def _read_json(path: Path) -> dict[str, JsonValue]:
        # 先限制字节数再解码和解析, 大文件不会进入 json.loads。
        if path.stat().st_size > MAX_RESOLVER_SOURCE_BYTES:
            raise EvidenceResolutionError("fixture source exceeds resolver size limit")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EvidenceResolutionError("fixture source is not valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise EvidenceResolutionError("fixture source must contain a JSON object")
        return payload


def repository_root() -> Path:
    """解析项目根目录,但不依赖调用者的当前工作目录。"""
    return Path(__file__).parents[3]


def default_dataset_path() -> Path:
    """返回仓库内已提交的 Phase 6 数据集位置。"""
    return repository_root() / "data" / "evaluation" / "incidents-v1.json"


def load_evaluation_dataset(path: Path | None = None) -> EvaluationDataset:
    """在执行任何 Graph 前加载并严格校验离线数据集。"""
    selected = (path or default_dataset_path()).resolve()
    return EvaluationDataset.model_validate_json(selected.read_text(encoding="utf-8"))


def resolve_fixture_path(relative_path: str) -> Path:
    """解析已校验的数据集 Fixture 路径,并确保路径位于仓库内。"""
    root = repository_root().resolve()
    candidate = (root / relative_path).resolve()
    if root not in candidate.parents:
        raise ValueError("evaluation fixture path escapes the repository")
    return candidate
