"""Model-specific request policy at the actual HTTP boundary, synthetic only."""

import json
import unittest
from unittest import mock

from module.resource_analysis.evidence import Evidence
from module.resource_analysis.service import CompletionClient, analyze_resource, ModelError
from tests.module.test_analysis_recovery import Response
from tests.module.test_resource_analysis import ScriptedModel


class RequestPolicyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.evidence = Evidence("image", images=["fixture"], sources=["image"])
        self.session = mock.Mock()
        self.session.post.return_value = Response(
            data={"choices": [{"message": {"content": '{"ok": true}'}}]}
        )

    async def test_grok_46_archival_requests_use_explicit_low_effort(self):
        client = CompletionClient(
            self.session, "fixture", "https://fixture.invalid", "grok-4.6"
        )
        await client("classification", self.evidence)
        await client("details", self.evidence)
        for request in self.session.post.call_args_list:
            payload = request.kwargs["json"]
            self.assertEqual("grok-4.6", payload["model"])
            self.assertEqual("low", payload.get("reasoning_effort"))
            self.assertEqual(0, payload["temperature"])
            self.assertEqual(
                "fixture", payload["messages"][1]["content"][1]["image_url"]["url"]
            )

    async def test_grok_46_requests_well_formed_json_without_changing_the_model(self):
        await CompletionClient(self.session, "fixture", "https://fixture.invalid", "grok-4.6")("prompt", self.evidence)
        payload = self.session.post.call_args.kwargs["json"]
        self.assertEqual({"type": "json_object"}, payload.get("response_format"))
        self.assertEqual("grok-4.6", payload["model"])

    async def test_narrative_schema_is_requested_and_locally_enforced(self):
        self.evidence.output_schema = {"type": "object", "properties": {
            "activity": {"type": "string", "enum": ["intimacy", "conversation"]}},
            "required": ["activity"], "additionalProperties": False}
        self.session.post.return_value = Response(data={"choices": [{"message": {"content": '{"activity":"intimacy"}'}}]})
        client = CompletionClient(self.session, "fixture", "https://fixture.invalid", "grok-4.6")
        await client("narrative", self.evidence)
        self.assertEqual("json_schema", self.session.post.call_args.kwargs["json"]["response_format"]["type"])
        self.session.post.return_value = Response(data={"choices": [{"message": {"content": '{"activity":"unrequested detail"}'}}]})
        with self.assertRaises(ModelError) as failure:
            await client("narrative", self.evidence)
        self.assertEqual("model_schema_mismatch", failure.exception.reason)

    async def test_other_models_do_not_receive_an_unverified_default_parameter(self):
        for model in ("fixture-model", "grok-4", "grok-4.6-unverified"):
            await CompletionClient(
                self.session, "fixture", "https://fixture.invalid", model
            )("prompt", self.evidence)
            self.assertNotIn(
                "reasoning_effort", self.session.post.call_args.kwargs["json"]
            )
            self.assertNotIn("response_format", self.session.post.call_args.kwargs["json"])

    async def test_schema_failure_records_rules_without_values_or_unknown_keys(self):
        from module.resource_analysis.service import _complete_stage
        self.evidence.output_schema = {"type": "object", "properties": {
            "events": {"type": "array", "items": {"type": "object", "properties": {
                "action": {"type": "string", "enum": ["allowed"]}},
                "required": ["action"], "additionalProperties": False}}},
            "required": ["events"], "additionalProperties": False}
        self.session.post.return_value = Response(data={"choices": [{"message": {"content": json.dumps({
            "events": [{"action": "PRIVATE_PROSE", "PRIVATE_KEY": "PRIVATE_VALUE"}]})}}]})
        result = {"diagnostics": {"attempts": []}}
        client = CompletionClient(self.session, "fixture", "https://fixture.invalid", "grok-4.6")
        await _complete_stage(client, "narrative", self.evidence, "narrative", result, 5, lambda _: None)
        issues = result["diagnostics"]["attempts"][0]["schema_issues"]
        self.assertIn({"path": ["events", 0, "action"], "rule": "enum"}, issues)
        self.assertIn({"path": ["events", 0], "rule": "additionalProperties"}, issues)
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(1, self.session.post.call_count)

    async def test_explicit_effort_and_provider_default_are_preserved(self):
        for effort in ("low", "medium", "high", "xhigh", "default"):
            client = CompletionClient(
                self.session,
                "fixture",
                "https://fixture.invalid",
                "grok-4.6",
                reasoning_effort=effort,
            )
            await client("prompt", self.evidence)
            self.assertEqual(
                None if effort == "default" else effort,
                self.session.post.call_args.kwargs["json"].get("reasoning_effort"),
            )

    def test_invalid_effort_is_rejected_before_a_request(self):
        for effort in ("unrecognized", {"private": "value"}, True):
            with self.assertRaises(ValueError):
                CompletionClient(
                    self.session,
                    "fixture",
                    "https://fixture.invalid",
                    "grok-4.6",
                    reasoning_effort=effort,
                )
        self.session.post.assert_not_called()

    async def test_public_analysis_records_the_actual_request_policy(self):
        model = ScriptedModel("illustration", "image")
        self.session.post.side_effect = [
            Response(
                data={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(await model(stage, self.evidence))
                            }
                        }
                    ]
                }
            )
            for stage in ("first", "second")
        ]
        transport = mock.MagicMock()
        transport.__aenter__ = mock.AsyncMock(return_value=self.session)
        transport.__aexit__ = mock.AsyncMock()
        with mock.patch(
            "module.resource_analysis.service.extract_evidence",
            return_value=self.evidence,
        ), mock.patch(
            "module.resource_analysis.service.aiohttp.ClientSession",
            return_value=transport,
        ):
            result = await analyze_resource(
                __file__, "fixture", resource_name="fixture.jpg", model="grok-4.6"
            )
        self.assertEqual("complete", result["status"])
        self.assertEqual("passed", result["diagnostics"]["validation"]["status"])
        self.assertEqual(
            {"reasoning_effort": "low", "response_format": {"type": "json_object"}}, result["diagnostics"].get("request_options")
        )
        self.assertEqual(2, self.session.post.call_count)

        prompt = self.session.post.call_args_list[0].kwargs["json"]["messages"][1][
            "content"
        ][0]["text"]
        shape = json.loads(prompt.split("输出结构：\n", 1)[1].split("\n素材：", 1)[0])
        fields = list(shape)
        self.assertLess(fields.index("observations"), fields.index("category"))
        self.assertLess(fields.index("decision_reason"), fields.index("category"))
        self.assertLess(fields.index("evidence_sources"), fields.index("category"))


if __name__ == "__main__":
    unittest.main()
