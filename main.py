"""Entry point.

Start the app with `python main.py`.

Why this file exists: the `uvicorn` console script does not always put the
project folder on `sys.path`, and with `--reload` on Windows the worker is a
fresh spawned process that inherits even less. The result is
`ModuleNotFoundError: No module named 'backend'` even though the folder is
right there. Inserting the path explicitly here removes that whole class of
problem, regardless of how or from where the script is launched.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import uvicorn  # noqa: E402  (import after sys.path is fixed)


def main():
    port = int(os.environ.get("PORT", 8000))
    reload_on = os.environ.get("RELOAD", "0") == "1"
    print(f"\n  Material Intelligence  ->  http://127.0.0.1:{port}")
    print("  Press CTRL+C to stop\n")
    if reload_on:
        uvicorn.run("backend.api:app", host="127.0.0.1", port=port,
                    reload=True, reload_dirs=[str(ROOT)], app_dir=str(ROOT))
    else:
        from backend.api import app
        uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
