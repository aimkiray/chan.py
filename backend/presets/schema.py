import hashlib
import json
from copy import deepcopy
from typing import Any, Dict


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(data: Any, length: int = 12) -> str:
    raw = canonical_json(data).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[: int(length or 12)]


def normalize_preset(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("preset must be a dict")
    preset = deepcopy(raw)
    name = str(preset.get("name") or "").strip()
    if not name:
        raise ValueError("preset.name is required")
    version = str(preset.get("version") or "v1").strip() or "v1"
    params = preset.get("params") if isinstance(preset.get("params"), dict) else {}
    gates = preset.get("gates") if isinstance(preset.get("gates"), list) else []
    score = preset.get("score") if isinstance(preset.get("score"), dict) else {}
    scenario = preset.get("scenario") if isinstance(preset.get("scenario"), dict) else {}

    preset["name"] = name
    preset["version"] = version
    preset["status"] = str(preset.get("status") or "experimental").strip() or "experimental"
    preset["params"] = params
    preset["gates"] = gates
    preset["score"] = score
    preset["scenario"] = scenario
    preset["identity"] = {
        "name": name,
        "version": version,
        "status": preset["status"],
        "params_hash": stable_hash(params),
        "gates_hash": stable_hash(gates),
        "preset_hash": stable_hash(
            {
                "name": name,
                "version": version,
                "params": params,
                "gates": gates,
                "score": score,
                "scenario": scenario,
            }
        ),
    }
    return preset
