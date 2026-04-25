import json
import subprocess
from pathlib import Path

import streamlit as st

RESULTS_PATH = Path("workflow_results.json")
TESTS_PATH = Path("output/test_results.json")

# Classification label → (icon, text colour, background colour)
LABEL_PALETTE = {
    "TOP SECRET": ("🔴", "#7b1c24", "#f8d7da"),
    "SECRET":     ("🟡", "#856404", "#fff3cd"),
    "CONFIDENTIAL": ("🟢", "#1a5c2a", "#d4edda"),
}


def label_style(label: str):
    for key, style in LABEL_PALETTE.items():
        if label.upper().startswith(key):
            return style
    return ("⚪", "#444", "#f0f0f0")


def classification_badge(label: str) -> str:
    icon, fg, bg = label_style(label)
    return (
        f'<span style="background:{bg};color:{fg};padding:3px 12px;'
        f'border-radius:5px;font-weight:700;font-size:0.88em;">'
        f'{icon}&nbsp;{label}</span>'
    )


def test_badge(status: str) -> str:
    if status == "PASS":
        fg, bg = "#155724", "#d4edda"
    else:
        fg, bg = "#7b1c24", "#f8d7da"
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 9px;'
        f'border-radius:4px;font-weight:700;font-size:0.82em;">{status}</span>'
    )


# ── Page setup ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Pipeline Orchestrator", layout="wide")
st.title("Classified Pipeline Orchestrator")
st.caption("All codenames, compartments, and classification levels are fictional.")

# ── Run Pipeline button ───────────────────────────────────────────────────────
if st.button("▶ Run Pipeline", type="primary"):
    with st.spinner("Running pipeline…"):
        r1 = subprocess.run(
            ["python3", "pipeline_orchestrator.py"],
            capture_output=True, text=True,
        )
    if r1.returncode != 0:
        st.error("Pipeline failed")
        st.code(r1.stderr)
        st.stop()

    with st.spinner("Running tests…"):
        r2 = subprocess.run(
            ["python3", "test_harness.py"],
            capture_output=True, text=True,
        )
    if r2.returncode != 0:
        st.error("Test harness failed")
        st.code(r2.stderr)
        st.stop()

    st.success("Pipeline complete — results updated.")
    st.rerun()

# ── Load data ─────────────────────────────────────────────────────────────────
if not RESULTS_PATH.exists():
    st.info("No results yet. Click **▶ Run Pipeline** to start.")
    st.stop()

results: list = json.loads(RESULTS_PATH.read_text())
tests: list = json.loads(TESTS_PATH.read_text()) if TESTS_PATH.exists() else []

# ── Test Results ──────────────────────────────────────────────────────────────
if tests:
    st.header("Test Results")
    pass_count = sum(1 for t in tests if t["status"] == "PASS")
    total = len(tests)

    if pass_count == total:
        st.success(f"**{pass_count} / {total} passing**")
    else:
        st.warning(f"**{pass_count} / {total} passing**")

    for t in tests:
        st.markdown(
            f'{test_badge(t["status"])}&nbsp;&nbsp;'
            f'<span style="font-weight:600">{t["test_id"]}</span>'
            f'&nbsp;—&nbsp;{t["detail"]}',
            unsafe_allow_html=True,
        )
    st.divider()

# ── Workflow Results ──────────────────────────────────────────────────────────
st.header("Workflow Results")

PIPELINE_LABEL = {
    "WF-LOGISTICS": "1 of 5",
    "WF-SIGINT":    "2 of 5",
    "WF-IMINT":     "3 of 5",
    "WF-FUSION":    "4 of 5",
    "WF-BRIEFING":  "5 of 5",
}

for wf in results:
    wid   = wf["workflow_id"]
    name  = wf.get("workflow_name", wid)
    label = wf.get("classification_label", "")
    status = wf.get("status", "")
    summary = wf.get("summary", "")

    icon, _, _ = label_style(label)
    header = f"{PIPELINE_LABEL.get(wid, '')} &nbsp;·&nbsp; **{wid}** — {name} &nbsp; {icon} `{label}`"

    with st.expander(f"{wid} — {name}  {icon} {label}"):
        # Classification badge
        st.markdown(classification_badge(label), unsafe_allow_html=True)
        st.write("")

        if status in ("skipped_no_authorized_documents", "skipped_no_upstream_text"):
            st.warning(f"Skipped: {status.replace('_', ' ')}")
            continue

        # Summary
        st.subheader("Summary")
        st.write(summary if summary else "*No summary generated.*")

        # Metadata columns
        left, right = st.columns(2)
        with left:
            docs_in = wf.get("document_ids_included", [])
            if docs_in:
                st.markdown(f"**Docs included:** {', '.join(docs_in)}")
            else:
                st.markdown("**Docs included:** *(upstream text workflow)*")

            comps = wf.get("output_compartments", [])
            if comps:
                st.markdown(f"**Compartments:** {', '.join(comps)}")

        with right:
            denied = wf.get("documents_denied", {})
            if denied:
                st.markdown("**Docs denied:**")
                for doc_id, reason in denied.items():
                    st.markdown(f"- `{doc_id}`: {reason}")
            else:
                st.markdown("**Docs denied:** none")

        # Downgrade gate artifacts (WF-BRIEFING only)
        if "downgrade_to_secret" in wf:
            st.write("")
            with st.expander("Downgrade gate — internal artifacts"):
                st.markdown("**Stage 1 — regex redacted**")
                st.code(wf["downgrade_to_secret"]["stage1_redacted_text"], language="text")
                st.markdown("**Stage 2 — LLM rewritten (SECRET-safe)**")
                st.code(wf["downgrade_to_secret"]["stage2_rewritten_text"], language="text")

        # Input texts (for text-based workflows)
        input_texts = wf.get("input_texts", [])
        if input_texts and not wf.get("document_ids_included"):
            with st.expander("Upstream inputs passed to this workflow"):
                for i, txt in enumerate(input_texts, 1):
                    st.markdown(f"**Input {i}**")
                    st.write(txt)
