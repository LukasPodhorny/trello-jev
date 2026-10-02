"""Thin wrapper over the Trello REST API.

Auth (``key`` + ``token`` query params) is attached centrally to the
underlying httpx client, so no call site ever builds or logs a full URL.
See https://developer.atlassian.com/cloud/trello/rest/
"""

from __future__ import annotations

import time
from collections.abc import Mapping

import httpx
from pydantic import TypeAdapter

from t.trello.models import Board, Card, Label, TrelloList

BASE_URL = "https://api.trello.com"

#: How many HTTP attempts at most (first try + retries) when rate limited.
MAX_ATTEMPTS = 3


class TrelloError(Exception):
    """Base class for Trello client errors (never carries secrets)."""


class TrelloAuthError(TrelloError):
    """The key is invalid or the token is missing/expired (HTTP 401/403)."""


class TrelloRateLimitError(TrelloError):
    """Still rate limited (HTTP 429) after exhausting retries."""


class TrelloClient:
    """Synchronous Trello client holding credentials in one place."""

    def __init__(
        self,
        key: str,
        token: str,
        *,
        backoff_base: float = 1.0,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=BASE_URL,
            params={"key": key, "token": token},
            timeout=timeout,
            transport=transport,
        )
        self._backoff_base = backoff_base

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TrelloClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # -- low level ----------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Send one request, retrying HTTP 429 with exponential backoff."""
        last: httpx.Response | None = None
        for attempt in range(MAX_ATTEMPTS):
            response = self._client.request(method, path, params=params)
            if response.status_code != 429:
                return self._checked(response)
            last = response
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(self._backoff_delay(response, attempt))
        assert last is not None
        raise TrelloRateLimitError(
            f"Rate limited (HTTP 429) after {MAX_ATTEMPTS} attempts; try again later."
        )

    def _backoff_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return max(float(retry_after), 0.0)
            except ValueError:
                pass
        return self._backoff_base * (2.0**attempt)

    @staticmethod
    def _checked(response: httpx.Response) -> httpx.Response:
        if response.status_code in (401, 403):
            raise TrelloAuthError(
                "Trello rejected the credentials (HTTP "
                f"{response.status_code}). Check TRELLO_KEY/TRELLO_TOKEN; "
                "see SETUP.md."
            )
        if response.status_code == 429:  # pragma: no cover - handled in _request
            raise TrelloRateLimitError("Rate limited (HTTP 429).")
        if response.status_code >= 400:
            raise TrelloError(f"Trello request failed (HTTP {response.status_code}).")
        return response

    # -- API ----------------------------------------------------------

    def get_me(self) -> dict[str, str]:
        """Validate credentials; returns basic member info."""
        response = self._request("GET", "/1/members/me", params={"fields": "id,username,fullName"})
        adapter = TypeAdapter(dict[str, str])
        return adapter.validate_python(response.json())

    def list_boards(self) -> list[Board]:
        response = self._request(
            "GET",
            "/1/members/me/boards",
            params={"filter": "open", "fields": "id,name,url,closed"},
        )
        adapter = TypeAdapter(list[Board])
        return adapter.validate_python(response.json())

    def list_lists(self, board_id: str) -> list[TrelloList]:
        response = self._request(
            "GET",
            f"/1/boards/{board_id}/lists",
            params={"cards": "none", "filter": "open", "fields": "id,name,idBoard"},
        )
        adapter = TypeAdapter(list[TrelloList])
        return adapter.validate_python(response.json())

    def list_labels(self, board_id: str) -> list[Label]:
        response = self._request(
            "GET",
            f"/1/boards/{board_id}/labels",
            params={"fields": "id,name,color", "limit": "1000"},
        )
        adapter = TypeAdapter(list[Label])
        return adapter.validate_python(response.json())

    def list_cards(self, list_id: str) -> list[Card]:
        response = self._request(
            "GET",
            f"/1/lists/{list_id}/cards",
            params={"fields": "id,name,url,idList"},
        )
        adapter = TypeAdapter(list[Card])
        return adapter.validate_python(response.json())

    def create_list(self, board_id: str, name: str) -> TrelloList:
        response = self._request(
            "POST",
            "/1/lists",
            params={"name": name, "idBoard": board_id, "pos": "bottom"},
        )
        return TrelloList.model_validate(response.json())

    def create_label(self, board_id: str, name: str, color: str) -> Label:
        response = self._request(
            "POST",
            "/1/labels",
            params={"name": name, "color": color, "idBoard": board_id},
        )
        return Label.model_validate(response.json())

    def create_card(
        self,
        *,
        list_id: str,
        name: str,
        desc: str | None = None,
        due: str | None = None,
        label_ids: list[str] | None = None,
    ) -> Card:
        params: dict[str, str] = {"idList": list_id, "name": name, "pos": "bottom"}
        if desc:
            params["desc"] = desc
        if due:
            params["due"] = due
        if label_ids:
            params["idLabels"] = ",".join(label_ids)
        response = self._request("POST", "/1/cards", params=params)
        return Card.model_validate(response.json())
