"""Game metadata boundary. No scraping or unlicensed API use is enabled."""
from typing import Protocol

class GameMetadataProvider(Protocol):
    def search(self,query:str)->list[dict]: ...

class ManualProvider:
    """Always available fallback; import only data the editor has verified."""
    def search(self,query:str)->list[dict]:return []

PROVIDERS={'manual':ManualProvider()}
# IGDB/Steam adapters require access approval, current terms review and secrets.
# Do not add an unofficial scraping endpoint as an automatic fallback.
