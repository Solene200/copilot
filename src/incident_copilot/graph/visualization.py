"""根据源码生成 Mermaid 并检查文档漂移。"""

from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.graph.bootstrap import build_offline_investigation_graph

FENCE_START = "```mermaid\n"
FENCE_END = "\n```"


def extract_documented_mermaid(path: Path) -> str:
    """从文档页面中提取唯一的自动生成 Mermaid 代码块。"""
    content = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = content.index(FENCE_START) + len(FENCE_START)
    end = content.index(FENCE_END, start)
    return content[start:end].strip()


def current_mermaid() -> str:
    """返回 LangGraph 为当前编译源码 Graph 生成的可视化。"""
    graph = build_offline_investigation_graph(
        checkpointer=InMemorySaver(),
        require_human_review=True,
    )
    return graph.get_graph().draw_mermaid().strip()
