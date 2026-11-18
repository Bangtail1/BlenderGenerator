"""Persistent management of BlendSwap Collection Rules (permanent defaults & next-run overrides)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from ..config import Settings
from ..db import VaultDatabase
from .models import CollectionRulesConfig

KEY_PERMANENT_RULES = "ref_collection_rules_permanent"
KEY_NEXT_RUN_RULES = "ref_collection_rules_next_run"


class CollectionRulesManager:
    """Stores, validates, and resolves permanent vs one-shot next-run collection rules."""

    def __init__(self, settings: Settings, db: VaultDatabase) -> None:
        self.settings = settings
        self.db = db

    @property
    def rules_file_path(self) -> Path:
        return (self.settings.resolved_reference_library_dir / "database" / "collection_rules.json").resolve()

    def default_rules(self) -> CollectionRulesConfig:
        return CollectionRulesConfig(
            daily_target=max(1, min(50, int(self.settings.collection_daily_target))),
            preferred_categories=[],
            excluded_categories=[],
            preferred_licenses=[],
            min_downloads=0,
            preferred_modeling_styles=[],
            preferred_poly_complexity="any",
            preferred_file_format="blend",
            diversity_enabled=True,
            max_per_category_per_cycle=2,
            collection_time=self.settings.collection_schedule_time,
            timezone=self.settings.collection_timezone,
            excluded_candidate_ids=[],
            scope="permanent",
        )

    def get_permanent_rules(self) -> CollectionRulesConfig:
        raw_db = self.db.get_setting(KEY_PERMANENT_RULES)
        if raw_db:
            try:
                return CollectionRulesConfig.model_validate_json(raw_db)
            except Exception:
                pass
        if self.rules_file_path.is_file():
            try:
                data = json.loads(self.rules_file_path.read_text(encoding="utf-8"))
                model = CollectionRulesConfig.model_validate(data)
                self.db.set_setting(KEY_PERMANENT_RULES, model.model_dump_json())
                return model
            except Exception:
                pass
        defaults = self.default_rules()
        self._persist_permanent(defaults)
        return defaults

    def get_next_run_override(self) -> Optional[CollectionRulesConfig]:
        raw = self.db.get_setting(KEY_NEXT_RUN_RULES)
        if not raw:
            return None
        try:
            return CollectionRulesConfig.model_validate_json(raw)
        except Exception:
            return None

    def get_active_rules(self) -> tuple[CollectionRulesConfig, bool]:
        """Return (effective_rules, has_next_run_override)."""
        nxt = self.get_next_run_override()
        if nxt is not None:
            return nxt, True
        return self.get_permanent_rules(), False

    def get_rules_bundle(self) -> dict[str, Any]:
        perm = self.get_permanent_rules()
        nxt = self.get_next_run_override()
        active = nxt or perm
        return {
            "active": active.model_dump(),
            "permanent": perm.model_dump(),
            "next_run_override": nxt.model_dump() if nxt else None,
            "has_next_run_override": nxt is not None,
            "defaults": self.default_rules().model_dump(),
        }

    def save_rules(self, rules: CollectionRulesConfig, *, scope: str = "permanent") -> dict[str, Any]:
        clean = rules.model_copy(update={"scope": scope})
        if scope == "next_run":
            self.db.set_setting(KEY_NEXT_RUN_RULES, clean.model_dump_json())
        else:
            clean = clean.model_copy(update={"scope": "permanent"})
            self._persist_permanent(clean)
            # Clear stale next-run override when user saves permanent rules
            self.db.set_setting(KEY_NEXT_RUN_RULES, "")
        return self.get_rules_bundle()

    def consume_next_run_override(self) -> Optional[CollectionRulesConfig]:
        nxt = self.get_next_run_override()
        if nxt is not None:
            self.db.set_setting(KEY_NEXT_RUN_RULES, "")
        return nxt

    def restore_defaults(self) -> dict[str, Any]:
        defaults = self.default_rules()
        self._persist_permanent(defaults)
        self.db.set_setting(KEY_NEXT_RUN_RULES, "")
        return self.get_rules_bundle()

    def _persist_permanent(self, rules: CollectionRulesConfig) -> None:
        self.db.set_setting(KEY_PERMANENT_RULES, rules.model_dump_json())
        path = self.rules_file_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(rules.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, path)
