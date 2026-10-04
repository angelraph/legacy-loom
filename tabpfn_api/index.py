"""The TabPFN planner as its own Vercel function.

The TabPFN client needs pandas, pyarrow, scipy and scikit-learn. Together with the main app that
is over Vercel's 500 MB function limit, so the planner gets a function of its own. Locally and on
Render the main app runs the planner in process and this file is not used.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException  # noqa: E402

from app import planner, store  # noqa: E402

app = FastAPI(title="Legacy Loom planner")


@app.get("/api/planner")
def plan(asked_by: str | None = None):
    try:
        return planner.plan(store.list_sessions(), asked_by=asked_by)
    except Exception as e:
        raise HTTPException(502, f"TabPFN call failed: {e}")
