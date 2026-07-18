"""Source-derived Mermaid rendering and documentation drift checks."""

from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.graph.bootstrap import build_offline_investigation_graph

FENCE_START = "```mermaid\n"
FENCE_END = "\n```"


def extract_documented_mermaid(path: Path) -> str:
    """Extract the single generated Mermaid fence from its documentation page."""
    content = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = content.index(FENCE_START) + len(FENCE_START)
    end = content.index(FENCE_END, start)
    return content[start:end].strip()


def current_mermaid() -> str:
    """Return LangGraph's own visualization of the compiled source graph."""
    graph = build_offline_investigation_graph(
        checkpointer=InMemorySaver(),
        require_human_review=True,
    )
    return graph.get_graph().draw_mermaid().strip()
