"""Render Mermaid directly from the currently compiled interactive graph."""

import argparse
from pathlib import Path

from incident_copilot.graph.visualization import current_mermaid, extract_documented_mermaid


def main() -> None:
    """Print current Mermaid or check that the committed document is current."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", type=Path, help="fail if a documented Mermaid fence is stale")
    arguments = parser.parse_args()
    generated = current_mermaid()
    if arguments.check is not None:
        documented = extract_documented_mermaid(arguments.check)
        if documented != generated:
            raise SystemExit("documented Mermaid does not match the compiled graph")
        print(f"Mermaid is current: {arguments.check}")
        return
    print(generated)


if __name__ == "__main__":
    main()
