import hashlib
import json
import math
from copy import deepcopy
from typing import Any, Dict


def _assert_json_native(value: Any, path: str = "$") -> None:
    """Ensure *value* only contains JSON-native types so serialization and
    hashing are deterministic.

    JSON-native means: None / bool / int / finite float / str / list / tuple
    (serialized as array) / dict with str keys. Anything else (custom objects,
    non-str dict keys, NaN/inf floats, sets, bytes, ...) is rejected so the
    result never silently depends on ``str()`` or ``sort_keys`` behaviour on
    mixed-type keys.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-JSON value at {path}: {value!r}")
        return
    if isinstance(value, (list, tuple)):
        for idx, item in enumerate(value):
            _assert_json_native(item, f"{path}[{idx}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"non-JSON dict key at {path}: {key!r}")
            _assert_json_native(item, f"{path}.{key}")
        return
    raise ValueError(f"non-JSON type at {path}: {type(value).__name__}")


def canonical_json(data: Any) -> str:
    _assert_json_native(data)
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


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

    # Params/gates/score/scenario must be JSON-native: they are hashed into the
    # preset identity, and non-native values would make the hash depend on
    # str() or crash sort_keys. _assert_json_native (via stable_hash) raises
    # ValueError with the offending path.
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
