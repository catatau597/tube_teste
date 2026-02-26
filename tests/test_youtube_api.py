import pytest
from core.youtube_api import YouTubeAPI

def test_youtube_api_instancia():
    api = YouTubeAPI(api_key="dummy")
    assert api.api_key == "dummy"
