import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.monitoring import news_briefing as briefing


class NewsBriefingTests(unittest.TestCase):
    def test_deduplicate_syndicated_headlines(self):
        alerts = [
            {"urgency": "high", "headline": "Fed cuts rates by 25 basis points - Reuters"},
            {"urgency": "critical", "headline": "Fed cuts rates by 25 basis points — Reuters update"},
        ]
        self.assertEqual(len(briefing.deduplicate(alerts)), 1)

    def test_filter_directionless_whale_alert(self):
        alert = {
            "source": "whale:blockchain.info",
            "headline": "BTC whale transferred 700 BTC",
            "urgency": "high",
            "sentiment": "neutral",
        }
        self.assertFalse(briefing.is_ai_worthy(alert))

    def test_critical_news_is_ai_worthy(self):
        alert = {"source": "rss:Reuters", "headline": "Federal Reserve announces emergency rate cut", "urgency": "critical"}
        self.assertTrue(briefing.is_ai_worthy(alert))

    def test_legacy_alert_without_received_at_is_not_recent(self):
        self.assertFalse(briefing.is_recent({"headline": "old"}))

    def test_current_received_at_is_recent(self):
        alert = {"received_at": datetime.now(timezone.utc).isoformat()}
        self.assertTrue(briefing.is_recent(alert))

    def test_old_published_story_is_rejected(self):
        self.assertFalse(briefing.is_published_recent({"timestamp": "2020-01-01T00:00:00+00:00"}))

    def test_process_once_suppresses_no_alert(self):
        alert = {
            "id": "x",
            "source": "rss:Reuters",
            "headline": "Fed cuts rates by 25 basis points",
            "urgency": "critical",
            "received_at": datetime.now(timezone.utc).isoformat(),
        }
        with patch.object(briefing, "load_pending", return_value=[alert]), \
             patch.object(briefing, "load_state", return_value=briefing._empty_state()), \
             patch.object(briefing, "save_state") as save_state, \
             patch.object(briefing, "analyze_with_friday", return_value="NO_ALERT"), \
             patch.object(briefing, "send_telegram") as send:
            result = briefing.process_once()
        self.assertEqual(result["status"], "suppressed")
        send.assert_not_called()
        self.assertIn("x", save_state.call_args.args[0]["processed_ids"])

    def test_claim_batch_marks_alert_before_returning_prompt(self):
        alert = {
            "id": "claim-1", "source": "rss:Reuters",
            "headline": "Federal Reserve announces emergency rate cut",
            "urgency": "critical",
            "received_at": datetime.now(timezone.utc).isoformat(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with patch.object(briefing, "load_pending", return_value=[alert]), \
             patch.object(briefing, "load_state", return_value=briefing._empty_state()), \
             patch.object(briefing, "save_state") as save_state:
            prompt = briefing.claim_batch()
        self.assertIn("Federal Reserve", prompt)
        self.assertIn("claim-1", save_state.call_args.args[0]["processed_ids"])

    def test_peek_batch_is_read_only_and_signed(self):
        alert = {
            "id": "peek-1", "source": "rss:Reuters",
            "headline": "Federal Reserve announces emergency rate cut",
            "urgency": "critical",
            "received_at": datetime.now(timezone.utc).isoformat(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with patch.object(briefing, "load_pending", return_value=[alert]):
            batch = briefing.peek_batch()
        self.assertTrue(batch["signature"])
        self.assertIn("Federal Reserve", batch["prompt"])


if __name__ == "__main__":
    unittest.main()
