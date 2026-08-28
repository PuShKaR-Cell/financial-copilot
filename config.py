"""Central config loader — Step 5.

Reads variables from .env. This file never contains actual
secret values — only the logic to read them.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    llm_api_key = os.getenv("LLM_API_KEY", "")
    llm_provider = os.getenv("LLM_PROVIDER", "ollama")
    ollama_model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    edgar_user_agent = os.getenv("EDGAR_USER_AGENT", "")
    alpha_vantage_api_key = os.getenv("ALPHA_VANTAGE_API_KEY", "")
    fred_api_key = os.getenv("FRED_API_KEY", "")
    langfuse_public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    langfuse_secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    postgres_url = os.getenv("POSTGRES_URL", "")


settings = Settings()


def check_config():
    required = ["edgar_user_agent", "qdrant_url", "postgres_url"]
    missing = [name for name in required if not getattr(settings, name)]
    if missing:
        print("Missing required config:", ", ".join(missing))
    else:
        print("config loaded")


if __name__ == "__main__":
    check_config()	