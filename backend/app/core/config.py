from pathlib import Path
from pydantic_settings import BaseSettings

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
WORKSPACE_DIR = BASE_DIR
DATA_DIR = WORKSPACE_DIR / "data"
CLIPS_DIR = DATA_DIR / "clips"
OUTPUT_DIR = DATA_DIR / "output"

for folder in [CLIPS_DIR, OUTPUT_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

ENV_FILE_PATH = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    # App Settings
    APP_NAME: str = "AAT — Autonomous YouTube Shorts Viral Engine"
    ENVIRONMENT: str = "production"
    LOG_LEVEL: str = "INFO"

    # Regional Strategy (Nigeria & Global Organic)
    DEFAULT_REGION: str = "NG"
    LANGUAGE: str = "en"

    # Google Gemini AI Brain (Video + Audio Multimodal Director — 3.8 Flash / 4 Argon Pool)
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.8-flash"

    # YouTube Official Data API v3 OAuth Credentials (for posting Shorts)
    YOUTUBE_CLIENT_ID: str = ""
    YOUTUBE_CLIENT_SECRET: str = ""
    YOUTUBE_REDIRECT_URI: str = "https://yomide01.github.io/callback"
    YOUTUBE_ACCESS_TOKEN: str = ""
    YOUTUBE_REFRESH_TOKEN: str = ""

    # Clip discovery (TikTok + Open Sources) and cloud<->local data bridge
    PEXELS_API_KEY: str = ""
    PIXABAY_API_KEY: str = ""
    GITHUB_TOKEN: str = ""
    GITHUB_REPO: str = "yomide01/aat-bot"

    # Telegram Human-in-the-Loop Dispatcher & Subagent Bridge
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # Video Production Standards (YouTube Shorts 9:16 Vertical)
    TARGET_FPS: int = 30
    TARGET_WIDTH: int = 1080
    TARGET_HEIGHT: int = 1920
    TARGET_BITRATE_KBPS: int = 12000
    TARGET_LUFS: float = -14.0

    class Config:
        env_file = str(ENV_FILE_PATH)
        extra = "ignore"


settings = Settings()
