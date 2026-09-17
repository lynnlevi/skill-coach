from copy import deepcopy
import json

import httpx
from openai import OpenAI
import pytest

from coaching.ai import CoachAI, AIError
from coaching.domain import DEFAULT_CONFIG, Evaluation, score_evaluation, MAX_AUDIO_BYTES
from coaching.settings import Settings


def sdk_client(handler):
    return OpenAI(api_key="test-key-never-sent", max_retries=0,
                  http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def response_envelope(content, status="completed"):
    return {"id": "resp_test", "object": "response", "created_at": 1, "status": status,
            "model": "gpt-4.1-mini", "output": [{"id": "msg_test", "type": "message",
            "status": "completed", "role": "assistant", "content": content}]}


def test_real_sdk_parses_structured_evaluation_and_uses_final_answer(evaluation):
    calls = []
    structured = {k: v for k, v in evaluation.items() if k != "overall_score"}

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=response_envelope([
            {"type": "output_text", "text": json.dumps(structured), "annotations": []}
        ]))

    ai = CoachAI(Settings(database_url="unused"), client=sdk_client(handler))
    result = ai.evaluate({"config_snapshot": DEFAULT_CONFIG, "question_text": "A question?",
                          "answer": "Final edited answer", "raw_transcript": "incorrect transcript", "model": "gpt-4.1-mini"})
    assert result["overall_score"] == 8
    payload = json.loads(calls[0]["input"][1]["content"])
    assert payload["answer"] == "Final edited answer"
    assert "incorrect transcript" not in json.dumps(calls[0])
    assert calls[0]["store"] is False
    assert calls[0]["text"]["format"]["type"] == "json_schema"
    assert calls[0]["text"]["format"]["strict"] is True


def test_transcription_sends_wav_and_does_not_evaluate():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/audio/transcriptions"
        assert b'filename="answer.wav"' in request.content
        assert b"gpt-4o-mini-transcribe" in request.content
        return httpx.Response(200, json={"text": " Recognized words "})

    ai = CoachAI(Settings(database_url="unused"), client=sdk_client(handler))
    assert ai.transcribe(b"RIFF-test-audio") == "Recognized words"
    assert len(calls) == 1
    with pytest.raises(AIError):
        ai.transcribe(b"x" * (MAX_AUDIO_BYTES + 1))
    assert len(calls) == 1


@pytest.mark.parametrize("status,content", [
    ("completed", [{"type": "refusal", "refusal": "Unable to comply"}]),
    ("incomplete", []),
    ("completed", [{"type": "output_text", "text": "not json", "annotations": []}]),
])
def test_refused_incomplete_or_malformed_output_is_retryable(status, content):
    ai = CoachAI(Settings(database_url="unused"), client=sdk_client(
        lambda _: httpx.Response(200, json=response_envelope(content, status))))
    with pytest.raises(AIError):
        ai.evaluate({"config_snapshot": DEFAULT_CONFIG, "question_text": "Question?", "answer": "Answer", "model": "model"})


def test_errors_do_not_expose_provider_payloads():
    ai = CoachAI(Settings(database_url="unused"), client=sdk_client(
        lambda _: httpx.Response(401, json={"error": {"message": "SECRET-PAYLOAD", "type": "invalid_request_error"}})))
    with pytest.raises(AIError) as error:
        ai.transcribe(b"RIFF")
    assert "SECRET-PAYLOAD" not in str(error.value)


def test_weighted_score_and_missing_or_duplicate_dimensions(evaluation):
    data = {k: v for k, v in evaluation.items() if k != "overall_score"}
    data["dimension_scores"][0]["score"] = 10
    assert score_evaluation(Evaluation(**data), DEFAULT_CONFIG)["overall_score"] == 8.6
    data["dimension_scores"].pop()
    with pytest.raises(ValueError):
        score_evaluation(Evaluation(**data), DEFAULT_CONFIG)
    data["dimension_scores"].append(deepcopy(data["dimension_scores"][0]))
    with pytest.raises(ValueError):
        score_evaluation(Evaluation(**data), DEFAULT_CONFIG)


def test_trend_analysis_keeps_chronological_order_and_rubric_context():
    payloads = []
    result = {"summary": "Two attempts", "strengths": [], "recurring_weaknesses": [], "trends": [],
              "recommendations": ["Practice a direct opening"], "next_training_focus": ["Clarity"], "limitations": "Small sample"}

    def handler(request):
        payloads.append(json.loads(json.loads(request.content)["input"][1]["content"]))
        return httpx.Response(200, json=response_envelope([
            {"type": "output_text", "text": json.dumps(result), "annotations": []}]))

    ai = CoachAI(Settings(database_url="unused"), client=sdk_client(handler))
    history = [{"id": n, "created_at": str(n), "question_text": "Question", "evaluation": {},
                "config_snapshot": DEFAULT_CONFIG, "model": "model"} for n in (2, 1)]
    assert ai.analyze(DEFAULT_CONFIG, history)["limitations"] == "Small sample"
    assert [a["attempt_id"] for a in payloads[0]["attempts"]] == [1, 2]
    assert payloads[0]["attempts"][0]["rubric"] == DEFAULT_CONFIG["rubric"]
