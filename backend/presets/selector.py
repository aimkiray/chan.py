import math
from typing import Any, Dict, List, Optional, Tuple

# Preset statuses that may be listed/evaluated but must never win selection.
NON_SELECTABLE_STATUSES = frozenset({"disabled", "archived", "guardrail"})


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


def _resolve_term_value(term: Dict[str, Any], ctx: Dict[str, Any]) -> float:
    metric = str(term.get("metric") or "").strip()
    raw = _get_path(ctx, metric, term.get("missing", 0.0))
    value = _as_number(raw)
    if value is None or math.isnan(value):
        fallback = _as_number(term.get("missing", 0.0))
        value = fallback if fallback is not None else 0.0
    if math.isinf(value):
        fallback = _as_number(term.get("infinite", 10.0))
        value = fallback if fallback is not None else 10.0
    return float(value)


def _resolve_term_weight(term: Dict[str, Any]) -> float:
    weight = _as_number(term.get("weight", 1.0))
    if weight is None or math.isnan(weight):
        return 1.0
    return float(weight)


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
            "required": required,
            "passed": bool(ok),
            "message": gate.get("message"),
        }
        checked.append(row)
        if not ok:
            if required:
                failed.append(row)
            else:
                # Optional gates are advisory: a failed comparison degrades to a
                # warning instead of a hard failure.
                warnings.append(
                    {
                        "metric": metric,
                        "reason": "optional_failed",
                        "op": op,
                        "value": expected,
                        "actual": actual,
                        "message": gate.get("message"),
                    }
                )
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
        value = _resolve_term_value(term, ctx)
        weight = _resolve_term_weight(term)
        contribution = float(value) * float(weight)
        total += contribution
        details.append({"metric": metric, "value": value, "weight": weight, "contribution": contribution})
    return {"preset": preset.get("name"), "version": preset.get("version"), "score": float(total), "terms": details}


def _normalize_candidate_scores(rows: List[Dict[str, Any]]) -> None:
    """Populate ``row["score_normalized"]`` for each candidate row.

    Raw ``score`` values are not comparable across presets because each preset
    defines its own term weights and scales. To rank candidates we min-max
    normalize every score-term metric across the candidate set, then compute a
    weighted mean divided by the sum of |weight| per preset. The result is a
    scale-invariant score in roughly [-1, 1] that is comparable across presets.
    """
    metric_values: Dict[str, List[float]] = {}
    for row in rows:
        terms = (row.get("score_detail") or {}).get("terms") or []
        for term in terms:
            value = _as_number(term.get("value"))
            if value is None or math.isnan(value) or math.isinf(value):
                continue
            metric_values.setdefault(str(term.get("metric") or ""), []).append(value)
    bounds: Dict[str, Tuple[float, float]] = {}
    for metric, values in metric_values.items():
        bounds[metric] = (min(values), max(values))
    for row in rows:
        total = 0.0
        weight_sum = 0.0
        terms = (row.get("score_detail") or {}).get("terms") or []
        for term in terms:
            value = _as_number(term.get("value"))
            weight = _as_number(term.get("weight"))
            if (
                value is None
                or weight is None
                or math.isnan(value)
                or math.isnan(weight)
                or math.isinf(value)
                or math.isinf(weight)
            ):
                continue
            lo, hi = bounds.get(str(term.get("metric") or ""), (value, value))
            norm = 0.5 if hi <= lo else (value - lo) / (hi - lo)
            total += weight * norm
            weight_sum += abs(weight)
        row["score_normalized"] = float(total / weight_sum) if weight_sum > 0 else 0.0


def select_best_preset(
    presets: List[Dict[str, Any]],
    metrics_by_preset: Optional[Dict[str, Dict[str, Any]]] = None,
    metrics: Optional[Dict[str, Any]] = None,
    profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    metrics_by_preset = metrics_by_preset if isinstance(metrics_by_preset, dict) else {}
    rows = []
    excluded = []
    for preset in presets or []:
        if not isinstance(preset, dict):
            continue
        name = str(preset.get("name") or "")
        item_metrics = metrics_by_preset.get(name)
        if not isinstance(item_metrics, dict):
            item_metrics = metrics
        gate = evaluate_preset(preset, item_metrics or {}, profile)
        score = score_preset(preset, item_metrics or {}, profile)
        status = str(preset.get("status") or "").strip().lower()
        eligible = status not in NON_SELECTABLE_STATUSES
        row = {
            "preset": name,
            "version": preset.get("version"),
            "status": preset.get("status"),
            "identity": preset.get("identity"),
            "eligible": eligible,
            "passed": bool(gate.get("passed")),
            "score": float(score.get("score", 0.0)),
            "score_normalized": 0.0,
            "gate": gate,
            "score_detail": score,
        }
        if not eligible:
            # Guardrail/disabled/archived presets are still evaluated for
            # transparency but never enter the candidate ranking.
            row["excluded_reason"] = f"status:{status or 'unknown'}"
            excluded.append(row)
            continue
        rows.append(row)
    _normalize_candidate_scores(rows)
    rows.sort(key=lambda r: (bool(r.get("passed")), float(r.get("score_normalized", 0.0))), reverse=True)
    return {"best": rows[0] if rows else None, "candidates": rows, "excluded": excluded}
