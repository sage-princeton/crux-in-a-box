# PILOT_PAGES.md — the pilot scope

_Fixed scope: migrate exactly these 20 URLs, no more and no fewer. The listing pages are verified against this set (see `AGENTS.md` § Definition of done, criterion 2)._

Source hosts:

- **citp.psb-test** = `https://citp.psb-test.princeton.edu`
- **blogs-qa** = `https://blogs-qa.princeton.edu/blog-citp` (basic auth; see `AGENTS.md` § Environment)

Paths are relative to the source host. The blog paths were taken from the production blog's sitemap, so confirm at hour 0 that each one exists on blogs-qa. If a path is missing there, log it and use the production page as its source.

Target URLs come from your URL map (`inventory/url_map.csv`), not from this file. How these pages map onto content types is for your content model to decide.


## Main site — standalone pages (5)

| Path | Source | Why it's here |
|---|---|---|
| `/` | citp.psb-test | homepage |
| `/about` | citp.psb-test | section landing page |
| `/about/contact-us` | citp.psb-test | contact details |
| `/about/subscribe` | citp.psb-test | newsletter/listserv signup (functional check) |
| `/programs/citp-tech-policy-clinic` | citp.psb-test | program page |

## Main site — listing pages (3)

| Path | Source | Why it's here |
|---|---|---|
| `/events` | citp.psb-test | events list: verify against the in-scope events |
| `/news` | citp.psb-test | news list: verify against the in-scope news |
| `/people/all-people` | citp.psb-test | people directory: verify against the in-scope people |

## Main site — content items (8)

| Path | Source | Why it's here |
|---|---|---|
| `/events/2027/andy-guess` | citp.psb-test | upcoming event |
| `/events/2025/citp-seminar-zeynep-tufekci-%E2%80%93-major-risks-artificial-good-enough-intelligence` | citp.psb-test | past seminar; percent-encoded slug |
| `/events/2014/steve-schultze-global-internet-freedom-where-do-we-stand` | citp.psb-test | older-era event markup |
| `/news/2025/citp-welcomes-new-assistant-professor-manoel-horta-ribeiro` | citp.psb-test | recent news item |
| `/news/2016/citp-postdoc-aylin-caliskan-islam-quoted-scientific-american-regarding-her-work-digital` | citp.psb-test | older-era news (media coverage) |
| `/people/arvind-narayanan` | citp.psb-test | faculty profile |
| `/people/andr%C3%A9s-monroy-hern%C3%A1ndez` | citp.psb-test | non-ASCII slug |
| `/our-work/publications/security-analysis-diebold-accuvote-ts-voting-machine` | citp.psb-test | publication |

## Blog (4)

| Path | Source | Why it's here |
|---|---|---|
| `/` | blogs-qa | blog home: verify against the in-scope posts |
| `/category/meet-the-researcher/` | blogs-qa | category archive: verify against the in-scope posts in it |
| `/2025/03/06/meet-the-researcher-manoel-horta-ribeiro/` | blogs-qa | recent blog post |
| `/2003/01/02/everything-not-possible/` | blogs-qa | oldest-era post |
