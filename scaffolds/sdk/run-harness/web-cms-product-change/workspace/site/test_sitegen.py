import json
import tempfile
import unittest
from pathlib import Path

import sitegen

CONTENT = Path(__file__).parent / "content" / "events.json"


class SitegenTest(unittest.TestCase):
    def test_listing_is_newest_first(self):
        dates = [event["date"] for event in sitegen.load_events(CONTENT)]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_every_event_page_shows_title_date_and_summary(self):
        with tempfile.TemporaryDirectory() as out:
            for event in sitegen.build(CONTENT, out):
                page = (Path(out) / f"{event['slug']}.html").read_text()
                for field in ("title", "date", "summary"):
                    self.assertIn(event[field], page)

    def test_listing_links_every_event(self):
        events = sitegen.load_events(CONTENT)
        index = sitegen.render_index(events)
        for event in events:
            self.assertIn(f'href="{event["slug"]}.html"', index)

    def test_missing_required_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.json"
            path.write_text(json.dumps([{"slug": "x", "title": "X", "date": "2026-01-01"}]))
            with self.assertRaisesRegex(ValueError, "missing summary"):
                sitegen.load_events(path)


if __name__ == "__main__":
    unittest.main()
