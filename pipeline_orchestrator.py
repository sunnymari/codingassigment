import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence, TypedDict

from langchain_google_vertexai import ChatVertexAI
from langchain_core.messages import HumanMessage


DEFAULT_MODEL = "gemini-1.5-flash"


@dataclass(frozen=True)
class Workflow:
    workflow_id: str
    name: str
    classification_ceiling: str
    compartments: List[str]
    document_ids: List[str]


class CorpusDocument(TypedDict):
    text: str
    classification_level: str
    required_compartments: List[str]


CLASSIFICATION_RANK = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "secret": 3,
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_workflows(path: Path) -> List[Workflow]:
    raw = load_json(path)
    workflows: List[Workflow] = []
    for item in raw:
        workflows.append(
            Workflow(
                workflow_id=item["workflow_id"],
                name=item["name"],
                classification_ceiling=item["classification_ceiling"],
                compartments=item["compartments"],
                document_ids=item["document_ids"],
            )
        )
    if len(workflows) != 5:
        raise ValueError(
            f"Expected exactly 5 workflows in {path}, found {len(workflows)}."
        )
    return workflows


def is_classification_allowed(doc_level: str, ceiling: str) -> bool:
    if doc_level not in CLASSIFICATION_RANK:
        raise ValueError(f"Unknown document classification level: '{doc_level}'.")
    if ceiling not in CLASSIFICATION_RANK:
        raise ValueError(f"Unknown workflow classification ceiling: '{ceiling}'.")
    return CLASSIFICATION_RANK[doc_level] <= CLASSIFICATION_RANK[ceiling]


def fetch_documents_for_workflow(
    workflow: Workflow, document_ids: Sequence[str], corpus: Dict[str, CorpusDocument]
) -> tuple[Dict[str, CorpusDocument], Dict[str, str]]:
    allowed_docs: Dict[str, CorpusDocument] = {}
    denied_docs: Dict[str, str] = {}
    workflow_compartments = set(workflow.compartments)

    for doc_id in document_ids:
        if doc_id not in corpus:
            raise KeyError(f"Document ID '{doc_id}' not found in corpus.")
        doc = corpus[doc_id]
        level_ok = is_classification_allowed(
            doc["classification_level"], workflow.classification_ceiling
        )
        compartments_ok = set(doc["required_compartments"]).issubset(workflow_compartments)

        if level_ok and compartments_ok:
            allowed_docs[doc_id] = doc
            continue

        if not level_ok:
            denied_docs[doc_id] = (
                f"classification '{doc['classification_level']}' exceeds "
                f"workflow ceiling '{workflow.classification_ceiling}'"
            )
        else:
            denied_docs[doc_id] = (
                "missing required compartments: "
                f"{sorted(set(doc['required_compartments']) - workflow_compartments)}"
            )

    return allowed_docs, denied_docs


def build_summary_prompt(workflow: Workflow, docs: Dict[str, CorpusDocument]) -> str:
    chunks = [f"{doc_id}: {doc['text']}" for doc_id, doc in docs.items()]
    docs_text = "\n".join(chunks)
    return (
        f"You are summarizing documents for workflow '{workflow.name}' "
        f"({workflow.workflow_id}).\n"
        "Produce a concise summary in 3-5 sentences. Focus on key business outcomes,\n"
        "risks, and notable operational signals.\n\n"
        f"Documents:\n{docs_text}"
    )


def compute_classification(input_docs: Sequence[CorpusDocument]) -> str:
    if not input_docs:
        raise ValueError("compute_classification requires at least one input document.")

    max_doc = max(
        input_docs, key=lambda doc: CLASSIFICATION_RANK[doc["classification_level"]]
    )
    return max_doc["classification_level"]


def compute_output_compartments(input_docs: Sequence[CorpusDocument]) -> List[str]:
    compartments: set[str] = set()
    for doc in input_docs:
        compartments.update(doc["required_compartments"])
    return sorted(compartments)


def compute_classification_label(summary: str) -> str:
    text = summary.lower()
    risk_terms = ("risk", "vulnerability", "delay", "incident", "issue")
    growth_terms = ("growth", "increase", "improved", "expanded", "gained")

    risk_score = sum(1 for term in risk_terms if term in text)
    growth_score = sum(1 for term in growth_terms if term in text)

    if risk_score > growth_score and risk_score > 0:
        return "risk"
    if growth_score > risk_score and growth_score > 0:
        return "growth"
    return "neutral"


def run_workflow(
    workflow: Workflow, corpus: Dict[str, CorpusDocument], model_name: str
) -> Dict[str, Any]:
    docs, denied_docs = fetch_documents_for_workflow(workflow, workflow.document_ids, corpus)
    if not docs:
        return {
            "workflow_id": workflow.workflow_id,
            "workflow_name": workflow.name,
            "document_ids_requested": workflow.document_ids,
            "document_ids_included": [],
            "documents_denied": denied_docs,
            "output_classification_level": "",
            "output_compartments": [],
            "summary": "",
            "classification_label": "not_run",
            "status": "skipped_no_authorized_documents",
        }

    doc_values = list(docs.values())
    output_classification_level = compute_classification(doc_values)
    output_compartments = compute_output_compartments(doc_values)
    prompt = build_summary_prompt(workflow, docs)

    # Fresh LLM instance per workflow: no shared client object, no shared history.
    llm = ChatVertexAI(model=model_name, temperature=0.2)
    response = llm.invoke([HumanMessage(content=prompt)])
    summary = response.content if isinstance(response.content, str) else str(response.content)
    label = compute_classification_label(summary)

    return {
        "workflow_id": workflow.workflow_id,
        "workflow_name": workflow.name,
        "document_ids_requested": workflow.document_ids,
        "document_ids_included": list(docs.keys()),
        "documents_denied": denied_docs,
        "output_classification_level": output_classification_level,
        "output_compartments": output_compartments,
        "summary": summary,
        "classification_label": label,
        "status": "completed",
    }


def run_orchestrator(
    workflows: Sequence[Workflow],
    corpus: Dict[str, CorpusDocument],
    model_name: str,
    workflow_id: str | None = None,
) -> List[Dict[str, Any]]:
    selected = workflows
    if workflow_id:
        selected = [w for w in workflows if w.workflow_id == workflow_id]
        if not selected:
            raise ValueError(f"Workflow '{workflow_id}' was not found in workflows.json.")

    outputs = []
    for workflow in selected:
        outputs.append(run_workflow(workflow, corpus, model_name))
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Run document-summary workflows.")
    parser.add_argument(
        "--workflows",
        default="workflows.json",
        help="Path to workflows JSON file.",
    )
    parser.add_argument(
        "--corpus",
        default="corpus.json",
        help="Path to corpus JSON file.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Vertex model to use (e.g. gemini-1.5-flash or gemini-1.5-pro).",
    )
    parser.add_argument(
        "--workflow-id",
        default=None,
        help="Optional single workflow id to run.",
    )
    parser.add_argument(
        "--output",
        default="workflow_results.json",
        help="Output file path for JSON results.",
    )
    args = parser.parse_args()

    workflows = load_workflows(Path(args.workflows))
    corpus = load_json(Path(args.corpus))
    results = run_orchestrator(workflows, corpus, args.model, args.workflow_id)

    with Path(args.output).open("w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
