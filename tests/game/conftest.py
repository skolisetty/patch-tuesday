import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from game import config, world  # noqa: E402


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    """Tests never read the real .env and never call a real LLM."""
    monkeypatch.setattr(config, "_loaded", True)
    for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "BAND_ENABLED", "TELEM_ENABLED", "TICK_SECONDS",
              "PT_TICK_SECONDS", "BAND_API_KEY", "BAND_CHAT_ID", "TELEM_API_KEY", "LLM_REASONS"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(scope="session")
def mock_dir(tmp_path_factory):
    from game import make_mock
    d = tmp_path_factory.mktemp("serving_mock")
    make_mock.main(d)
    return d


@pytest.fixture(scope="session")
def serving(mock_dir):
    return world.load_serving(mock_dir, "serving_mock")


@pytest.fixture(scope="session")
def next_serving(mock_dir):
    return world.load_serving(mock_dir / "next", "serving_mock")
