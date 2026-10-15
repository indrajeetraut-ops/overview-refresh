from trace_utils.profiler import flatten, profile


def test_flatten_nested_dict_list_and_json_string():
    obj = {
        "type": "assistant",
        "message": {"model": "opus", "usage": {"output_tokens": 42}},
        "content": [{"type": "text"}, {"type": "tool_use"}],
        "blob": '{"nested": 1}',  # JSON-encoded string is parsed and descended
    }
    paths = dict(flatten(obj))
    assert paths["type"] == "assistant"
    assert paths["message.model"] == "opus"
    assert paths["message.usage.output_tokens"] == 42
    assert paths["content[].type"] in ("text", "tool_use")
    assert paths["blob.nested"] == 1


def test_profile_discovers_nested_paths_and_coverage(engine, jsonl_source):
    prof = profile(engine, jsonl_source, sample=100)
    by_path = {f.path: f for f in prof.fields}
    assert prof.total_rows == 4
    # message.model present on the 2 assistant events -> 50% coverage
    assert "message.model" in by_path
    assert abs(by_path["message.model"].coverage(prof.total_rows) - 0.5) < 1e-9
    assert "int" in by_path["message.usage.output_tokens"].types
    # type present on every event
    assert by_path["type"].coverage(prof.total_rows) == 1.0
    assert "claude-opus-4-6" in by_path["message.model"].sample_repr()
