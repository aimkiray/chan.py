import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .schema import normalize_preset


class PresetNotFoundError(KeyError):
    pass


class PresetManager:
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = Path(config_path) if config_path else Path(__file__).with_name("strategy_presets.json")
        self._cache_mtime = None
        self._cache = None

    def _load_config(self) -> Dict[str, Any]:
        try:
            mtime = self.config_path.stat().st_mtime
        except FileNotFoundError:
            return {"schema_version": 1, "presets": []}
        if self._cache is not None and self._cache_mtime == mtime:
            return self._cache
        with self.config_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        presets = [normalize_preset(p) for p in (data.get("presets") or [])]
        self._cache = {
            "schema_version": data.get("schema_version", 1),
            "updated_at": data.get("updated_at"),
            "presets": presets,
        }
        self._cache_mtime = mtime
        return self._cache

    def metadata(self) -> Dict[str, Any]:
        data = self._load_config()
        return {
            "schema_version": data.get("schema_version", 1),
            "updated_at": data.get("updated_at"),
        }

    def list_presets(self, include_disabled: bool = False, status: Optional[str] = None) -> List[Dict[str, Any]]:
        data = self._load_config()
        want_status = str(status or "").strip().lower()
        items = []
        for preset in data.get("presets") or []:
            preset_status = str(preset.get("status") or "").strip().lower()
            if not include_disabled and preset_status in {"disabled", "archived"}:
                continue
            if want_status and preset_status != want_status:
                continue
            items.append(deepcopy(preset))
        return items

    def get_preset(self, name: str, include_disabled: bool = True) -> Dict[str, Any]:
        want = str(name or "").strip()
        if not want:
            raise PresetNotFoundError("preset name is required")
        for preset in self.list_presets(include_disabled=include_disabled):
            if preset.get("name") == want or f"{preset.get('name')}@{preset.get('version')}" == want:
                return deepcopy(preset)
        raise PresetNotFoundError(f"strategy preset not found: {want}")

    def get_identity(self, name_or_preset: Any) -> Dict[str, Any]:
        preset = name_or_preset if isinstance(name_or_preset, dict) else self.get_preset(str(name_or_preset))
        return deepcopy(preset.get("identity") or normalize_preset(preset).get("identity") or {})

    @staticmethod
    def request_field_set(req: Any) -> Set[str]:
        for attr in ("model_fields_set", "__fields_set__"):
            value = getattr(req, attr, None)
            if value is not None:
                try:
                    return set(value)
                except Exception:
                    return set()
        return set()

    def apply_to_request(
        self,
        req: Any,
        preset_name: Optional[str] = None,
        preset_overrides: bool = True,
        include_unknown: bool = False,
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        name = str(preset_name or getattr(req, "preset_name", "") or "").strip()
        if not name:
            return None, {}
        preset = self.get_preset(name)
        params = preset.get("params") if isinstance(preset.get("params"), dict) else {}
        explicit = self.request_field_set(req)
        applied = []
        skipped_explicit = []
        skipped_unknown = []
        for key, value in params.items():
            if not include_unknown and not hasattr(req, key):
                skipped_unknown.append(key)
                continue
            if not bool(preset_overrides) and key in explicit:
                skipped_explicit.append(key)
                continue
            try:
                setattr(req, key, deepcopy(value))
                applied.append(key)
            except Exception:
                skipped_unknown.append(key)
        audit = {
            "identity": self.get_identity(preset),
            "applied_params": applied,
            "skipped_explicit_params": skipped_explicit,
            "skipped_unknown_params": skipped_unknown,
            "preset_overrides": bool(preset_overrides),
        }
        return preset, audit

    def apply_to_params(
        self,
        base_params: Dict[str, Any],
        preset_name: str,
        explicit_keys: Optional[Iterable[str]] = None,
        preset_overrides: bool = True,
    ) -> Dict[str, Any]:
        preset = self.get_preset(preset_name)
        merged = dict(base_params or {})
        explicit = set(explicit_keys or [])
        for key, value in (preset.get("params") or {}).items():
            if bool(preset_overrides) or key not in explicit:
                merged[key] = deepcopy(value)
        merged["strategy_preset"] = self.get_identity(preset)
        return merged
