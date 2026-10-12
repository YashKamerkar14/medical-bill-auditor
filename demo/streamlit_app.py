"""Entry point of the hosted public demo (Streamlit Community Cloud).

Turns on demo mode (example bills only, no uploads, rules only, no model, no
keys) and runs the same app as a local install. The package is used from src/
directly, so the demo needs only demo/requirements.txt.

Run it locally the same way: uv run streamlit run demo/streamlit_app.py
"""

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["BILL_AUDITOR_DEMO"] = "1"
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

# Streamlit re-runs this file on every interaction; run_path re-runs the app each time (an import would not).
runpy.run_path(str(ROOT / "src" / "bill_auditor" / "ui" / "app.py"), run_name="__main__")
