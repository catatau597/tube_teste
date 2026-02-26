"""
core/youtube_api.py
-------------------
Responsabilidade: Interface para YouTube Data API v3.
Depende de: api_key passado pelo chamador
NÃO depende de: AppConfig, Flask, FastHTML, os.getenv

Exemplo de uso:
    yt = YouTubeAPI(api_key="dummy")
    print(yt.api_key)
"""
class YouTubeAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key
    # Métodos reais implementados na etapa posterior
