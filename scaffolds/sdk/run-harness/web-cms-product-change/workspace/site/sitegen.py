import html
import json
import sys
from pathlib import Path

REQUIRED_FIELDS = ("slug", "title", "date", "summary")


def load_events(path):
    events = json.loads(Path(path).read_text())
    for event in events:
        missing = [field for field in REQUIRED_FIELDS if not event.get(field)]
        if missing:
            raise ValueError(f"event {event.get('slug', '?')} is missing {', '.join(missing)}")
    return sorted(events, key=lambda event: event["date"], reverse=True)


def render_index(events):
    items = "\n".join(
        f'<li><a href="{html.escape(event["slug"])}.html">{html.escape(event["title"])}</a> '
        f'<time>{html.escape(event["date"])}</time></li>'
        for event in events
    )
    return f"<!doctype html>\n<title>Events</title>\n<h1>Events</h1>\n<ul>\n{items}\n</ul>\n"


def render_event(event):
    return (
        f"<!doctype html>\n<title>{html.escape(event['title'])}</title>\n"
        f"<h1>{html.escape(event['title'])}</h1>\n"
        f"<time>{html.escape(event['date'])}</time>\n"
        f"<p>{html.escape(event['summary'])}</p>\n"
    )


def build(content_path, out_dir):
    events = load_events(content_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render_index(events))
    for event in events:
        (out / f"{event['slug']}.html").write_text(render_event(event))
    return events


if __name__ == "__main__":
    build(Path(__file__).parent / "content" / "events.json", sys.argv[1] if len(sys.argv) > 1 else "dist")
