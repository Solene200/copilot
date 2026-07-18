"""Repository-safe loading for versioned offline evaluation datasets."""

from pathlib import Path

from incident_copilot.evaluation.schemas import EvaluationDataset


def repository_root() -> Path:
    """Resolve the project root without depending on the caller's working directory."""
    return Path(__file__).parents[3]


def default_dataset_path() -> Path:
    """Return the checked-in Phase 6 dataset location."""
    return repository_root() / "data" / "evaluation" / "incidents-v1.json"


def load_evaluation_dataset(path: Path | None = None) -> EvaluationDataset:
    """Load and strictly validate an offline dataset before any graph execution."""
    selected = (path or default_dataset_path()).resolve()
    return EvaluationDataset.model_validate_json(selected.read_text(encoding="utf-8"))


def resolve_fixture_path(relative_path: str) -> Path:
    """Resolve a validated dataset fixture path and keep it inside the repository."""
    root = repository_root().resolve()
    candidate = (root / relative_path).resolve()
    if root not in candidate.parents:
        raise ValueError("evaluation fixture path escapes the repository")
    return candidate
