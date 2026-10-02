"""Trello data models: Board, TrelloList, Label, Card."""

from __future__ import annotations

from pydantic import BaseModel


class Board(BaseModel):
    id: str
    name: str
    url: str | None = None
    closed: bool = False


class TrelloList(BaseModel):
    id: str
    name: str
    idBoard: str
    closed: bool = False


class Label(BaseModel):
    id: str
    name: str = ""
    color: str | None = None


class Card(BaseModel):
    id: str
    name: str
    url: str | None = None
    idList: str
