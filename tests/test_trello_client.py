"""M1 tests: TrelloClient parsing, 401 handling and 429 retries."""

from __future__ import annotations

import httpx
import pytest
import respx

from t.trello.client import (
    TrelloAuthError,
    TrelloClient,
    TrelloRateLimitError,
)

KEY = "key123"
TOKEN = "token456"


def make_client(**kwargs: object) -> TrelloClient:
    return TrelloClient(KEY, TOKEN, backoff_base=0.0, **kwargs)  # type: ignore[arg-type]


@respx.mock
def test_list_boards_parses() -> None:
    respx.get("https://api.trello.com/1/members/me/boards").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "b1", "name": "Work", "url": "https://trello.com/b/x"},
                {"id": "b2", "name": "Home"},
            ],
        )
    )
    with make_client() as client:
        boards = client.list_boards()
    assert [(b.id, b.name) for b in boards] == [("b1", "Work"), ("b2", "Home")]
    # Auth travels as query params on every request.
    request = respx.calls[0].request
    assert "key=key123" in str(request.url)
    assert "token=token456" in str(request.url)


@respx.mock
def test_list_lists_and_labels() -> None:
    respx.get("https://api.trello.com/1/boards/b1/lists").mock(
        return_value=httpx.Response(200, json=[{"id": "l1", "name": "Todo", "idBoard": "b1"}])
    )
    respx.get("https://api.trello.com/1/boards/b1/labels").mock(
        return_value=httpx.Response(200, json=[{"id": "lb1", "name": "urgent", "color": "red"}])
    )
    with make_client() as client:
        lists = client.list_lists("b1")
        labels = client.list_labels("b1")
    assert lists[0].name == "Todo"
    assert labels[0].color == "red"


@respx.mock
def test_401_raises_auth_error() -> None:
    respx.get("https://api.trello.com/1/members/me").mock(
        return_value=httpx.Response(401, json="invalid key")
    )
    with make_client() as client:
        with pytest.raises(TrelloAuthError):
            client.get_me()


@respx.mock
def test_429_retries_then_succeeds() -> None:
    route = respx.get("https://api.trello.com/1/members/me/boards")
    route.side_effect = [
        httpx.Response(429, json="rate limit"),
        httpx.Response(200, json=[{"id": "b1", "name": "Work"}]),
    ]
    with make_client() as client:
        boards = client.list_boards()
    assert [b.id for b in boards] == ["b1"]
    assert route.call_count == 2


@respx.mock
def test_429_exhausted_raises() -> None:
    route = respx.get("https://api.trello.com/1/members/me/boards")
    route.side_effect = [httpx.Response(429, json="slow down")] * 3
    with make_client() as client:
        with pytest.raises(TrelloRateLimitError):
            client.list_boards()
    assert route.call_count == 3


@respx.mock
def test_create_card() -> None:
    route = respx.post("https://api.trello.com/1/cards").mock(
        return_value=httpx.Response(
            200,
            json={"id": "c1", "name": "Task", "idList": "l1", "url": "https://t/c1"},
        )
    )
    with make_client() as client:
        card = client.create_card(list_id="l1", name="Task")
    assert card.id == "c1"
    assert route.calls[0].request.url.params["idList"] == "l1"
