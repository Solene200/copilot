"""在仓库边界内安全加载版本化离线评估数据集。"""

from pathlib import Path

from incident_copilot.evaluation.schemas import EvaluationDataset


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
