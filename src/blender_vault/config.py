"""Application configuration and environment path resolution."""

from __future__ import annotations

import glob
import os
import shutil
from pathlib import Path
from typing import Optional

from pydantic import Field
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
        ):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def parsed_gdrive_scopes(self) -> list[str]:
        return [s.strip() for s in self.gdrive_scopes.split(",") if s.strip()]
