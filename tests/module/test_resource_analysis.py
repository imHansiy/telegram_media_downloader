"""Analysis interface regressions. No external model, Telegram or cloud access."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from PIL import Image

from module.resource_analysis import (
    analyze_resource,
    category_to_dir,
    detect_resource_kind,
)
from module.resource_analysis.evidence import Evidence, extract_evidence
from module.resource_analysis.paths import archive_path
from module.resource_analysis.taxonomy import (
    ADULT_SUBTYPES,
    CATEGORIES,
    analysis_profile,
)


def uncertain_details(kind="image", sources=None):
    return {
        "resource_kind": kind,
        "category": "other",
        "subtype": "",
        "matches_category": True,
        "facets": {key: "未能确认" for key in analysis_profile("other").directions},
        "tags": [],
        "evidence_sources": sources or ["image"],
        "correction": None,
    }


class ScriptedModel:
    def __init__(
        self,
        category="education",
        kind="document",
        subtype="",
        confidence=0.9,
        mismatch=False,
    ):
        self.category, self.kind, self.subtype = category, kind, subtype
        self.confidence, self.mismatch = confidence, mismatch
        self.prompts = []

    async def __call__(self, prompt, evidence):
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            return {
                "resource_kind": self.kind,
                "category": self.category,
                "subtype": self.subtype,
                "confidence": self.confidence,
                "evidence_sources": evidence.sources,
            }
        return {
            "resource_kind": self.kind,
            "category": "movie" if self.mismatch else self.category,
            "subtype": self.subtype,
            "matches_category": True,
            "evidence_sources": evidence.sources,
            "facets": {
                direction: f"可观察的{direction}"
                for direction in analysis_profile(
                    self.category, self.subtype
                ).directions
            },
            "tags": ["示例", "示例", "字幕", 42, "x" * 30],
        }


class AnalysisTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.resource = Path(self.temp.name) / "lesson.txt"
        self.resource.write_text("Python 入门课程：变量、循环、函数。适合初学者。", encoding="utf-8")

    async def test_document_gets_learning_summary_instead_of_video_story(self):
        model = ScriptedModel()
        result = await analyze_resource(str(self.resource), completion=model)
        self.assertEqual("complete", result["status"])
        self.assertEqual("education", result["category"])
        self.assertEqual("document", result["resource_kind"])
        self.assertIn("知识点与步骤", result["summary"])
        self.assertNotIn("内容梗概", result["summary"])
        self.assertEqual(["示例", "字幕"], result["tags"])
        self.assertEqual(2, len(model.prompts))
        self.assertIn('"category"', model.prompts[1])

    async def test_every_category_has_its_own_consistent_directions(self):
        for category in CATEGORIES:
            if category.id == "other":
                continue
            kind = category.kinds[0]
            subtype = "other" if category.id == "adult" else ""
            model = ScriptedModel(category.id, kind, subtype)
            with self.subTest(category=category.id), mock.patch(
                "module.resource_analysis.service.extract_evidence",
                return_value=Evidence(kind, "内容证据", sources=["document_text"]),
            ):
                result = await analyze_resource(str(self.resource), completion=model)
                self.assertEqual("complete", result["status"])
                self.assertEqual(
                    set(analysis_profile(category.id, subtype).directions),
                    set(result["facets"]),
                )

    async def test_nsfw_subtypes_keep_separate_directions_and_directories(self):
        for subtype in ADULT_SUBTYPES:
            model = ScriptedModel("adult", subtype.kinds[0], subtype.id)
            with self.subTest(subtype=subtype.id), mock.patch(
                "module.resource_analysis.service.extract_evidence",
                return_value=Evidence(
                    subtype.kinds[0], "中性的资源形式线索", sources=["video_frames"]
                ),
            ):
                result = await analyze_resource(str(self.resource), completion=model)
                self.assertEqual("complete", result["status"])
                self.assertEqual(subtype.id, result["subtype"])
                self.assertEqual(set(subtype.directions), set(result["facets"]))
                self.assertEqual(
                    f"nsfw/{subtype.label}",
                    category_to_dir("adult", result["resource_kind"], subtype.id),
                )

    async def test_conflicting_summary_is_discarded(self):
        result = await analyze_resource(
            str(self.resource), completion=ScriptedModel(mismatch=True)
        )
        self.assertEqual("needs_review", result["status"])
        self.assertEqual("other", result["category"])
        self.assertEqual({}, result["facets"])
        self.assertEqual([], result["tags"])
        self.assertIsNone(category_to_dir(result["category"]))

    async def test_low_confidence_does_not_generate_a_genre_summary(self):
        model = ScriptedModel(confidence=0.3)
        result = await analyze_resource(str(self.resource), completion=model)
        self.assertEqual("needs_review", result["status"])
        self.assertEqual(1, len(model.prompts))

    async def test_invalid_confidence_and_incompatible_category_are_not_confirmed(self):
        cases = [
            ScriptedModel(confidence=float("nan")),
            ScriptedModel(confidence=1.5),
            ScriptedModel(confidence=True),
            ScriptedModel("movie"),
            ScriptedModel("not_a_category"),
        ]
        for model in cases:
            with self.subTest(category=model.category, confidence=model.confidence):
                result = await analyze_resource(str(self.resource), completion=model)
                self.assertNotEqual("complete", result["status"])
                self.assertEqual([], result["tags"])

    async def test_threshold_zero_is_supported(self):
        result = await analyze_resource(
            str(self.resource),
            completion=ScriptedModel(confidence=0.2),
            min_confidence=0,
        )
        self.assertEqual("complete", result["status"])

    async def test_metadata_only_audio_does_not_invent_sound_or_dialogue(self):
        resource = Path(self.temp.name) / "music.mp3"
        resource.write_bytes(b"not-real-audio")
        model = ScriptedModel("music", "audio")
        result = await analyze_resource(
            str(resource), completion=model, transcribe=False
        )
        self.assertEqual("audio", result["resource_kind"])
        self.assertEqual("needs_review", result["status"])
        self.assertEqual([], model.prompts)
        self.assertTrue(any("未分析声音" in value for value in result["limitations"]))

    async def test_logical_filename_detects_document_downloaded_as_part(self):
        target = Path(self.temp.name) / "download.part"
        target.write_text(self.resource.read_text(encoding="utf-8"), encoding="utf-8")
        result = await analyze_resource(
            str(target), resource_name="lesson.txt", completion=ScriptedModel()
        )
        self.assertEqual("complete", result["status"])

    async def test_external_failure_becomes_recoverable_result(self):
        async def failure(*args):
            raise TimeoutError("must not appear in result")

        result = await analyze_resource(str(self.resource), completion=failure)
        self.assertEqual("failed", result["status"])
        self.assertNotIn("must not appear", json.dumps(result))


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_format_detection_uses_extension_then_mime_then_telegram_kind(self):
        for name, mime, telegram, expected in [
            ("clip.MKV", "application/octet-stream", "document", "video"),
            ("photo.bin", "image/jpeg", "document", "image"),
            ("book.pdf", "", "document", "document"),
            ("voice", "", "voice", "audio"),
            ("thing.zip", "", "document", "archive"),
            ("setup.apk", "", "document", "software"),
            ("something.bin", "", "document", "unknown"),
        ]:
            with self.subTest(name=name):
                self.assertEqual(expected, detect_resource_kind(name, mime, telegram))

    def test_image_is_resized_without_editing_original(self):
        path = self.root / "image.png"
        Image.new("RGB", (1400, 500), "blue").save(path)
        before = path.read_bytes()
        evidence = extract_evidence(path, self.root)
        self.assertEqual(["image"], evidence.sources)
        self.assertTrue(evidence.images[0].startswith("data:image/jpeg;base64,"))
        self.assertEqual(before, path.read_bytes())

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is not installed"
    )
    def test_video_uses_real_duration_and_seeked_frames(self):
        path = self.root / "sample.mp4"
        kwargs = (
            {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        )
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
                "2",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=30,
            **kwargs,
        )
        evidence = extract_evidence(path, self.root, max_frames=3)
        self.assertEqual(3, len(evidence.images))
        self.assertEqual(["video_frames"], evidence.sources)

    def test_archive_lists_entries_without_extracting_traversal_paths(self):
        path = self.root / "bundle.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("../../escaped.txt", "do not extract")
            archive.writestr("README.txt", "not sent as document text")
        evidence = extract_evidence(path, self.root)
        self.assertEqual(["archive_listing"], evidence.sources)
        self.assertIn("README.txt", evidence.text)
        self.assertNotIn("not sent as document text", evidence.text)
        self.assertEqual([path], list(self.root.iterdir()))

    def test_docx_reads_text_and_rejects_oversized_members(self):
        path = self.root / "report.docx"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", "<document><p>财务报告</p></document>")
        evidence = extract_evidence(path, self.root)
        self.assertEqual("财务报告", evidence.text)
        self.assertEqual(["document_text"], evidence.sources)

    def test_pdf_support_and_scan_limit(self):
        from pypdf import PdfWriter

        path = self.root / "scan.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(path)
        evidence = extract_evidence(path, self.root)
        self.assertFalse(evidence.usable)
        self.assertTrue(any("OCR" in value for value in evidence.limitations))

    def test_archive_path_preserves_source_and_date_and_does_not_move(self):
        original = self.root / "channel" / "2026-09-05" / "lesson.txt"
        analysis = {
            "status": "complete",
            "category": "education",
            "resource_kind": "document",
        }
        target = archive_path(self.root, original, analysis)
        self.assertTrue(
            target.endswith(os.path.join("channel", "2026-09-05", "lesson.txt"))
        )
        self.assertIn("课程与教程", target)
        self.assertEqual(target, archive_path(self.root, target, analysis))
        self.assertIsNone(
            archive_path(self.root, self.root.parent / "outside.txt", analysis)
        )
        self.assertFalse(Path(target).exists())


if __name__ == "__main__":
    unittest.main()
