from pathlib import Path
import json
from typing import Callable
from unittest.mock import MagicMock
from tests.fixtures_pack_selection import make_output_tree


def make_minimal_output_tree(tmp_path, *, domain="alpha", lang="en") -> Path:
    """Valid output/ tree. One domain, one article, 5 questions."""
    return make_output_tree(tmp_path, domain=domain, lang=lang, articles=1, questions_per_article=5)


def make_empty_spec() -> dict:
    return {"schema_version": 1, "packs": []}


def make_valid_pack_def(pack_id="test-pack", *, with_selection=False, kind="campaign") -> dict:
    """Valid definition. with_selection adds {"alpha": {"articles": ["art-1"], "questions_per_article": 5}}."""
    p = {
        "id": pack_id,
        "kind": kind,
        "domains": ["alpha"],
        "languages": ["en"],
        "campaigns": [],
    }
    if with_selection:
        p["selection"] = {"alpha": {"articles": ["art-1"], "questions_per_article": 5}}
    return p


def make_valid_builder_form(pack_id="test-pack") -> dict:
    """Form data dict ready for POST /packs/builder."""
    return {
        "id": pack_id,
        "kind": "campaign",
        "domains": ["alpha"],
        "languages": ["en"],
        "campaigns": [],
    }


def make_invalid_pack_defs() -> dict[str, tuple[dict, str]]:
    """Returns {case_name: (pack_dict, expected_error_substring)}."""
    return {
        "empty_id": (
            {"id": "", "kind": "campaign", "domains": ["alpha"], "languages": ["en"]},
            "pack id required"
        ),
        "bad_id_chars": (
            {"id": "BAD ID!", "kind": "campaign", "domains": ["alpha"], "languages": ["en"]},
            "invalid pack id"
        ),
        "bad_kind": (
            {"id": "x", "kind": "bogus", "domains": ["alpha"], "languages": ["en"]},
            "invalid kind"
        ),
        "bad_lang": (
            {"id": "x", "kind": "campaign", "domains": ["alpha"], "languages": ["xx"]},
            "not in configured langs"
        ),
        "selection_unknown_domain": (
            {
                "id": "x", "kind": "campaign", "domains": ["alpha"], "languages": ["en"],
                "selection": {"beta": {"articles": ["art-1"], "questions_per_article": 5}}
            },
            "not in pack domains"
        ),
        "selection_bad_qpa": (
            {
                "id": "x", "kind": "campaign", "domains": ["alpha"], "languages": ["en"],
                "selection": {"alpha": {"articles": ["art-1"], "questions_per_article": 0}}
            },
            "questions_per_article"
        ),
        "selection_empty_articles": (
            {
                "id": "x", "kind": "campaign", "domains": ["alpha"], "languages": ["en"],
                "selection": {"alpha": {"articles": [""], "questions_per_article": 5}}
            },
            "contains invalid slug"
        ),
    }


def make_corrupt_packs_spec_file(path: Path) -> Path:
    """Write '{not valid json' to path. load_spec raises ValueError."""
    path.write_text("{not valid json", encoding="utf-8")
    return path


def make_pack_in_spec_no_artifact(packs_json: Path, pack_id: str) -> Path:
    """Seed packs.json with a pack definition but no corresponding .qap on disk."""
    data = {"schema_version": 1, "packs": [make_valid_pack_def(pack_id)]}
    packs_json.write_text(json.dumps(data), encoding="utf-8")
    return packs_json


def make_broken_qap_file(qaps_dir: Path, pack_id: str, *, kind: str = "empty") -> Path:
    """Create .qap file in invalid state."""
    qaps_dir.mkdir(parents=True, exist_ok=True)
    p = qaps_dir / f"{pack_id}.qap"
    if kind == "empty":
        p.write_bytes(b"")
    elif kind == "corrupt":
        p.write_bytes(b"this is random non-zip bytes")
    elif kind == "truncated":
        p.write_bytes(b"PK\x03\x04")
    return p


def make_subprocess_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """MagicMock mimicking subprocess.CompletedProcess."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def make_cli_mock_response(status_code=201, json_data=None) -> MagicMock:
    """Mimics requests.Response with .status_code, .json(), .text, .headers."""
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_data or {}
    m.text = json.dumps(json_data or {})
    m.headers = {"content-type": "application/json"}
    return m


def make_cli_mock_post(captured: dict, *, status_code=201, json_data=None) -> Callable:
    """Returns fake_post function storing captured['json'] = json."""
    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return make_cli_mock_response(status_code=status_code, json_data=json_data)
    return fake_post
