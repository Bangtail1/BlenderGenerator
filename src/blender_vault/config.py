"""Application configuration and environment path resolution."""

from __future__ import annotations

import glob
import os
import shutil
from pathlib import Path
from typing import Any, Optional

from pydantic import Field, PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict


STANDARD_CATEGORIES: tuple[str, ...] = (
    "Architecture",
    "Furniture",
    "Characters",
    "Mechanical Objects",
    "Vehicles",
    "Household Objects",
    "Nature",
    "Miscellaneous",
)


def discover_blender_executable() -> Optional[str]:
    """Locate Blender executable on PATH or standard Windows/macOS/Linux locations."""
    env_val = os.environ.get("BLENDER_EXECUTABLE", "").strip()
    if env_val and Path(env_val).is_file():
        return str(Path(env_val))

    on_path = shutil.which("blender")
    if on_path:
        return on_path

    candidates: list[Path] = []
    for pattern in (
        r"C:\Program Files\Blender Foundation\Blender *\blender.exe",
        r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe",
    ):
        for match in glob.glob(pattern):
            p = Path(match)
            if p.is_file():
                candidates.append(p)

    if candidates:
        candidates.sort(key=lambda p: p.parent.name, reverse=True)
        return str(candidates[0])
    return None


class Settings(BaseSettings):
    """Runtime settings loaded from environment and optional .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    vault_host: str = "127.0.0.1"
    vault_port: int = 8765
    vault_data_dir: Path = Field(default=Path("./vault_storage"))
    vault_log_level: str = "INFO"

    blender_executable: Optional[str] = Field(default_factory=discover_blender_executable)
    blender_timeout_seconds: int = 300
    blender_render_engine: str = "BLENDER_EEVEE"
    blender_render_samples: int = 16
    blender_render_resolution: int = 768

    gdrive_client_secrets_path: Path = Field(default=Path("./credentials/client_secrets.json"))
    gdrive_token_path: Path = Field(default=Path("./credentials/gdrive_token.json"))
    gdrive_scopes: str = "https://www.googleapis.com/auth/drive.file"
    gdrive_root_folder_id: str = ""
    gdrive_root_folder_name: str = "Blender Asset Vault"
    vault_token_encryption_key: str = ""

    vision_qa_provider: str = "none"  # none | gemini | openai | ollama
    gemini_api_key: str = ""
    gemini_vision_model: str = "gemini-2.5-flash"
    openai_api_key: str = ""
    openai_vision_model: str = "gpt-4o-mini"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_vision_model: str = "qwen2.5vl:7b"

    external_3d_provider: str = "none"
    external_3d_api_key: str = ""

    # Branch 2: BlendSwap Reference Intelligence Engine
    blendswap_key: str = ""
    blendswap_base_url: str = "https://blendswap.com/api/v1"
    blendswap_timeout_seconds: float = 30.0
    reference_library_dir: Optional[Path] = None
    collection_daily_target: int = 7
    collection_schedule_frequency: str = "daily"
    collection_schedule_time: str = "03:00"
    collection_timezone: str = "Europe/London"
    collection_scheduler_enabled: bool = True
    collection_reset_recheck_minutes: int = 15
    collection_resume_missed_jobs: bool = True
    collection_schedule_config_path: Optional[Path] = None
    allow_credit_spend: bool = False
    reference_license_policy: str = "all_free"  # all_free | cc_only | permissive_only
    reference_drive_sync_enabled: bool = True

    @property
    def resolved_collection_schedule_config_path(self) -> Path:
        if self.collection_schedule_config_path is not None:
            return self.collection_schedule_config_path.resolve()
        return (self.resolved_reference_library_dir / "database" / "collection_schedule.json").resolve()

    # Pipeline 1 & 2: Roblox Asset Database & Automated QA System
    roblox_db_path: Optional[Path] = None
    roblox_backups_dir: Optional[Path] = None
    roblox_master_inventory_path: Path = Field(default=Path("./config/master_inventory.json"))
    roblox_qa_rules_path: Path = Field(default=Path("./config/qa_rules.json"))
    roblox_reports_dir: Optional[Path] = None
    roblox_output_dir: Optional[Path] = None
    roblox_default_export_format: str = "FBX"  # FBX | GLB | OBJ
    roblox_auto_register: bool = True
    roblox_auto_qa: bool = True

    _explicit_init_fields: set[str] = PrivateAttr(default_factory=set)

    def __init__(self, **values: Any) -> None:
        super().__init__(**values)
        self._explicit_init_fields = set(values.keys())

    @property
    def _is_default_vault_dir(self) -> bool:
        return self.vault_data_dir == Path("./vault_storage")

    def _should_isolate_roblox_path(self, field_name: str, env_var: str) -> bool:
        return (
            not self._is_default_vault_dir
            and field_name not in self._explicit_init_fields
            and env_var not in os.environ
        )

    @property
    def resolved_roblox_db_path(self) -> Path:
        if self._should_isolate_roblox_path("roblox_db_path", "ROBLOX_DB_PATH"):
            return self.vault_data_dir / "roblox_asset_library.db"
        if self.roblox_db_path is not None:
            return self.roblox_db_path
        if self._is_default_vault_dir:
            return Path("./data/roblox_asset_library.db")
        return self.vault_data_dir / "roblox_asset_library.db"

    @property
    def resolved_roblox_backups_dir(self) -> Path:
        if self._should_isolate_roblox_path("roblox_backups_dir", "ROBLOX_BACKUPS_DIR"):
            return self.vault_data_dir / "roblox_backups"
        if self.roblox_backups_dir is not None:
            return self.roblox_backups_dir
        if self._is_default_vault_dir:
            return Path("./data/backups")
        return self.vault_data_dir / "roblox_backups"

    @property
    def resolved_roblox_reports_dir(self) -> Path:
        if self._should_isolate_roblox_path("roblox_reports_dir", "ROBLOX_REPORTS_DIR"):
            return self.vault_data_dir / "roblox_reports"
        if self.roblox_reports_dir is not None:
            return self.roblox_reports_dir
        if self._is_default_vault_dir:
            return Path("./reports")
        return self.vault_data_dir / "roblox_reports"

    @property
    def resolved_roblox_output_dir(self) -> Path:
        if self._should_isolate_roblox_path("roblox_output_dir", "ROBLOX_OUTPUT_DIR"):
            return self.vault_data_dir / "roblox_output"
        if self.roblox_output_dir is not None:
            return self.roblox_output_dir
        if self._is_default_vault_dir:
            return Path("./output")
        return self.vault_data_dir / "roblox_output"

    @property
    def resolved_reference_library_dir(self) -> Path:
        if self.reference_library_dir is not None:
            return self.reference_library_dir
        return self.vault_data_dir / "reference_library"

    @property
    def db_path(self) -> Path:
        return self.vault_data_dir / "vault.db"

    @property
    def assets_dir(self) -> Path:
        return self.vault_data_dir / "assets"

    @property
    def imports_dir(self) -> Path:
        return self.vault_data_dir / "imports_original"

    @property
    def tmp_dir(self) -> Path:
        return self.vault_data_dir / "tmp"

    @property
    def logs_dir(self) -> Path:
        return self.vault_data_dir / "logs"

    @property
    def credentials_dir(self) -> Path:
        return self.gdrive_client_secrets_path.parent

    def ensure_directories(self) -> None:
        """Create required runtime directories cleanly."""
        for path in (
            self.vault_data_dir,
            self.assets_dir,
            self.imports_dir,
            self.tmp_dir,
            self.logs_dir,
            self.credentials_dir,
            self.resolved_roblox_db_path.parent,
            self.resolved_roblox_backups_dir,
            self.resolved_roblox_reports_dir,
            self.resolved_roblox_output_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def parsed_gdrive_scopes(self) -> list[str]:
        return [s.strip() for s in self.gdrive_scopes.split(",") if s.strip()]
