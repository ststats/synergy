"""Small file and roster helpers used by the Synergy web build."""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def atomic_write_json(path: Path, data, **json_kwargs) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    json_kwargs.setdefault("ensure_ascii", False)
    json_kwargs.setdefault("indent", 2)
    fd, temp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, **json_kwargs)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def safe_read_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[경고] {path}를 읽을 수 없어 기본값으로 대체합니다: {exc}", file=sys.stderr)
        return default


def normalize_elo_id(value) -> str:
    if value in (None, ""):
        return ""
    try:
        return str(int(float(str(value).strip())))
    except (TypeError, ValueError, OverflowError):
        return ""


def validate_and_clean_members(members: list) -> list:
    cleaned = []
    for index, member in enumerate(members):
        if not isinstance(member, dict):
            print(f"[경고] members[{index}] 형식이 올바르지 않습니다.", file=sys.stderr)
            continue
        item = dict(member)
        item["id"] = str(item.get("id") or "").strip()
        item["nickname"] = str(item.get("nickname") or "").strip()
        if not item["id"] or not item["nickname"]:
            print(f"[경고] members[{index}]에 SOOP ID 또는 닉네임이 없습니다.", file=sys.stderr)
            continue
        if item.get("elo_id") is not None:
            normalized = normalize_elo_id(item["elo_id"])
            item["elo_id"] = int(normalized) if normalized else None
        cleaned.append(item)
    return cleaned
