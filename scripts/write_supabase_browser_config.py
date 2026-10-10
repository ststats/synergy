from pathlib import Path
from build_tools.browser_config import write_config

if __name__ == "__main__":
    write_config(Path(__file__).resolve().parents[1] / "docs/supabase-config.js", "SYNERGY_SUPABASE_CONFIG")
