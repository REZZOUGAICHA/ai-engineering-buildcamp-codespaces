"""Tests for the homework5 changes to fetch_trace / fetch_traces (no network)."""
from trace_replay.converter import fetch_trace, fetch_traces


def msg(role, text):
    return {'role': role, 'parts': [{'type': 'text', 'content': text}]}


class FakeClient:
    """Returns hand-written rows, and records the kwargs it was called with."""

    def __init__(self, rows):
        self.rows = rows
        self.kwargs = None

    def query_json_rows(self, sql, **kwargs):
        self.kwargs = kwargs
        return {'rows': self.rows}


U1, A1, U2, A2 = msg('user', 'q1'), msg('assistant', 'a1'), msg('user', 'q2'), msg('assistant', 'a2')


def test_cumulative_snapshots_are_not_duplicated_and_tokens_are_summed():
    client = FakeClient([
        {'trace_id': 't', 'all_messages': [U1, A1], 'input_tokens': '100', 'output_tokens': '10'},
        {'trace_id': 't', 'all_messages': [U1, A1, U2, A2], 'input_tokens': '250', 'output_tokens': '20'},
    ])
    trace = fetch_trace('t', client)
    assert trace.all_messages == [U1, A1, U2, A2]
    assert (trace.input_tokens, trace.output_tokens) == (350, 30)


def test_partial_spans_are_appended():
    client = FakeClient([
        {'trace_id': 't', 'all_messages': [U1, A1], 'input_tokens': '100', 'output_tokens': '10'},
        {'trace_id': 't', 'all_messages': [U2, A2], 'input_tokens': '90', 'output_tokens': '12'},
    ])
    trace = fetch_trace('t', client)
    assert trace.all_messages == [U1, A1, U2, A2]
    assert (trace.input_tokens, trace.output_tokens) == (190, 22)


def test_min_timestamp_is_passed():
    client = FakeClient([{'trace_id': 't', 'all_messages': [U1], 'input_tokens': '1', 'output_tokens': '1'}])
    fetch_trace('t', client)
    assert 'min_timestamp' in client.kwargs


def test_fetch_traces_groups_rows_per_trace():
    client = FakeClient([
        {'trace_id': 'a', 'all_messages': [U1, A1], 'input_tokens': '5', 'output_tokens': '1'},
        {'trace_id': 'b', 'all_messages': [U2], 'input_tokens': '7', 'output_tokens': '2'},
        {'trace_id': 'a', 'all_messages': [U1, A1, U2, A2], 'input_tokens': '6', 'output_tokens': '3'},
    ])
    traces = fetch_traces(['a', 'b'], client)
    assert traces['a'].all_messages == [U1, A1, U2, A2]
    assert (traces['a'].input_tokens, traces['b'].input_tokens) == (11, 7)
