from __future__ import annotations
import json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "supabase-config.js"

url = (os.getenv("SUPABASE_URL") or "").strip()
key = (os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or "").strip()
if not url or not key:
    raise SystemExit("SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY are required")

OUT.write_text(
    "window.SYNERGY_SUPABASE_CONFIG = " + json.dumps({"url": url, "key": key}) + ";\n",
    encoding="utf-8",
)
print(f"wrote {OUT}")
