"""Tests for translation layer."""

import pytest
import asyncio
from codex_proxy.translate import req_to_chat, resp_to_responses, stream_to_sse
from codex_proxy.types import (
    ResponsesRequest, ResponsesTool, ResponsesReasoning,
    ChatResponse, ChatChoice, ChatChoiceMessage, ChatUsage,
    ChatStreamChunk, ChatStreamChoice, ChatStreamDelta,
)


# ── req_to_chat tests ──


def test_simple_text_input():
    req = ResponsesRequest(model="test", input="hello")
    body = req_to_chat(req)
    assert body["model"] == "test"
    assert len(body["messages"]) == 1
    assert body["messages"][0] == {"role": "user", "content": "hello"}


def test_instructions_becomes_system():
    req = ResponsesRequest(model="test", input="hello", instructions="Be helpful.")
    body = req_to_chat(req)
    assert body["messages"][0] == {"role": "system", "content": "Be helpful."}
    assert body["messages"][1]["role"] == "user"


def test_message_array_input():
    req = ResponsesRequest(model="test", input=[
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "bye"},
    ])
    body = req_to_chat(req)
    assert len(body["messages"]) == 3
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][1]["role"] == "assistant"
    assert body["messages"][2]["role"] == "user"


def test_tools_conversion():
    req = ResponsesRequest(model="test", input="hi", tools=[
        ResponsesTool(name="search", description="Search the web", parameters={"type": "object", "properties": {"q": {"type": "string"}}}),
    ])
    body = req_to_chat(req)
    assert len(body["tools"]) == 1
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["function"]["name"] == "search"


def test_local_shell_converted_to_shell_function():
    req = ResponsesRequest(model="test", input="hi", tools=[
        {"type": "local_shell"},
    ])
    body = req_to_chat(req)
    assert len(body["tools"]) == 1
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["function"]["name"] == "shell"
    assert "command" in body["tools"][0]["function"]["parameters"]["properties"]


def test_web_search_dropped():
    req = ResponsesRequest(model="test", input="hi", tools=[
        {"type": "function", "name": "search"},
        {"type": "web_search"},
        {"type": "web_search_preview"},
    ])
    body = req_to_chat(req)
    assert len(body["tools"]) == 1
    assert body["tools"][0]["function"]["name"] == "search"


def test_web_search_forwarded_when_enabled():
    req = ResponsesRequest(model="test", input="hi", tools=[
        {"type": "function", "name": "search"},
        {
            "type": "web_search_preview",
            "user_location": {"type": "approximate", "country": "US"},
            "max_keyword": 3,
            "force_search": True,
            "limit": 5,
        },
    ])

    body = req_to_chat(req, enable_web_search=True)

    assert len(body["tools"]) == 2
    assert body["tools"][0]["function"]["name"] == "search"
    assert body["tools"][1] == {
        "type": "web_search",
        "user_location": {"type": "approximate", "country": "US"},
        "max_keyword": 3,
        "force_search": True,
        "limit": 5,
    }


def test_duplicate_web_search_tools_deduped_when_enabled():
    req = ResponsesRequest(model="test", input="hi", tools=[
        {"type": "web_search"},
        {"type": "web_search_preview"},
    ])

    body = req_to_chat(req, enable_web_search=True)

    assert body["tools"] == [{"type": "web_search"}]


def test_custom_tool_converted():
    req = ResponsesRequest(model="test", input="hi", tools=[
        {"type": "custom", "name": "my_tool", "description": "A custom tool", "format": {"type": "grammar"}},
    ])
    body = req_to_chat(req)
    assert len(body["tools"]) == 1
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["function"]["name"] == "my_tool"
    assert "grammar" in body["tools"][0]["function"]["description"]


def test_namespace_flattened():
    req = ResponsesRequest(model="test", input="hi", tools=[
        {"type": "namespace", "name": "my_ns", "tools": [
            {"type": "function", "name": "tool_a"},
            {"type": "function", "name": "tool_b"},
        ]},
    ])
    body = req_to_chat(req)
    assert len(body["tools"]) == 2
    assert body["tools"][0]["function"]["name"] == "tool_a"
    assert body["tools"][1]["function"]["name"] == "tool_b"


def test_server_side_tools_dropped():
    req = ResponsesRequest(model="test", input="hi", tools=[
        {"type": "function", "name": "real_tool"},
        {"type": "code_interpreter"},
        {"type": "file_search"},
        {"type": "image_generation"},
        {"type": "computer_use_preview"},
    ])
    body = req_to_chat(req)
    assert len(body["tools"]) == 1
    assert body["tools"][0]["function"]["name"] == "real_tool"


def test_mcp_tool_dropped():
    req = ResponsesRequest(model="test", input="hi", tools=[
        {"type": "mcp", "server_label": "github", "connector_id": "github"},
        {"type": "function", "name": "shell"},
    ])
    body = req_to_chat(req)
    assert len(body["tools"]) == 1
    assert body["tools"][0]["function"]["name"] == "shell"


def test_tool_search_converted():
    req = ResponsesRequest(model="test", input="hi", tools=[
        {"type": "tool_search", "description": "Search tools", "parameters": {"type": "object", "properties": {}}},
    ])
    body = req_to_chat(req)
    assert len(body["tools"]) == 1
    assert body["tools"][0]["function"]["name"] == "tool_search"


def test_function_tool_strict_null_stripped():
    req = ResponsesRequest(model="test", input="hi", tools=[
        {"type": "function", "name": "test", "strict": None},
        {"type": "function", "name": "test2", "strict": True},
    ])
    body = req_to_chat(req)
    assert "strict" not in body["tools"][0]["function"]
    assert body["tools"][1]["function"]["strict"] is True


def test_mixed_real_world_tools():
    """Simulate the real Codex Desktop tool set that caused the 400 error."""
    req = ResponsesRequest(model="test", input="hi", tools=[
        {"type": "function", "name": "apply_patch", "description": "Apply a patch"},
        {"type": "local_shell"},
        {"type": "function", "name": "think"},
        {"type": "code_interpreter"},
        {"type": "web_search"},
        {"type": "namespace", "name": "mcp_ns", "tools": [
            {"type": "function", "name": "mcp_tool_1"},
        ]},
        {"type": "mcp", "server_label": "github", "connector_id": "github"},
        {"type": "tool_search", "description": "Find tools"},
        {"type": "function", "name": "_fetch"},
        {"type": "custom", "name": "structured_output"},
        {"type": "function", "name": "view_image"},
        {"type": "function", "name": "summarize"},
    ])
    body = req_to_chat(req)
    names = [t["function"]["name"] for t in body["tools"]]
    # All function tools + converted tools should be present
    assert "apply_patch" in names
    assert "shell" in names
    assert "think" in names
    assert "mcp_tool_1" in names
    assert "tool_search" in names
    assert "_fetch" in names
    assert "structured_output" in names
    assert "view_image" in names
    assert "summarize" in names
    # Dropped tools should NOT be present
    assert len(body["tools"]) == 9
    # Every tool must have a non-empty function.name
    for t in body["tools"]:
        assert t["function"]["name"], f"tool missing function.name: {t}"


def test_max_output_tokens_mapping():
    req = ResponsesRequest(model="test", input="hi", max_output_tokens=1000)
    body = req_to_chat(req)
    assert body["max_completion_tokens"] == 1000
    assert "max_output_tokens" not in body


def test_reasoning_effort_mapping():
    req = ResponsesRequest(model="test", input="hi", reasoning=ResponsesReasoning(effort="high"))
    body = req_to_chat(req)
    assert body["reasoning_effort"] == "high"


def test_reasoning_effort_minimal_maps_to_low():
    req = ResponsesRequest(model="test", input="hi", reasoning=ResponsesReasoning(effort="minimal"))
    body = req_to_chat(req)
    assert body["reasoning_effort"] == "low"


def test_function_call_output_to_tool_message():
    req = ResponsesRequest(model="test", input=[
        {"role": "user", "content": "search for cats"},
        {"type": "function_call", "id": "fc_1", "call_id": "call_123", "name": "search", "arguments": '{"q":"cats"}'},
        {"type": "function_call_output", "call_id": "call_123", "output": "Found 10 cats"},
    ])
    body = req_to_chat(req)
    # Should have: user msg, assistant msg with tool_calls, tool msg
    roles = [m["role"] for m in body["messages"]]
    assert "user" in roles
    assert "assistant" in roles
    assert "tool" in roles
    tool_msg = next(m for m in body["messages"] if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "call_123"
    assert tool_msg["content"] == "Found 10 cats"


def test_reasoning_reinjection():
    req = ResponsesRequest(model="test", input=[
        {"role": "user", "content": "think about this"},
        {"type": "reasoning", "id": "rs_1", "encrypted_content": "I am thinking...", "summary": []},
        {"role": "assistant", "content": "Here is my answer"},
    ])
    body = req_to_chat(req)
    asst = next(m for m in body["messages"] if m["role"] == "assistant")
    assert asst.get("reasoning_content") == "I am thinking..."
    assert asst["content"] == "Here is my answer"


# ── resp_to_responses tests ──


def test_resp_to_responses_basic():
    chat = ChatResponse(
        id="chatcmpl-1", created=1000, model="test",
        choices=[ChatChoice(message=ChatChoiceMessage(content="Hello!"))],
    )
    req = ResponsesRequest(model="test", input="hi")
    resp = resp_to_responses(chat, req)
    assert resp.status == "completed"
    assert len(resp.output) == 1
    assert resp.output[0].type == "message"
    assert resp.output[0].content[0]["text"] == "Hello!"


def test_resp_to_responses_with_reasoning():
    chat = ChatResponse(
        id="chatcmpl-2", created=1000, model="test",
        choices=[ChatChoice(message=ChatChoiceMessage(
            reasoning_content="Let me think...",
            content="The answer is 42.",
        ))],
    )
    req = ResponsesRequest(model="test", input="hi")
    resp = resp_to_responses(chat, req, expose_reasoning=True)
    assert len(resp.output) == 2
    assert resp.output[0].type == "reasoning"
    assert resp.output[0].encrypted_content == "Let me think..."
    assert resp.output[0].summary[0]["text"] == "Let me think..."
    assert resp.output[1].type == "message"


def test_resp_to_responses_reasoning_hidden():
    chat = ChatResponse(
        id="chatcmpl-3", created=1000, model="test",
        choices=[ChatChoice(message=ChatChoiceMessage(
            reasoning_content="Thinking...", content="Answer",
        ))],
    )
    req = ResponsesRequest(model="test", input="hi")
    resp = resp_to_responses(chat, req, expose_reasoning=False)
    assert resp.output[0].type == "reasoning"
    assert resp.output[0].summary == []  # empty when hidden
    assert resp.output[0].encrypted_content == "Thinking..."  # still present for round-trip


def test_resp_to_responses_with_tool_calls():
    chat = ChatResponse(
        id="chatcmpl-4", created=1000, model="test",
        choices=[ChatChoice(message=ChatChoiceMessage(
            tool_calls=[{
                "id": "call_abc",
                "type": "function",
                "function": {"name": "search", "arguments": '{"q":"cats"}'},
            }],
        ))],
    )
    req = ResponsesRequest(model="test", input="hi")
    resp = resp_to_responses(chat, req)
    assert len(resp.output) == 1
    assert resp.output[0].type == "function_call"
    assert resp.output[0].call_id == "call_abc"
    assert resp.output[0].name == "search"


def test_resp_to_responses_usage():
    chat = ChatResponse(
        id="chatcmpl-5", created=1000, model="test",
        choices=[ChatChoice(message=ChatChoiceMessage(content="hi"))],
        usage=ChatUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150,
                        completion_tokens_details={"reasoning_tokens": 20}),
    )
    req = ResponsesRequest(model="test", input="hi")
    resp = resp_to_responses(chat, req)
    assert resp.usage.input_tokens == 100
    assert resp.usage.output_tokens == 50
    assert resp.usage.output_tokens_details["reasoning_tokens"] == 20


# ── stream_to_sse tests ──


async def _collect_sse(chunks):
    """Helper to collect SSE events from stream_to_sse."""
    async def async_iter(items):
        for item in items:
            yield item
    events = []
    async for event in stream_to_sse(async_iter(chunks), ResponsesRequest(model="test", input="hi")):
        events.append(event)
    return events

@pytest.mark.asyncio
async def _run_stream_test(chunks):
    return await _collect_sse(chunks)


def test_stream_emits_response_created():
    chunks = [
        ChatStreamChunk(id="s1", created=1000, model="test", choices=[
            ChatStreamChoice(delta=ChatStreamDelta(content="Hi")),
        ]),
    ]
    events = asyncio.run(_collect_sse(chunks))
    assert any("response.created" in e for e in events)
    assert any("response.in_progress" in e for e in events)


def test_stream_emits_message_events():
    chunks = [
        ChatStreamChunk(id="s1", created=1000, model="test", choices=[
            ChatStreamChoice(delta=ChatStreamDelta(content="Hello")),
        ]),
        ChatStreamChunk(id="s1", created=1000, model="test", choices=[
            ChatStreamChoice(delta=ChatStreamDelta(content=" world")),
        ]),
        ChatStreamChunk(id="s1", created=1000, model="test", choices=[
            ChatStreamChoice(finish_reason="stop"),
        ]),
    ]
    events = asyncio.run(_collect_sse(chunks))
    assert any("output_text.delta" in e for e in events)
    assert any("response.completed" in e for e in events)


def test_stream_emits_reasoning_events():
    chunks = [
        ChatStreamChunk(id="s1", created=1000, model="test", choices=[
            ChatStreamChoice(delta=ChatStreamDelta(reasoning_content="Thinking...")),
        ]),
        ChatStreamChunk(id="s1", created=1000, model="test", choices=[
            ChatStreamChoice(delta=ChatStreamDelta(content="Answer")),
        ]),
        ChatStreamChunk(id="s1", created=1000, model="test", choices=[
            ChatStreamChoice(finish_reason="stop"),
        ]),
    ]
    events = asyncio.run(_collect_sse(chunks))
    assert any("reasoning_summary_text.delta" in e for e in events)
    assert any("reasoning_summary_part.done" in e for e in events)


def test_stream_emits_tool_call_events():
    chunks = [
        ChatStreamChunk(id="s1", created=1000, model="test", choices=[
            ChatStreamChoice(delta=ChatStreamDelta(tool_calls=[{"index": 0, "id": "call_1", "function": {"name": "search"}}])),
        ]),
        ChatStreamChunk(id="s1", created=1000, model="test", choices=[
            ChatStreamChoice(delta=ChatStreamDelta(tool_calls=[{"index": 0, "function": {"arguments": '{"q":'}}])),
        ]),
        ChatStreamChunk(id="s1", created=1000, model="test", choices=[
            ChatStreamChoice(delta=ChatStreamDelta(tool_calls=[{"index": 0, "function": {"arguments": '"cats"}'}}])),
        ]),
        ChatStreamChunk(id="s1", created=1000, model="test", choices=[
            ChatStreamChoice(finish_reason="stop"),
        ]),
    ]
    events = asyncio.run(_collect_sse(chunks))
    assert any("function_call_arguments.delta" in e for e in events)
    assert any("function_call_arguments.done" in e for e in events)
