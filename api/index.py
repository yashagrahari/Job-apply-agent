"""Vercel serverless entrypoint (imports the FastAPI app from ../api.py)."""

import importlib.util
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_api_py = _root / "api.py"

spec = importlib.util.spec_from_file_location("job_apply_api", _api_py)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Could not load FastAPI app from {_api_py}")

_module = importlib.util.module_from_spec(spec)
sys.modules["job_apply_api"] = _module
spec.loader.exec_module(_module)

app = _module.app
