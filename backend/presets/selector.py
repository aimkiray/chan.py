import math
from typing import Any, Dict, List, Optional


def _get_path(data: Any, path: str, default: Any = None) -> Any:
    cur = data
    for part in str(path or "").split("."):
        if part == "":
            continue
        if isinstance(cur, dict) and part in cur:
            cur = cur.get(part)
            continue
        return default
    return cur


def _as_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return None
    raw = str(value).strip().lower()
    if raw in {"inf", "+inf", "infinity", "+infinity"}:
        return float("inf")
    if raw in {"-inf", "-infinity"}:
        return float("-inf")
    try:
        return float(raw)
    except Exception:
        return None


def _compare(actual: Any, op: str, expected: Any) -> bool:
    op = str(op or ">=").strip().lower()
    if op == "exists":
        return actual is not None
    if op == "absent":
        return actual is None
    if op in {"in", "not_in"}:
        vals = expected if isinstance(expected, list) else [expected]
        hit = actual in vals
        return hit if op == "in" else not hit
    if op in {"==", "eq"}:
        return actual == expected
    if op in {"!=", "ne"}:
        return actual != expected
    left = _as_number(actual)
    right = _as_number(expected)
    if left is None or right is None:
        return False
    if op in {">=", "gte"}:
        return left >= right
    if op in {">", "gt"}:
        return left > right
    if op in {"<=", "lte"}:
        return left <= right
    if op in {"<", "lt"}:
        return left < right
    return False


def evaluate_preset(preset: Dict[str, Any], metrics: Optional[Dict[str, Any]] = None, profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    metrics = metrics if isinstance(metrics, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    ctx = {"metrics": metrics, "profile": profile, **metrics}
    failed = []
    warnings = []
    checked = []
    for gate in preset.get("gates") or []:
        if not isinstance(gate, dict):
            continue
        metric = str(gate.get("metric") or "").strip()
        if not metric:
            continue
        required = bool(gate.get("required", True))
        actual = _get_path(ctx, metric, None)
        if actual is None and not required:
            warnings.append({"metric": metric, "reason": "missing_optional"})
            continue
        op = str(gate.get("op") or ">=")
        expected = gate.get("value")
        ok = _compare(actual, op, expected)
        row = {
            "metric": metric,
            "op": op,
            "value": expected,
            "actual": actual,
            "passed": bool(ok),
            "message": gate.get("message"),
        }
        checked.append(row)
        if not ok:
            failed.append(row)
    return {
        "preset": preset.get("name"),
        "version": preset.get("version"),
        "passed": len(failed) == 0,
        "failed": failed,
        "warnings": warnings,
        "checked": checked,
    }


def score_preset(preset: Dict[str, Any], metrics: Optional[Dict[str, Any]] = None, profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    metrics = metrics if isinstance(metrics, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    ctx = {"metrics": metrics, "profile": profile, **metrics}
    terms = ((preset.get("score") or {}).get("terms") or [])
    total = 0.0
    details = []
    for term in terms:
        if not isinstance(term, dict):
            continue
        metric = str(term.get("metric") or "").strip()
        raw = _get_path(ctx, metric, term.get("missing", 0.0))
        value = _as_number(raw)
        if value is None:
            value = _as_number(term.get("missing", 0.0)) or 0.0
        if math.isinf(value):
            value = float(term.get("infinite", 10.0))
        weight = _as_number(term.get("weight", 1.0)) or 1.0
        contribution = float(value) * float(weight)
        total += contribution
        details.append({"metric": metric, "value": value, "weight": weight, "contribution": contribution})
    return {"preset": preset.get("name"), "version": preset.get("version"), "score": float(total), "terms": details}


def select_best_preset(
    presets: List[Dict[str, Any]],
    metrics_by_preset: Optional[Dict[str, Dict[str, Any]]] = None,
    metrics: Optional[Dict[str, Any]] = None,
    profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    metrics_by_preset = metrics_by_preset if isinstance(metrics_by_preset, dict) else {}
    rows = []
    for preset in presets or []:
        name = str(preset.get("name") or "")
        item_metrics = metrics_by_preset.get(name) if isinstance(metrics_by_preset.get(name), dict) else metrics
        gate = evaluate_preset(preset, item_metrics or {}, profile)
        score = score_preset(preset, item_metrics or {}, profile)
        rows.append(
            {
                "preset": name,
                "version": preset.get("version"),
                "identity": preset.get("identity"),
                "passed": bool(gate.get("passed")),
                "score": float(score.get("score", 0.0)),
                "gate": gate,
                "score_detail": score,
            }
        )
    rows.sort(key=lambda r: (bool(r.get("passed")), float(r.get("score", 0.0))), reverse=True)
    return {"best": rows[0] if rows else None, "candidates": rows}
