"""Recovery at the real analysis boundary; no external model or user media."""

import json
import asyncio
import unittest
from unittest import mock

from module.resource_analysis.evidence import Evidence
from module.resource_analysis.service import CompletionClient, analyze_resource
from tests.module.test_resource_analysis import ScriptedModel, uncertain_details


class Response:
    def __init__(self, status=200, data=None):
        self.status, self.data = status, data
        self.headers = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self.data


class RecoveryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.evidence = Evidence("image", images=["synthetic-only"], sources=["image"])
        self.extract = self.enterContext(
            mock.patch(
                "module.resource_analysis.service.extract_evidence",
                return_value=self.evidence,
            )
        )
        self.enterContext(
            mock.patch(
                "module.resource_analysis.service._retry_pause",
                new=mock.AsyncMock(),
                create=True,
            )
        )

    async def analyze(self, completion, **options):
        return await analyze_resource(
            __file__, resource_name="fixture.jpg", completion=completion, **options
        )

    async def test_timeout_retries_only_failed_stage_and_preserves_evidence(self):
        model = ScriptedModel("photography", "image")
        calls = []

        async def flaky(prompt, evidence):
            calls.append(prompt)
            if len(calls) == 1:
                raise TimeoutError("private transport info")
            return await model(prompt, evidence)

        stages = []
        result = await self.analyze(flaky, progress_callback=stages.append)
        self.assertEqual("complete", result["status"])
        self.assertEqual(3, len(calls))
        self.assertEqual(1, self.extract.call_count)
        self.assertTrue(any("重试" in stage for stage in stages))
        attempts = result["diagnostics"]["attempts"]
        self.assertEqual(
            ["classification", "classification", "details"],
            [a["stage"] for a in attempts],
        )
        self.assertEqual([60, 120, 60], [a["timeout_seconds"] for a in attempts])

    async def test_details_timeout_does_not_repeat_classification(self):
        model = ScriptedModel("photography", "image")
        calls = []

        async def flaky(prompt, evidence):
            calls.append(prompt)
            if len(calls) == 2:
                raise TimeoutError()
            return await model(prompt, evidence)

        result = await self.analyze(flaky)
        self.assertEqual("complete", result["status"])
        self.assertEqual(
            ["classification", "details", "details"],
            [a["stage"] for a in result["diagnostics"]["attempts"]],
        )
        self.assertEqual(calls[1], calls[2])

    async def test_exhausted_timeout_is_actionable_and_does_not_erase_extraction(self):
        failure = mock.AsyncMock(side_effect=TimeoutError("secret-key-not-to-leak"))
        result = await self.analyze(failure)
        self.assertEqual(2, failure.call_count)
        self.assertEqual("failed", result["status"])
        self.assertEqual("model_timeout", result["reason"])
        self.assertEqual("classification", result["failure_stage"])
        self.assertEqual(["image"], result["evidence_sources"])
        self.assertIn("120", result["summary"])
        self.assertIn("分类", result["summary"])
        self.assertTrue(result["retryable"])
        self.assertNotIn("secret-key", json.dumps(result))

    async def test_other_at_threshold_reviews_content_without_repeating_classification(
        self,
    ):
        classify = mock.AsyncMock(
            return_value={
                "resource_kind": "image",
                "category": "other",
                "confidence": 0.4,
                "evidence_sources": ["image"],
                "decision_reason": "画面信息不足，无法确认主要用途。",
            }
        )
        classify.side_effect = [classify.return_value, uncertain_details()]
        result = await self.analyze(classify, min_confidence=0.4)
        self.assertEqual("needs_review", result["status"])
        self.assertEqual("model_uncertain", result["reason"])
        self.assertEqual("画面信息不足，无法确认主要用途。", result["review_note"])
        self.assertEqual(0.4, result["diagnostics"]["threshold"])
        self.assertEqual(2, classify.call_count)
        self.assertNotEqual(
            classify.call_args_list[0].args[0], classify.call_args_list[1].args[0]
        )
        self.assertEqual("passed", result["diagnostics"]["validation"]["status"])
        self.assertIn("无法确定", result["summary"])

    async def test_low_confidence_explains_threshold_not_model_refusal(self):
        result = await self.analyze(
            ScriptedModel("photography", "image", confidence=0.3), min_confidence=0.4
        )
        self.assertEqual("low_confidence", result["reason"])
        self.assertIn("30%", result["summary"])
        self.assertIn("40%", result["summary"])
        self.assertEqual("other", result["category"])

    async def test_http_auth_errors_are_specific_without_retry_or_raw_body(self):
        session = mock.Mock()
        session.post.return_value = Response(401, {"error": "secret body"})
        result = await self.analyze(
            CompletionClient(session, "secret", "https://fixture.invalid", "fixture")
        )
        self.assertEqual("model_auth", result["reason"])
        self.assertEqual(401, result["diagnostics"]["attempts"][0]["http_status"])
        self.assertFalse(result["retryable"])
        self.assertEqual(1, session.post.call_count)

    async def test_malformed_json_retries_once_without_reclassifying(self):
        model = ScriptedModel("photography", "image")
        classification = await model("first", self.evidence)
        details = await model("second", self.evidence)
        session = mock.Mock()
        replies = [json.dumps(classification), '{"facets":', json.dumps(details)]
        session.post.side_effect = [
            Response(data={"choices": [{"message": {"content": reply}}]})
            for reply in replies
        ]
        result = await self.analyze(
            CompletionClient(session, "fixture", "https://fixture.invalid", "fixture")
        )
        self.assertEqual("complete", result["status"])
        self.assertEqual(3, session.post.call_count)
        self.assertEqual(
            ["classification", "details", "details"],
            [attempt["stage"] for attempt in result["diagnostics"]["attempts"]],
        )
        self.assertNotIn('{"facets":', json.dumps(result))

    async def test_plain_text_refusal_is_not_misreported_as_retryable_json_error(self):
        for reply in (
            "I'm sorry, but I can't assist with this request.",
            "抱歉，我无法分析这张图片。",
        ):
            with self.subTest(reply=reply):
                session = mock.Mock()
                session.post.return_value = Response(
                    data={"choices": [{"message": {"content": reply}}]}
                )
                result = await self.analyze(
                    CompletionClient(
                        session, "fixture", "https://fixture.invalid", "fixture"
                    )
                )
                self.assertEqual("model_refused", result["reason"])
                self.assertFalse(result["retryable"])
                self.assertEqual(1, session.post.call_count)
                self.assertNotIn(reply, json.dumps(result, ensure_ascii=False))
        self.assertNotIn("secret", json.dumps(result))

    async def test_explicit_refusal_is_not_retried_or_bypassed(self):
        session = mock.Mock()
        session.post.return_value = Response(
            data={
                "choices": [
                    {"message": {"content": None, "refusal": "private refusal text"}}
                ]
            }
        )
        result = await self.analyze(
            CompletionClient(session, "secret", "https://fixture.invalid", "fixture")
        )
        self.assertEqual("model_refused", result["reason"])
        self.assertFalse(result["retryable"])
        self.assertEqual(1, session.post.call_count)
        self.assertNotIn("private refusal", json.dumps(result))

    async def test_invalid_model_json_is_not_misreported_as_timeout(self):
        session = mock.Mock()
        session.post.return_value = Response(
            data={"choices": [{"message": {"content": "private non-json text"}}]}
        )
        result = await self.analyze(
            CompletionClient(session, "secret", "https://fixture.invalid", "fixture")
        )
        self.assertEqual("model_invalid_response", result["reason"])
        self.assertFalse(result["retryable"])
        self.assertNotIn("private non-json", json.dumps(result))

    async def test_gateway_524_retries_once_with_a_specific_timeout_diagnosis(self):
        model = ScriptedModel("photography", "image")
        classification = await model("first", self.evidence)
        details = await model("second", self.evidence)
        session = mock.Mock()
        session.post.side_effect = [
            Response(524),
            Response(
                data={"choices": [{"message": {"content": json.dumps(classification)}}]}
            ),
            Response(data={"choices": [{"message": {"content": json.dumps(details)}}]}),
        ]
        result = await self.analyze(
            CompletionClient(session, "fixture", "https://fixture.invalid", "fixture")
        )
        self.assertEqual("complete", result["status"])
        self.assertEqual(3, session.post.call_count)
        self.assertEqual(1, self.extract.call_count)
        first = result["diagnostics"]["attempts"][0]
        self.assertEqual("model_gateway_timeout", first["reason"])
        self.assertEqual(524, first["http_status"])

    async def test_exhausted_gateway_timeout_never_becomes_a_complete_analysis(self):
        session = mock.Mock()
        session.post.return_value = Response(524, {"error": "private upstream body"})
        result = await self.analyze(
            CompletionClient(session, "fixture", "https://fixture.invalid", "fixture")
        )
        self.assertEqual("failed", result["status"])
        self.assertEqual("model_gateway_timeout", result["reason"])
        self.assertEqual(2, session.post.call_count)
        self.assertIn("524", result["summary"])
        self.assertTrue(result["retryable"])
        self.assertNotIn("private upstream body", json.dumps(result))

    async def test_transient_server_error_recovers_without_reextracting(self):
        model = ScriptedModel("photography", "image")
        first = await model("", self.evidence)
        second = await model("", self.evidence)
        session = mock.Mock()
        session.post.side_effect = [
            Response(503),
            Response(data={"choices": [{"message": {"content": json.dumps(first)}}]}),
            Response(data={"choices": [{"message": {"content": json.dumps(second)}}]}),
        ]
        result = await self.analyze(
            CompletionClient(session, "", "https://fixture.invalid", "fixture")
        )
        self.assertEqual("complete", result["status"])
        self.assertEqual(3, session.post.call_count)
        self.assertEqual(1, self.extract.call_count)

    async def test_real_http_timeout_cancels_request_and_retries_with_new_deadline(
        self,
    ):
        import aiohttp
        from aiohttp import web
        from module.resource_analysis.service import _base_result, _complete_stage

        calls = 0

        async def respond(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                await asyncio.sleep(0.5)
            return web.json_response(
                {"choices": [{"message": {"content": '{"ok":true}'}}]}
            )

        app = web.Application()
        app.router.add_post("/chat/completions", respond)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession() as session:
                client = CompletionClient(
                    session, "fixture", f"http://127.0.0.1:{port}", "fixture"
                )
                result = _base_result("image")
                value = await _complete_stage(
                    client,
                    "synthetic",
                    self.evidence,
                    "classification",
                    result,
                    0.15,
                    lambda stage: None,
                )
                self.assertEqual({"ok": True}, value)
                self.assertEqual(2, calls)
                self.assertEqual(
                    "model_timeout", result["diagnostics"]["attempts"][0]["reason"]
                )
                self.assertEqual(
                    0.3, result["diagnostics"]["attempts"][1]["timeout_seconds"]
                )
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    unittest.main()
