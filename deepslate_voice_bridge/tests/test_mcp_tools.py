from types import SimpleNamespace

from app.mcp_tools import mcp_tools_to_function_dicts, result_to_text


def test_tool_translation():
    tools = [
        SimpleNamespace(
            name="HassTurnOn",
            description="Turn on a device",
            inputSchema={"type": "object", "properties": {"name": {"type": "string"}}},
        ),
        SimpleNamespace(name="GetLiveContext", description=None, inputSchema=None),
    ]
    defs = mcp_tools_to_function_dicts(tools)
    assert defs[0] == {
        "type": "function",
        "function": {
            "name": "HassTurnOn",
            "description": "Turn on a device",
            "parameters": {"type": "object", "properties": {"name": {"type": "string"}}},
        },
    }
    assert defs[1]["function"]["parameters"] == {"type": "object", "properties": {}}
    assert defs[1]["function"]["description"] == ""


def test_result_flattening():
    ok = SimpleNamespace(content=[SimpleNamespace(text="done")], isError=False)
    assert result_to_text(ok) == "done"
    err = SimpleNamespace(content=[SimpleNamespace(text="no such entity")], isError=True)
    assert result_to_text(err) == "Error: no such entity"
    empty = SimpleNamespace(content=[], isError=False)
    assert result_to_text(empty) == "(empty result)"
