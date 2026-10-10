"""Content coverage regressions: public analysis/extraction seams, synthetic data only."""

import base64
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

from module.resource_analysis import analyze_resource
from module.resource_analysis.evidence import Evidence, extract_evidence
from module.resource_analysis.taxonomy import (
    ADULT_BY_ID,
    CATEGORY_BY_ID,
    categories_for,
    classification_guidance,
)
from module.resource_analysis.service import CompletionClient
from tests.module.test_analysis_recovery import Response
from tests.module.test_resource_analysis import ScriptedModel, uncertain_details


class CoverageTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.evidence = Evidence("image", images=["synthetic-only"], sources=["image"])
        self.extract = self.enterContext(
            mock.patch(
                "module.resource_analysis.service.extract_evidence",
                return_value=self.evidence,
            )
        )

    async def test_static_cartoon_has_a_compatible_category_without_a_plot(self):
        model = ScriptedModel("illustration", "image")
        result = await analyze_resource(
            __file__, resource_name="picture.jpg", completion=model
        )
        self.assertEqual("complete", result["status"])
        self.assertEqual("illustration", result["category"])
        self.assertIn("主体与元素", result["facets"])
        self.assertIn("不需要剧情", model.prompts[0])
        self.assertNotIn("内容梗概", result["summary"])

    async def test_analysis_prompts_do_not_anchor_on_unverified_resource_metadata(self):
        model = ScriptedModel("illustration", "image")
        self.evidence.text = "正文中的实际可见内容。"
        result = await analyze_resource(
            __file__,
            resource_name="unverified-private-title.jpg",
            text_hints={
                "file_name": "unverified-private-title.jpg",
                "caption": "unverified-publisher-claim",
                "chat_title": "unverified-channel-name",
            },
            completion=model,
        )
        self.assertEqual("complete", result["status"])
        self.assertEqual(2, len(model.prompts))
        for prompt in model.prompts:
            with self.subTest(stage=model.prompts.index(prompt)):
                context = json.loads(prompt.split("\n素材：", 1)[1])
                self.assertEqual("image", context["resource_kind"])
                self.assertEqual(self.evidence.text, context["extracted_text"])
                self.assertEqual(["image"], context["evidence_sources"])
                self.assertNotIn("unverified-private-title", prompt)
                self.assertNotIn("unverified-publisher-claim", prompt)
                self.assertNotIn("unverified-channel-name", prompt)

    async def test_legacy_filename_hint_still_detects_kind_without_reaching_model(self):
        model = ScriptedModel("illustration", "image")
        result = await analyze_resource(
            __file__,
            text_hints={"file_name": "private-legacy-name.jpg"},
            completion=model,
        )
        self.assertEqual("complete", result["status"])
        self.assertEqual("image", result["resource_kind"])
        self.assertEqual(
            "private-legacy-name.jpg", self.extract.call_args.kwargs["name"]
        )
        self.assertTrue(
            all("private-legacy-name" not in prompt for prompt in model.prompts)
        )

    def test_image_vocabulary_covers_stills_not_just_entertainment(self):
        ids = {category.id for category in categories_for("image")}
        self.assertTrue({"illustration", "meme", "screenshot"} <= ids)
        self.assertNotIn(
            "illustration", {category.id for category in categories_for("audio")}
        )

    async def test_nsfw_and_photography_are_separated_by_theme_not_graphicness(self):
        self.assertIn("非情色", CATEGORY_BY_ID["photography"].hint)
        self.assertIn("非露骨", CATEGORY_BY_ID["adult"].hint)
        self.assertIn("情色主题", classification_guidance("video"))
        self.assertIn("不以看过全片为前提", classification_guidance("video"))
        model = ScriptedModel("adult", "image", "independent")
        result = await analyze_resource(
            __file__, resource_name="fixture.jpg", completion=model
        )
        self.assertEqual("complete", result["status"])
        self.assertEqual("adult", result["category"])
        self.assertIn("不等于非成人", model.prompts[0])
        self.assertIn("matches_category", model.prompts[1])

    async def test_independent_means_observed_filming_style_not_verified_authorship(
        self,
    ):
        hint = ADULT_BY_ID["independent"].hint
        self.assertIn("拍摄风格", hint)
        self.assertIn("不要求作者", hint)
        self.assertIn("不认证", hint)
        model = ScriptedModel("adult", "video", "independent")
        self.evidence.kind = "video"
        self.evidence.sources = ["video_frames"]
        result = await analyze_resource(
            __file__, resource_name="fixture.mp4", completion=model
        )
        self.assertEqual("complete", result["status"])
        for prompt in model.prompts[:2]:
            self.assertIn(hint, prompt)

    async def test_uncertain_category_preserves_observations_without_forcing_adult(
        self,
    ):
        model = mock.AsyncMock(
            return_value={
                "resource_kind": "image",
                "category": "other",
                "confidence": 0.7,
                "evidence_sources": ["image"],
                "decision_reason": "不能确定用途。",
                "observations": [{"text": "画面为蓝色几何图形。", "sources": ["image"]}],
            }
        )
        model.side_effect = [model.return_value, uncertain_details()]
        result = await analyze_resource(
            __file__,
            resource_name="picture.jpg",
            text_hints={"caption": "untrusted adult label"},
            completion=model,
            min_confidence=0.4,
        )
        self.assertEqual("needs_review", result["status"])
        self.assertEqual("other", result["category"])
        self.assertEqual("画面为蓝色几何图形。", result["content_description"])
        self.assertEqual(["image"], result["observations"][0]["sources"])
        self.assertEqual(2, model.call_count)
        self.assertEqual("model_uncertain", result["reason"])
        self.assertEqual("passed", result["diagnostics"]["validation"]["status"])
        self.assertEqual({}, result["facets"])

    async def test_observation_cannot_invent_an_unprovided_evidence_source(self):
        model = mock.AsyncMock(
            return_value={
                "resource_kind": "image",
                "category": "other",
                "confidence": 0.7,
                "evidence_sources": ["image"],
                "observations": [
                    {"text": "伪造的声音说明", "sources": ["transcript"]},
                    {"text": "可见几何线条。", "sources": ["image"]},
                    {"text": "没有来源", "sources": []},
                ],
            }
        )
        result = await analyze_resource(
            __file__, resource_name="picture.jpg", completion=model
        )
        self.assertEqual("可见几何线条。", result["content_description"])
        self.assertEqual(1, len(result["observations"]))

    async def test_low_confidence_never_hides_identified_content_or_changes_threshold(
        self,
    ):
        model = mock.AsyncMock(
            return_value={
                "resource_kind": "image",
                "category": "illustration",
                "confidence": 0.3,
                "evidence_sources": ["image"],
                "observations": [{"text": "可见绘制的花朵。", "sources": ["image"]}],
            }
        )
        result = await analyze_resource(
            __file__, resource_name="picture.jpg", completion=model, min_confidence=0.4
        )
        self.assertEqual("needs_review", result["status"])
        self.assertEqual("other", result["category"])
        self.assertEqual("low_confidence", result["reason"])
        self.assertEqual("可见绘制的花朵。", result["content_description"])
        self.assertEqual(0.4, result["diagnostics"]["threshold"])

    async def test_selected_timestamps_are_attached_to_the_matching_image(self):
        evidence = Evidence(
            "video",
            images=["frame-a", "frame-b"],
            sources=["video_frames"],
            sampling={"timestamps": [1.2, 9.109]},
        )
        session = mock.Mock()
        session.post.return_value = Response(
            data={"choices": [{"message": {"content": json.dumps({"ok": True})}}]}
        )
        await CompletionClient(
            session, "fixture-only", "https://fixture.invalid", "fixture"
        )("prompt", evidence)
        content = session.post.call_args.kwargs["json"]["messages"][1]["content"]
        self.assertEqual(
            ["text", "text", "image_url", "text", "image_url"],
            [item["type"] for item in content],
        )
        self.assertIn("1.2 秒", content[1]["text"])
        self.assertEqual("frame-a", content[2]["image_url"]["url"])
        self.assertIn("9.109 秒", content[3]["text"])
        self.assertEqual("frame-b", content[4]["image_url"]["url"])

    async def test_prompts_provide_concrete_json_contracts_not_ambiguous_original_ids(
        self,
    ):
        model = ScriptedModel("illustration", "image")
        await analyze_resource(__file__, resource_name="picture.jpg", completion=model)
        examples = [
            json.loads(prompt.split("输出结构：\n", 1)[1].split("\n素材：", 1)[0])
            for prompt in model.prompts
        ]
        self.assertEqual("image", examples[0]["resource_kind"])
        self.assertEqual(["image"], examples[0]["observations"][0]["sources"])
        self.assertEqual("image", examples[1]["resource_kind"])
        self.assertEqual("illustration", examples[1]["category"])
        self.assertEqual("", examples[1]["subtype"])
        self.assertEqual({"主体与元素", "画风与配色", "图像形式"}, set(examples[1]["facets"]))
        self.assertIn("中文", model.prompts[0])
        self.assertIn("中文", model.prompts[1])

    async def test_provider_no_subtype_alias_does_not_discard_valid_nonadult_analysis(
        self,
    ):
        for empty in ("无", None):
            model = ScriptedModel("illustration", "image")

            async def provider(prompt, evidence):
                value = await model(prompt, evidence)
                if "facets" in value:
                    value["subtype"] = empty
                return value

            with self.subTest(empty=empty):
                result = await analyze_resource(
                    __file__, resource_name="picture.jpg", completion=provider
                )
                self.assertEqual("complete", result["status"])
                self.assertEqual("illustration", result["category"])
                self.assertEqual("", result["subtype"])

    async def test_empty_subtype_alias_never_overrides_a_real_adult_subtype(self):
        model = ScriptedModel("adult", "image", "studio")

        async def provider(prompt, evidence):
            value = await model(prompt, evidence)
            if "facets" in value:
                value["subtype"] = "无"
            return value

        result = await analyze_resource(
            __file__, resource_name="picture.jpg", completion=provider
        )
        self.assertEqual("needs_review", result["status"])
        self.assertEqual("inconsistent_analysis", result["reason"])
        self.assertEqual({}, result["facets"])


class SamplingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []

    def frame(self, args, **kwargs):
        seconds = float(args[args.index("-ss") + 1])
        self.calls.append(seconds)
        # The old 2.5, 7.5, 12.5, 17.5 samples miss the brief red scene entirely.
        color = "black" if seconds < 2 else "red" if 9 <= seconds <= 11 else "blue"
        Image.new("RGB", (64, 64), color).save(args[-1])

    def test_diversity_sampling_catches_brief_scene_without_sending_more_images(self):
        with mock.patch(
            "module.resource_analysis.evidence._probe", return_value=20
        ), mock.patch("module.resource_analysis.evidence._run", side_effect=self.frame):
            evidence = extract_evidence("fixture.mp4", self.root, max_frames=4)
        self.assertEqual(4, len(evidence.images))
        colors = []
        for image in evidence.images:
            with Image.open(
                io.BytesIO(base64.b64decode(image.split(",", 1)[1]))
            ) as picture:
                colors.append(picture.getpixel((10, 10)))
        self.assertTrue(
            any(red > 200 and blue < 50 for red, green, blue in colors),
            "brief scene was missed",
        )
        self.assertTrue(
            all(max(color) > 20 for color in colors),
            "black lead-in used despite other readable frames",
        )
        self.assertGreater(len(self.calls), 4)
        self.assertLessEqual(len(self.calls), 48)
        self.assertEqual(4, len(evidence.sampling["timestamps"]))
        self.assertEqual(
            sorted(evidence.sampling["timestamps"]), evidence.sampling["timestamps"]
        )

    def test_static_video_still_has_bounded_timeline_samples(self):
        def blue(args, **kwargs):
            self.calls.append(float(args[args.index("-ss") + 1]))
            Image.new("RGB", (64, 64), "blue").save(args[-1])

        with mock.patch(
            "module.resource_analysis.evidence._probe", return_value=20
        ), mock.patch("module.resource_analysis.evidence._run", side_effect=blue):
            evidence = extract_evidence("fixture.mp4", self.root, max_frames=3)
        self.assertEqual(3, len(evidence.images))
        self.assertLessEqual(len(self.calls), 48)
        self.assertGreater(
            evidence.sampling["timestamps"][-1] - evidence.sampling["timestamps"][0], 10
        )

    def dim_watermarked_frame(self, args, **kwargs):
        seconds = float(args[args.index("-ss") + 1])
        self.calls.append(seconds)
        color = (180, 125, 75) if seconds < 12 else (5, 3, 8)
        image = Image.new("RGB", (64, 64), color)
        ImageDraw.Draw(image).rectangle((0, 0, 9, 9), fill="white")
        image.save(args[-1])

    def test_dark_watermark_frames_do_not_displace_available_readable_content(self):
        with mock.patch(
            "module.resource_analysis.evidence._probe", return_value=20
        ), mock.patch(
            "module.resource_analysis.evidence._run",
            side_effect=self.dim_watermarked_frame,
        ):
            evidence = extract_evidence("fixture.mp4", self.root, max_frames=4)
        self.assertEqual(4, len(evidence.images))
        self.assertTrue(
            all(seconds < 12 for seconds in evidence.sampling["timestamps"]),
            "Lighting changes or bright watermarks displaced clear content frames",
        )
        self.assertGreater(evidence.sampling["quality_filtered_count"], 0)

    def test_dark_only_video_keeps_bounded_samples_instead_of_losing_all_evidence(self):
        def dark(args, **kwargs):
            self.calls.append(float(args[args.index("-ss") + 1]))
            image = Image.new("RGB", (64, 64), (5, 3, 8))
            ImageDraw.Draw(image).rectangle((0, 0, 9, 9), fill="white")
            image.save(args[-1])

        with mock.patch(
            "module.resource_analysis.evidence._probe", return_value=20
        ), mock.patch("module.resource_analysis.evidence._run", side_effect=dark):
            evidence = extract_evidence("fixture.mp4", self.root, max_frames=4)
        self.assertEqual(4, len(evidence.images))
        self.assertEqual(["video_frames"], evidence.sources)
        self.assertEqual(0, evidence.sampling["quality_filtered_count"])
        self.assertLessEqual(len(self.calls), 48)

    def test_black_padding_does_not_hide_readable_late_video_content(self):
        def inset_content(args, **kwargs):
            seconds = float(args[args.index("-ss") + 1])
            if seconds < 12:
                image = Image.new("RGB", (128, 128), (180, 125, 75))
            else:
                image = Image.new("RGB", (128, 128), "black")
                draw = ImageDraw.Draw(image)
                # A smaller, readable scene in a black canvas, not just a watermark.
                draw.rectangle((44, 40, 83, 87), fill=(150, 110, 70))
                draw.rectangle((58, 42, 80, 79), fill=(240, 190, 100))
                draw.rectangle((0, 0, 8, 8), fill="white")
            image.save(args[-1])

        with mock.patch(
            "module.resource_analysis.evidence._probe", return_value=20
        ), mock.patch(
            "module.resource_analysis.evidence._run", side_effect=inset_content
        ):
            evidence = extract_evidence("fixture.mp4", self.root, max_frames=4)
        self.assertEqual(4, len(evidence.images))
        self.assertTrue(
            any(seconds >= 12 for seconds in evidence.sampling["timestamps"]),
            "Readable inset content in the later video was discarded as darkness",
        )
        self.assertEqual(12, evidence.sampling["readable_candidate_count"])

    def test_sampling_preserves_image_limit_even_with_many_candidates(self):
        with mock.patch(
            "module.resource_analysis.evidence._probe", return_value=20
        ), mock.patch("module.resource_analysis.evidence._run", side_effect=self.frame):
            evidence = extract_evidence("fixture.mp4", self.root, max_frames=1)
        self.assertEqual(1, len(evidence.images))
        self.assertLessEqual(len(self.calls), 48)

    def test_sampling_stops_at_budget_and_keeps_available_frames(self):
        now = [0]

        def slow(args, **kwargs):
            self.assertLessEqual(kwargs["timeout"], 10)
            self.frame(args, **kwargs)
            now[0] += 61

        with mock.patch(
            "module.resource_analysis.evidence._probe", return_value=20
        ), mock.patch(
            "module.resource_analysis.evidence._run", side_effect=slow
        ), mock.patch(
            "module.resource_analysis.evidence.time.monotonic",
            side_effect=lambda: now[0],
        ):
            evidence = extract_evidence("fixture.mp4", self.root, max_frames=4)
        self.assertEqual(1, len(self.calls))
        self.assertEqual(1, len(evidence.images))
        self.assertTrue(any("60秒上限" in limit for limit in evidence.limitations))

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed"
    )
    def test_real_video_brief_scene_survives_selection_without_modifying_original(self):
        path = self.root / "brief-scene.mp4"
        subprocess.run(
            [
                shutil.which("ffmpeg"),
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=64x64:r=4",
                "-t",
                "20",
                "-vf",
                "drawbox=color=red:t=fill:enable='between(t,9,11)'",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=30,
            **(
                {"creationflags": subprocess.CREATE_NO_WINDOW}
                if os.name == "nt"
                else {}
            )
        )
        before = path.read_bytes()
        evidence = extract_evidence(path, self.root, max_frames=4)
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(4, len(evidence.images))
        self.assertTrue(
            any(9 <= seconds <= 11 for seconds in evidence.sampling["timestamps"])
        )
        colors = []
        for encoded in evidence.images:
            with Image.open(
                io.BytesIO(base64.b64decode(encoded.split(",", 1)[1]))
            ) as frame:
                colors.append(frame.getpixel((10, 10)))
        self.assertTrue(any(red > 200 and blue < 50 for red, green, blue in colors))


if __name__ == "__main__":
    unittest.main()
