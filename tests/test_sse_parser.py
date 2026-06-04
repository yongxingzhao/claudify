"""Tests for the SSEParser class."""

from __future__ import annotations

from claudify.sse import SSEParser


def test_sse_parser_single_event():
    parser = SSEParser()
    events = parser.feed("data: {\"id\":\"1\"}\n\n")
    assert len(events) == 1
    assert events[0]["id"] == "1"


def test_sse_parser_multiple_events():
    parser = SSEParser()
    events = parser.feed(
        "data: {\"id\":\"1\"}\n\n"
        "data: {\"id\":\"2\"}\n\n"
    )
    assert len(events) == 2


def test_sse_parser_split_chunks():
    """Events split across multiple feed calls should be correctly parsed."""
    parser = SSEParser()
    events1 = parser.feed("data: {\"id\":")
    assert len(events1) == 0
    events2 = parser.feed("\"1\"}\n\n")
    assert len(events2) == 1
    assert events2[0]["id"] == "1"


def test_sse_parser_done():
    parser = SSEParser()
    events = parser.feed("data: [DONE]\n\n")
    assert parser.done is True
    assert len(events) == 0


def test_sse_parser_malformed_json_skipped():
    parser = SSEParser()
    events = parser.feed("data: not-json\n\ndata: {\"ok\":true}\n\n")
    assert len(events) == 1
    assert events[0]["ok"] is True


def test_sse_parser_event_line_ignored():
    """event: lines are ignored — only data: lines are parsed."""
    parser = SSEParser()
    events = parser.feed("event: chat\ndata: {\"role\":\"assistant\"}\n\n")
    assert len(events) == 1
    assert events[0]["role"] == "assistant"


def test_sse_parser_incremental():
    """Multiple feed calls accumulate events correctly."""
    parser = SSEParser()
    parser.feed("data: {\"a\":1}\n\n")
    parser.feed("data: {\"b\":2}\n\n")
    # Done flag should be false
    assert not parser.done
