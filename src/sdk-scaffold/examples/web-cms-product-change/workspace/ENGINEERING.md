# ENGINEERING.md — Coding Agent Standing Context

The product is a static site generator in `site/`:

- `site/content/events.json`: the event content, one object per event.
- `site/sitegen.py`: validates the content and renders `index.html` (the events listing, newest first) plus one page per event. Standard library only.
- `site/test_sitegen.py`: `unittest` tests for the generator.

Rules for every brief:

- Change only the files the brief puts in scope.
- Every acceptance criterion in the brief gets at least one test in `site/test_sitegen.py`.
- Run `python3 -m unittest discover -s site` before you finish, and report the result verbatim.
- Keep the generator dependency-free, and keep events that lack a new optional field rendering.
