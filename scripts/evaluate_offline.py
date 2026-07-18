"""Run the versioned no-network evaluation and write raw plus summary artifacts."""

import argparse
import asyncio
from pathlib import Path

from incident_copilot.evaluation import OfflineEvaluationRunner, load_evaluation_dataset


def parse_args() -> argparse.Namespace:
    """Parse a deliberately small offline evaluation CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, help="Optional versioned dataset JSON path")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/evaluation/latest"),
        help="Directory for raw-results.jsonl, summary.json, and summary.md",
    )
    parser.add_argument(
        "--langsmith",
        action="store_true",
        help="Explicitly enable LangSmith tracing; disabled by default",
    )
    parser.add_argument(
        "--langsmith-project",
        default="incident-copilot-offline-evaluation",
        help="LangSmith project used only with --langsmith",
    )
    return parser.parse_args()


async def main() -> None:
    """Validate the dataset, execute all samples, and print the artifact location."""
    args = parse_args()
    dataset = load_evaluation_dataset(args.dataset)
    runner = OfflineEvaluationRunner(
        enable_langsmith=args.langsmith,
        project_name=args.langsmith_project,
    )
    summary = await runner.run(dataset, args.output_dir)
    print(args.output_dir / "summary.md")
    print(
        f"{summary.completed_sample_count}/{summary.sample_count} samples completed; "
        f"{summary.failed_sample_count} failed"
    )


if __name__ == "__main__":
    asyncio.run(main())
