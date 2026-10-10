"""Bounded, evidence-checked semantic correction through the public interface."""

import copy
import json
import unittest
from unittest import mock

from module.resource_analysis import analyze_resource
from module.resource_analysis.evidence import Evidence
from module.resource_analysis.service import ModelError
from module.resource_analysis.taxonomy import analysis_profile


class CorrectionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.evidence = Evidence(
            "video", images=["synthetic"], sources=["video_frames"]
        )
        self.extract = self.enterContext(
            mock.patch(
                "module.resource_analysis.service.extract_evidence",
                return_value=self.evidence,
            )
        )
        self.enterContext(
            mock.patch(
                "module.resource_analysis.service._retry_pause", new=mock.AsyncMock()
            )
        )
        self.classified = {
            "resource_kind": "video",
            "category": "adult",
            "subtype": "studio",
            "confidence": 0.9,
            "evidence_sources": ["video_frames"],
        }
        self.proposal = {
            "resource_kind": "video",
            "category": "adult",
            "subtype": "independent",
            "confidence": 0.8,
            "evidence_sources": ["video_frames"],
        }

    def details(self, subtype, *, accepted=True, correction=None):
        return {
            "resource_kind": "video",
            "category": "adult",
            "subtype": subtype,
            "matches_category": accepted,
            "facets": {
                key: "已提供画面支持的拍摄形式。" if accepted else "PRIVATE_REJECTED_PROSE"
                for key in analysis_profile("adult", subtype).directions
            },
            "tags": [],
            "evidence_sources": ["video_frames"],
            "correction": correction,
        }

    async def run_replies(self, replies):
        classification_completion = mock.AsyncMock(
            side_effect=[
                copy.deepcopy(reply) if isinstance(reply, dict) else reply
                for reply in replies
            ]
        )
        async def completion(prompt, evidence):
            if prompt.startswith("叙事模式"):
                return {"mode": "insufficient", "basis": "unreadable", "evidence_refs": []}
            if prompt.startswith("阶段三"):
                return {"mode": "insufficient", "events": []}
            return await classification_completion(prompt, evidence)
        result = await analyze_resource(
            __file__,
            resource_name="fixture.mp4",
            completion=completion,
            min_confidence=0.65,
        )
        # These assertions count classification/correction calls only; event and
        # writing orchestration has separate public-interface regression tests.
        return result, classification_completion

    async def test_model_proposal_is_reanalyzed_in_new_dimensions_not_trusted_as_success(
        self,
    ):
        result, completion = await self.run_replies(
            [
                self.classified,
                self.details("studio", accepted=False, correction=self.proposal),
                self.details("independent"),
            ]
        )
        self.assertEqual("complete", result["status"])
        self.assertEqual(
            ("adult", "independent"), (result["category"], result["subtype"])
        )
        self.assertEqual(0.8, result["confidence"])
        self.assertEqual(3, completion.await_count)
        self.assertEqual(1, self.extract.call_count)
        self.assertEqual(
            set(analysis_profile("adult", "independent").directions),
            set(result["facets"]),
        )
        self.assertNotIn("PRIVATE_REJECTED_PROSE", json.dumps(result))
        self.assertTrue(
            all(call.args[1] is self.evidence for call in completion.call_args_list)
        )
        self.assertEqual(
            ["failed", "passed"],
            [item["status"] for item in result["diagnostics"]["validation_history"]],
        )
        self.assertIs(
            False,
            result["diagnostics"]["validation_history"][0]["received"][
                "matches_category"
            ]["value"],
        )
        detail_attempts = [item for item in result["diagnostics"]["attempts"] if item["stage"] == "details"]
        self.assertEqual(1, detail_attempts[-1]["revision"])

    async def test_invalid_or_low_confidence_proposals_never_trigger_correction(self):
        changes = [
            {"confidence": 0.2},
            {"confidence": True},
            {"confidence": float("nan")},
            {"confidence": 1.1},
            {"resource_kind": "image"},
            {"category": "illustration", "subtype": ""},
            {"category": "other", "subtype": ""},
            {"subtype": "PRIVATE_UNKNOWN"},
            {"subtype": "studio"},
            {"evidence_sources": ["PRIVATE_SOURCE"]},
        ]
        for change in changes:
            with self.subTest(change=change):
                result, completion = await self.run_replies(
                    [
                        self.classified,
                        self.details(
                            "studio",
                            accepted=False,
                            correction={**self.proposal, **change},
                        ),
                    ]
                )
                self.assertEqual("needs_review", result["status"])
                self.assertEqual(2, completion.await_count)
                self.assertEqual({}, result["facets"])
                self.assertNotIn("PRIVATE_", json.dumps(result))

    async def test_wrong_main_category_is_rebuilt_from_the_corrected_profile(self):
        classified = {
            **self.classified,
            "category": "photography",
            "subtype": "",
            "confidence": 0.7,
        }
        rejected = {
            **self.details("studio", accepted=False, correction=self.proposal),
            "category": "photography",
            "subtype": "",
            "facets": {
                key: "PRIVATE_REJECTED_PROSE"
                for key in analysis_profile("photography").directions
            },
        }
        result, completion = await self.run_replies(
            [classified, rejected, self.details("independent")]
        )
        self.assertEqual("complete", result["status"])
        self.assertEqual("adult", result["category"])
        self.assertEqual(0.7, result["confidence"])
        self.assertEqual(3, completion.await_count)
        self.assertEqual(
            "photography", result["diagnostics"]["corrections"][0]["from"]["category"]
        )
        self.assertNotIn("拍摄主题", result["summary"])
        self.assertNotIn("PRIVATE_", json.dumps(result))

    async def test_malformed_review_is_not_a_semantic_correction_opportunity(self):
        rejected = self.details("studio", accepted=False, correction=self.proposal)
        rejected["facets"] = {"PRIVATE_UNKNOWN": "PRIVATE_REJECTED_PROSE"}
        result, completion = await self.run_replies([self.classified, rejected])
        self.assertEqual("needs_review", result["status"])
        self.assertEqual(2, completion.await_count)
        self.assertNotIn("PRIVATE_", json.dumps(result))

    async def test_second_conflict_stops_even_if_it_proposes_another_category(self):
        result, completion = await self.run_replies(
            [
                self.classified,
                self.details("studio", accepted=False, correction=self.proposal),
                self.details(
                    "independent",
                    accepted=False,
                    correction={**self.proposal, "subtype": "other"},
                ),
            ]
        )
        self.assertEqual("needs_review", result["status"])
        self.assertEqual("inconsistent_analysis", result["reason"])
        self.assertEqual(3, completion.await_count)
        self.assertEqual({}, result["facets"])
        self.assertEqual(
            ["failed", "failed"],
            [item["status"] for item in result["diagnostics"]["validation_history"]],
        )

    async def test_correction_refusal_is_not_retried_or_replaced(self):
        result, completion = await self.run_replies(
            [
                self.classified,
                self.details("studio", accepted=False, correction=self.proposal),
                ModelError("model_refused", "模型拒绝"),
            ]
        )
        self.assertEqual("failed", result["status"])
        self.assertEqual("model_refused", result["reason"])
        self.assertEqual(3, completion.await_count)
        self.assertEqual({}, result["facets"])

    async def test_correction_transport_retry_does_not_restart_previous_stages(self):
        result, completion = await self.run_replies(
            [
                self.classified,
                self.details("studio", accepted=False, correction=self.proposal),
                TimeoutError(),
                self.details("independent"),
            ]
        )
        self.assertEqual("complete", result["status"])
        self.assertEqual(4, completion.await_count)
        self.assertEqual(
            completion.call_args_list[2].args[0], completion.call_args_list[3].args[0]
        )
        self.assertEqual(1, self.extract.call_count)

    async def test_unsolicited_proposal_does_not_change_an_accepted_classification(
        self,
    ):
        result, completion = await self.run_replies(
            [self.classified, self.details("studio", correction=self.proposal)]
        )
        self.assertEqual("complete", result["status"])
        self.assertEqual("studio", result["subtype"])
        self.assertEqual(2, completion.await_count)

    def unknown_review(self, *, correction=None):
        return {
            "resource_kind": "video",
            "category": "other",
            "subtype": "",
            "matches_category": correction is None,
            "facets": {"已知信息": "可见室内固定机位画面。", "证据限制": "无法核实制作来源。"},
            "tags": [],
            "evidence_sources": ["video_frames"],
            "correction": correction,
        }

    async def test_uncertain_first_pass_can_propose_a_verified_category_in_content_review(
        self,
    ):
        classified = {
            **self.classified,
            "category": "other",
            "subtype": "",
            "confidence": 0.7,
            "decision_reason": "主要用途仍需核验。",
        }
        result, completion = await self.run_replies(
            [
                classified,
                self.unknown_review(correction=self.proposal),
                self.details("independent"),
            ]
        )
        self.assertEqual("complete", result["status"])
        self.assertEqual("adult", result["category"])
        self.assertEqual(3, completion.await_count)
        self.assertEqual(
            "other", result["diagnostics"]["corrections"][0]["from"]["category"]
        )

    async def test_unresolved_content_review_never_becomes_completed_other_category(
        self,
    ):
        classified = {
            **self.classified,
            "category": "other",
            "subtype": "",
            "confidence": 0.7,
        }
        result, completion = await self.run_replies([classified, self.unknown_review()])
        self.assertEqual("needs_review", result["status"])
        self.assertEqual("model_uncertain", result["reason"])
        self.assertEqual("other", result["category"])
        self.assertEqual({}, result["facets"])
        self.assertEqual(2, completion.await_count)

    async def test_structured_refusal_and_low_confidence_are_not_followed_up(self):
        for confidence, note, expected in (
            (0.7, "抱歉，我无法分析该内容。", "model_refused"),
            (0.3, "用途不能确认。", "model_uncertain"),
        ):
            result, completion = await self.run_replies(
                [
                    {
                        **self.classified,
                        "category": "other",
                        "subtype": "",
                        "confidence": confidence,
                        "decision_reason": note,
                    }
                ]
            )
            self.assertEqual(expected, result["reason"])
            self.assertNotEqual("complete", result["status"])
            self.assertEqual(1, completion.await_count)


if __name__ == "__main__":
    unittest.main()
