# Generated from ststats/staruniv/scripts/build_tools/browser_config.py; do not edit.
"""Write the public browser configuration atomically for either site."""
import json
import os
from pathlib import Path


def write_config(output: Path, name: str) -> None:
    url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_PUBLISHABLE_KEY") or "").strip()
    if not url.startswith("https://") or not key:
        raise SystemExit("HTTPS SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY are required")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = f"window.{name} = Object.freeze({json.dumps({'url': url, 'key': key})});\n"
    temporary = output.with_suffix(".js.tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, output)
    print(f"wrote {output}")
