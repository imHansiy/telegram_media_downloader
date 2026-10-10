"""Field-level validation through the real analysis entry point, synthetic replies only."""

import json
import unittest
from unittest import mock

from module.resource_analysis import analyze_resource
from module.resource_analysis.evidence import Evidence
from tests.module.test_resource_analysis import ScriptedModel


class ValidationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.evidence = Evidence("image", images=["synthetic-only"], sources=["image"])
        self.enterContext(
            mock.patch(
                "module.resource_analysis.service.extract_evidence",
                return_value=self.evidence,
            )
        )

    async def analyze(self, change):
        model = ScriptedModel("illustration", "image")

        async def reply(prompt, evidence):
            value = await model(prompt, evidence)
            if "facets" in value:
                change(value)
            return value

        return await analyze_resource(
            __file__, resource_name="fixture.jpg", completion=reply
        )

    async def test_every_failed_guard_identifies_the_exact_field(self):
        cases = [
            ("category", "photography", "category_mismatch"),
            ("resource_kind", "video", "resource_kind_mismatch"),
            ("subtype", "studio", "subtype_mismatch"),
            ("matches_category", False, "category_rejected"),
            ("matches_category", "true", "category_flag_invalid"),
            ("facets", [], "facets_type_invalid"),
            ("facets", {"主体与元素": "蓝色几何图形"}, "facet_keys_mismatch"),
            ("evidence_sources", [], "evidence_sources_invalid"),
        ]
        for field, value, code in cases:
            with self.subTest(field=field, code=code):
                result = await self.analyze(lambda reply: reply.update({field: value}))
                self.assertEqual("needs_review", result["status"])
                validation = result["diagnostics"]["validation"]
                self.assertEqual("failed", validation["status"])
                self.assertIn(code, validation["issues"])
                self.assertEqual("illustration", validation["expected"]["category"])
                self.assertEqual("details", result["failure_stage"])

    async def test_diagnostics_never_store_unknown_values_or_rejected_content(self):
        def corrupt(reply):
            reply.update(
                category="PRIVATE_CATEGORY_NAME",
                evidence_sources=["PRIVATE_SOURCE_NAME"],
            )
            reply["facets"]["PRIVATE_FACET_NAME"] = "PRIVATE_CONTENT"

        result = await self.analyze(corrupt)
        diagnostic = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("PRIVATE_", diagnostic)
        self.assertEqual(
            1,
            result["diagnostics"]["validation"]["received"]["facets"][
                "unknown_key_count"
            ],
        )

    async def test_invalid_facet_values_name_only_the_allowed_dimension(self):
        def corrupt(reply):
            reply["facets"]["主体与元素"] = {"content": "PRIVATE_CONTENT"}

        result = await self.analyze(corrupt)
        validation = result["diagnostics"]["validation"]
        self.assertIn("facet_values_invalid", validation["issues"])
        self.assertEqual(
            ["主体与元素"], validation["received"]["facets"]["invalid_value_keys"]
        )
        self.assertNotIn("PRIVATE_CONTENT", json.dumps(result))

    async def test_success_retains_a_safe_passed_validation_record(self):
        result = await self.analyze(lambda reply: None)
        self.assertEqual("complete", result["status"])
        self.assertEqual("passed", result["diagnostics"]["validation"]["status"])
        self.assertEqual([], result["diagnostics"]["validation"]["issues"])


if __name__ == "__main__":
    unittest.main()
