import json
import io
from email.message import Message
import urllib.request
import urllib.error
from urllib.response import addinfourl
from pathlib import Path
from unittest.mock import Mock

import pytest

from specimen.contracts import RunConfig
from specimen import model
from specimen.model import AdviserError, advise, configuration


def _config(tmp_path: Path, content: str, **kwargs) -> RunConfig:
    source = tmp_path / "private-source.csv"
    source.write_text(content, encoding="utf-8")
    return RunConfig(input_path=source, script_path=tmp_path / "script.py",
                     output_dir=tmp_path / "out", runner="local", **kwargs)


def _completion(content: str, usage: dict | None = None) -> bytes:
    return json.dumps({
        "choices": [{"message": {"content": content}}],
        "usage": usage or {"prompt_tokens": 17, "completion_tokens": 9},
    }).encode("utf-8")


def _configure(monkeypatch, key: str | None = "test-secret-key") -> None:
    monkeypatch.setenv("SPECIMEN_MODEL_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("SPECIMEN_MODEL_NAME", "mock-model")
    if key is None:
        monkeypatch.delenv("SPECIMEN_MODEL_API_KEY", raising=False)
    else:
        monkeypatch.setenv("SPECIMEN_MODEL_API_KEY", key)


def test_payload_contains_only_column_positions_and_coarse_aggregates(tmp_path, monkeypatch):
    config = _config(
        tmp_path,
        "Private Header,Email Field,Number Field,UUID Field,Public Summary\n"
        "secret-name-73,alpha@example.test,123,550e8400-e29b-41d4-a716-446655440000,plain\n"
        "secret-name-74,beta@example.test,456,550e8400-e29b-41d4-a716-446655440001,éplain\n",
        sensitive_columns=["Private Header"],
        timeout=90,
    )
    config.script_path.write_text(
        "value = item.strip().encode('ascii')\n"
        "if private_name == 'PRIVATE_SCRIPT_SENTINEL':\n    pass\n",
        encoding="utf-8",
    )
    _configure(monkeypatch, key="never-echo-this-token")
    seen = {}

    def send(url, body, headers, timeout):
        seen.update(url=url, body=body, headers=headers, timeout=timeout)
        return _completion('{"sensitive_column_indices":[1],"strategies":["unicode","generic"]}')

    monkeypatch.setattr("specimen.model._send_request", send)
    result = advise(config)

    payload = json.loads(seen["body"])
    content = " ".join(message["content"] for message in payload["messages"])
    assert seen["url"] == "https://model.example/v1/chat/completions"
    assert seen["timeout"] == 10
    assert "never-echo-this-token" not in seen["body"].decode()
    assert "Private Header" not in content
    assert "Public Summary" not in content
    assert "secret-name-73" not in content
    assert "secret-name-74" not in content
    assert "PRIVATE_SCRIPT_SENTINEL" not in content
    assert str(config.input_path) not in content
    supplied = json.loads(payload["messages"][1]["content"])
    assert supplied["column_count"] == 5
    assert supplied["already_selected_sensitive_positions"] == [0]
    assert supplied["columns"][0] == {
        "position": 0, "contains_non_ascii": False, "contains_email_like": False,
        "contains_numeric": False, "contains_uuid_like": False,
        "maximum_length_bucket": "9-32", "uniqueness_ratio_bucket": "76-100%",
    }
    assert supplied["columns"][1]["contains_email_like"] is True
    assert supplied["columns"][2]["contains_numeric"] is True
    assert supplied["columns"][3]["contains_uuid_like"] is True
    assert supplied["columns"][4]["contains_non_ascii"] is True
    assert supplied["script_operation_hints"] == ["compare_eq", "encode_ascii", "strip"]
    assert result["suggested_sensitive_indices"] == [0, 1]
    assert result["strategies"] == ["generic", "unicode"]
    assert result["adviser"] == {
        "model": "mock-model", "attempts": 1, "input_tokens": 17, "output_tokens": 9,
    }
    assert "never-echo-this-token" not in repr(result)


def test_no_provider_call_without_configuration(tmp_path, monkeypatch):
    config = _config(tmp_path, "x\none\n")
    for name in ("SPECIMEN_MODEL_BASE_URL", "SPECIMEN_MODEL_NAME", "SPECIMEN_MODEL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    send = Mock()
    monkeypatch.setattr("specimen.model._send_request", send)

    assert configuration() == {"configured": False, "model": None}
    with pytest.raises(AdviserError, match="not configured"):
        advise(config)
    send.assert_not_called()


@pytest.mark.parametrize("content", [
    "not json",
    '{"sensitive_column_indices":[3],"strategies":[]}',
    '{"sensitive_column_indices":[true],"strategies":[]}',
    '{"sensitive_column_indices":[0],"strategies":["arbitrary code"]}',
    '{"sensitive_column_indices":[0],"strategies":[],"extra":"ignored?"}',
])
def test_rejects_invalid_or_out_of_range_advice(tmp_path, monkeypatch, content):
    config = _config(tmp_path, "opaque,other\na,b\n", sensitive_columns=["opaque"])
    _configure(monkeypatch)
    monkeypatch.setattr("specimen.model._send_request", lambda *args: _completion(content))

    with pytest.raises(AdviserError, match="invalid response"):
        advise(config)


def test_retryable_http_failures_are_bounded_and_auth_errors_are_not_retried(tmp_path, monkeypatch):
    config = _config(tmp_path, "a\nx\n")
    _configure(monkeypatch)
    monkeypatch.setattr("specimen.model.time.sleep", lambda _: None)
    send = Mock(side_effect=[
        urllib.error.HTTPError("https://model.example", 503, "private server text", {}, None),
        urllib.error.URLError("private network text"),
        _completion('{"sensitive_column_indices":[0],"strategies":["generic"]}'),
    ])
    monkeypatch.setattr("specimen.model._send_request", send)
    assert advise(config)["adviser"]["attempts"] == 3
    assert send.call_count == 3

    send.reset_mock(side_effect=True)
    send.side_effect = urllib.error.HTTPError("https://model.example", 401, "secret response", {}, None)
    with pytest.raises(AdviserError, match="request failed") as caught:
        advise(config)
    assert send.call_count == 1
    assert "secret response" not in str(caught.value)


def test_oversized_response_is_rejected_without_network(tmp_path, monkeypatch):
    config = _config(tmp_path, "a\nx\n")
    _configure(monkeypatch, key=None)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, limit):
            assert limit == 16_385
            return b"x" * limit

    opener = Mock()
    opener.open.return_value = FakeResponse()
    monkeypatch.setattr("specimen.model.urllib.request.build_opener", lambda *args: opener)
    with pytest.raises(AdviserError, match="size limit"):
        advise(config)
    opener.open.assert_called_once()


def test_rejects_remote_plain_http_and_redirects_cannot_forward_credentials(tmp_path, monkeypatch):
    config = _config(tmp_path, "a\nx\n")
    _configure(monkeypatch)
    monkeypatch.setenv("SPECIMEN_MODEL_BASE_URL", "http://not-local.example/v1")
    send = Mock()
    original_send = model._send_request
    monkeypatch.setattr("specimen.model._send_request", send)
    with pytest.raises(AdviserError, match="HTTPS or a loopback"):
        advise(config)
    send.assert_not_called()

    monkeypatch.setenv("SPECIMEN_MODEL_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setattr("specimen.model._send_request", original_send)
    captured = {"requests": 0}
    real_build_opener = urllib.request.build_opener

    def build(*handlers):
        captured["handler_types"] = [type(handler).__name__ for handler in handlers]
        return real_build_opener(*handlers, FakeRedirectTransport())

    class FakeRedirectTransport(urllib.request.BaseHandler):
        handler_order = 0

        def http_open(self, request):
            captured["requests"] += 1
            captured["authorization"] = request.get_header("Authorization")
            captured["url"] = request.full_url
            headers = Message()
            headers["Location"] = "https://elsewhere.example/steal"
            response = addinfourl(io.BytesIO(b"redirect body"), headers, request.full_url, 302)
            response.msg = "Found"
            return response

    monkeypatch.setattr("specimen.model.urllib.request.build_opener", build)
    with pytest.raises(AdviserError, match="request failed"):
        advise(config)
    assert "_NoRedirect" in captured["handler_types"]
    assert captured["authorization"] == "Bearer test-secret-key"
    assert captured["requests"] == 1
    assert captured["url"] == "http://127.0.0.1:8080/v1/chat/completions"
