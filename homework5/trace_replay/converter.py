import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TypeVar, overload

from pydantic import BaseModel
from pydantic_ai._agent_graph import GraphAgentState
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.run import AgentRunResult
from pydantic_ai.usage import RunUsage

T = TypeVar('T', bound=BaseModel)


@dataclass
class TraceData:
    """Raw data fetched from Logfire for a single agent run trace."""
    all_messages: list[dict]
    input_tokens: int = 0
    output_tokens: int = 0


# --- Changes from the lesson's main branch (homework5) -----------------------
# 1. Read ALL agent spans of a trace (ASC, no LIMIT 1), not only the latest.
#    A grouped session trace has one invoke_agent span per turn.
# 2. Token attributes: newer pydantic-ai writes gen_ai.aggregated_usage.*,
#    older versions gen_ai.usage.*. Read both. (With only the old name, our
#    version returned None, which `int(None or 0)` silently turned into 0.)
# 3. min_timestamp is required by query_json_rows in our logfire version.
# -----------------------------------------------------------------------------

LOOKBACK = timedelta(days=30)

_AGENT_SPANS_SQL = """
    SELECT
        trace_id,
        attributes->'pydantic_ai.all_messages' as all_messages,
        COALESCE(attributes->>'gen_ai.aggregated_usage.input_tokens',
                 attributes->>'gen_ai.usage.input_tokens') as input_tokens,
        COALESCE(attributes->>'gen_ai.aggregated_usage.output_tokens',
                 attributes->>'gen_ai.usage.output_tokens') as output_tokens
    FROM records
    WHERE trace_id IN ({ids})
      AND (span_name = 'agent run' OR span_name LIKE 'invoke_agent %')
    ORDER BY start_timestamp ASC
"""


def _merge_messages(merged: list[dict], new: list[dict]) -> list[dict]:
    """Add one span's messages to the session's message list.

    A span can hold a cumulative snapshot (it starts with everything we have
    so far: replace with the longer list) or only its own part (append it).
    """
    if new[:len(merged)] == merged:
        return list(new)
    return merged + new


def _trace_data_from_rows(rows: list[dict]) -> TraceData:
    """Merge the messages and SUM the tokens of all agent spans of one trace."""
    data = TraceData(all_messages=[])
    for row in rows:
        all_messages = row['all_messages'] or []
        if isinstance(all_messages, str):
            all_messages = json.loads(all_messages)
        data.all_messages = _merge_messages(data.all_messages, all_messages)
        data.input_tokens += int(row.get('input_tokens') or 0)
        data.output_tokens += int(row.get('output_tokens') or 0)
    return data


def _query_agent_spans(trace_ids: list[str], client: object) -> list[dict]:
    ids_csv = ', '.join(f"'{tid}'" for tid in trace_ids)
    return client.query_json_rows(
        sql=_AGENT_SPANS_SQL.format(ids=ids_csv),
        min_timestamp=datetime.now(timezone.utc) - LOOKBACK,
    )['rows']


def fetch_trace(trace_id: str, client: object) -> TraceData:
    """Fetch every agent span of a trace from Logfire and aggregate them.

    Args:
        trace_id: The trace ID to query.
        client: LogfireQueryClient instance.
    """
    rows = _query_agent_spans([trace_id], client)
    if not rows:
        raise ValueError(f'No agent run span found for trace_id={trace_id}')
    return _trace_data_from_rows(rows)


def fetch_traces(trace_ids: list[str], client: object) -> dict[str, TraceData]:
    """Fetch several traces in one query; each trace aggregates all its agent spans.

    Args:
        trace_ids: List of trace IDs to query.
        client: LogfireQueryClient instance.

    Returns:
        Dict mapping trace_id to TraceData. Missing traces are omitted.
    """
    if not trace_ids:
        return {}

    by_trace: dict[str, list[dict]] = {}
    for row in _query_agent_spans(trace_ids, client):
        by_trace.setdefault(row['trace_id'], []).append(row)

    return {tid: _trace_data_from_rows(rows) for tid, rows in by_trace.items()}


def _extract_output(messages: list[ModelMessage], output_type: type[T] | None = None) -> str | dict | T:
    """Extract the final output from a message list.

    For text output: returns the concatenated text from the last ModelResponse.
    For structured output: the last ModelResponse is a tool call (e.g. final_result),
    so we return its args dict — or validate it against output_type if provided.
    """
    for msg in reversed(messages):
        if isinstance(msg, ModelResponse):
            text_parts = [p.content for p in msg.parts if isinstance(p, TextPart)]
            if text_parts:
                return ''.join(text_parts)
            tool_calls = [p for p in msg.parts if isinstance(p, ToolCallPart)]
            if tool_calls:
                args = tool_calls[-1].args
                if isinstance(args, str):
                    args = json.loads(args)
                if output_type is not None:
                    return output_type.model_validate(args)
                return args or {}
            break
    return ''


def otel_to_model_messages(otel_messages: list[dict]) -> list[ModelMessage]:
    """Convert OTel-format messages (from Logfire) back to PydanticAI ModelMessage objects.

    OTel format uses role/parts/type, PydanticAI uses kind/parts/part_kind.
    Consecutive system/user messages are merged into a single ModelRequest.
    """
    messages: list[ModelMessage] = []
    pending_request_parts = []

    for msg in otel_messages:
        role = msg['role']
        parts = msg.get('parts', [])

        if role == 'assistant':
            if pending_request_parts:
                messages.append(ModelRequest(parts=pending_request_parts))
                pending_request_parts = []

            response_parts = []
            for part in parts:
                ptype = part['type']
                if ptype == 'text':
                    response_parts.append(TextPart(content=part.get('content', '')))
                elif ptype == 'tool_call':
                    args = part.get('arguments', {})
                    if isinstance(args, str):
                        args = json.loads(args)
                    response_parts.append(ToolCallPart(
                        tool_name=part['name'],
                        args=args,
                        tool_call_id=part.get('id', ''),
                    ))
                elif ptype == 'thinking':
                    response_parts.append(ThinkingPart(content=part.get('content', '')))

            messages.append(ModelResponse(
                parts=response_parts,
                finish_reason=msg.get('finish_reason'),
            ))

        elif role == 'system':
            for part in parts:
                if part['type'] == 'text':
                    pending_request_parts.append(
                        SystemPromptPart(content=part.get('content', ''))
                    )

        elif role == 'user':
            for part in parts:
                ptype = part['type']
                if ptype == 'text':
                    pending_request_parts.append(
                        UserPromptPart(content=part.get('content', ''))
                    )
                elif ptype == 'tool_call_response':
                    pending_request_parts.append(ToolReturnPart(
                        tool_name=part['name'],
                        content=part.get('result', ''),
                        tool_call_id=part.get('id', ''),
                    ))

    if pending_request_parts:
        messages.append(ModelRequest(parts=pending_request_parts))

    return messages


@overload
def trace_to_run_result(trace: TraceData) -> AgentRunResult[str | dict]: ...
@overload
def trace_to_run_result(trace: TraceData, output_type: type[T]) -> AgentRunResult[T]: ...

def trace_to_run_result(trace: TraceData, output_type: type[T] | None = None) -> AgentRunResult:
    """Convert fetched TraceData into an AgentRunResult.

    Args:
        trace: TraceData from fetch_trace().
        output_type: Optional Pydantic BaseModel class to validate the output against.
    """
    messages = otel_to_model_messages(trace.all_messages)
    output = _extract_output(messages, output_type)

    state = GraphAgentState(
        message_history=messages,
        usage=RunUsage(
            input_tokens=trace.input_tokens,
            output_tokens=trace.output_tokens,
        ),
    )

    return AgentRunResult(output=output, _state=state)


