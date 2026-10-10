"""Local publisher metadata survives persistence without becoming evidence."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from module.resource_analysis.source import capture_source, publisher_context, publisher_instruction
from module.resource_analysis.evidence import Evidence
from module.resource_analysis.library import CatalogStore, history_entries, merged_entries
from module.resource_analysis.summary import build_narrative


class SourceContextTest(unittest.TestCase):
    def test_group_is_exact_and_raw_caption_not_naming_cache(self):
        app = SimpleNamespace(get_caption_name=lambda *args: "wrong sanitized caption")
        message = SimpleNamespace(id=1, media_group_id="a", caption="标题：第一行\n第二行。")
        original = capture_source(message, app, 123, "original.mp4")
        message.id, message.caption = 2, ""
        self.assertEqual(original["caption"], capture_source(message, app, 123)["caption"])
        self.assertEqual("1", capture_source(message, app, 123)["caption_message_id"])
        self.assertEqual("", capture_source(message, app, 999)["caption"])
        message.media_group_id = None
        self.assertEqual("", capture_source(message, app, 123)["caption"])

    def test_sharing_is_strict_opt_in_and_excludes_identifiers(self):
        source = {"caption": "发布者背景 https://example.com/private @private_channel", "chat_id": "private-id"}
        for flag in (False, None, "true", 1):
            self.assertEqual({}, publisher_context(source, allowed=flag))
        value = publisher_context(source, allowed=True)
        self.assertEqual("发布者背景", value["publisher_description"])
        self.assertNotIn("private", str(value))
        evidence = Evidence("video", publisher_context=value)
        self.assertFalse(evidence.usable)
        self.assertIn("不是直接证据", publisher_instruction(evidence))
        narrative = build_narrative({"version": 2, "mode": "activity", "events": [{
            "importance": "content", "phase": "activity", "action": "参与者协作完成一个任务。", "evidence_refs": ["publisher_description"]}]}, evidence)
        self.assertIn("invalid_event_evidence", narrative["issues"])

    def test_history_and_catalog_restart_keep_raw_source(self):
        source = {"caption": "两行\n原文", "message_id": "1"}
        history = {1: {1: {"file_name": "renamed.mp4", "state": "completed", "source_context": source}}}
        relative = lambda root, name: name
        entries = history_entries(history, "", relative)
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.json")
            store.put(entries["renamed.mp4"])
            saved = CatalogStore(store.path).snapshot()["renamed.mp4"]
            self.assertEqual(source, saved["source_context"])
            del saved["source_context"]
            store.put(saved)
            self.assertEqual(source, merged_entries(history, "", relative, store)["renamed.mp4"]["source_context"])
