import unittest

from scripts.monitoring.realtime_news import parse_jin10_item


class Jin10NewsTests(unittest.TestCase):
    def test_important_flash_is_normalized(self):
        item = {
            "id": "202608080000001",
            "time": "2026-08-08 04:00:00",
            "important": 1,
            "type": 0,
            "extras": {"ad": False},
            "data": {"content": "【美联储】宣布降息25个基点", "source": "金十数据"},
        }
        alert = parse_jin10_item(item)
        self.assertIsNotNone(alert)
        self.assertEqual(alert["id"], "jin10:202608080000001")
        self.assertEqual(alert["source"], "jin10:flash")
        self.assertEqual(alert["urgency"], "critical")
        self.assertEqual(alert["timestamp"], "2026-08-07T20:00:00+00:00")

    def test_nonimportant_flash_is_skipped_by_default(self):
        item = {
            "id": "2",
            "time": "2026-08-08 04:00:00",
            "important": 0,
            "type": 0,
            "extras": {"ad": False},
            "data": {"content": "今日市场常规简报"},
        }
        self.assertIsNone(parse_jin10_item(item))

    def test_ad_is_always_skipped(self):
        item = {
            "id": "3",
            "time": "2026-08-08 04:00:00",
            "important": 1,
            "type": 0,
            "extras": {"ad": True},
            "data": {"content": "美联储宣布降息"},
        }
        self.assertIsNone(parse_jin10_item(item))


if __name__ == "__main__":
    unittest.main()
