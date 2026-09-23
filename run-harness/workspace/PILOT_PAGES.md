# PILOT_PAGES.md — the pilot scope

_Fixed scope: every URL below is in. You may migrate more; you may not drop any. 105 URLs._

Source hosts:

- **citp.psb-test** = `https://citp.psb-test.princeton.edu`
- **blogs-qa** = `https://blogs-qa.princeton.edu/blog-citp` (basic auth; see `AGENTS.md` § Environment)

Paths are relative to the source host. Blog paths were taken from the production blog's sitemap, so confirm at hour 0 that each one exists on blogs-qa. If a path is missing there, log it and use the production page as its source.

The **Type** column suggests which content type each page belongs to. Your content model decides the real mapping. Target URLs come from your URL map (`inventory/url_map.csv`), not from this file.

Deliberately excluded (drafts, duplicates or display-only pages): `/homepage`, `/homepage-copy`, `/homepage-version-b`, `/television`, `/citp-ftt-television-display`, `/blog` (a 2013 page on the main site).


## Main site — standalone pages (15)

| Path | Source | Type | Why it's here |
|---|---|---|---|
| `/` | citp.psb-test | Page | homepage |
| `/about` | citp.psb-test | Page | section landing |
| `/about/mission-statement` | citp.psb-test | Page | static text page |
| `/about/contact-us` | citp.psb-test | Page | contact details |
| `/about/subscribe` | citp.psb-test | Page | newsletter/listserv signup |
| `/about/newsletter` | citp.psb-test | Page | newsletter page |
| `/about/contact/unsubscribe-successful` | citp.psb-test | Page | subscription flow landing |
| `/about/hiring` | citp.psb-test | Page | static page |
| `/about/working-citp` | citp.psb-test | Page | page with child pages |
| `/about/graphic-identity` | citp.psb-test | Page | page with downloadable assets |
| `/our-work` | citp.psb-test | Page | section landing |
| `/our-work/privacy-security` | citp.psb-test | Page | research-area page |
| `/our-work/artificial-intelligence-data-science-society` | citp.psb-test | Page | research-area page |
| `/frequently-asked-questions` | citp.psb-test | Page | root-level page, accordion-style content |
| `/media` | citp.psb-test | Page | media page |

## Main site — list & landing pages (9)

| Path | Source | Type | Why it's here |
|---|---|---|---|
| `/events` | citp.psb-test | Listing | upcoming events list |
| `/events/past-events` | citp.psb-test | Listing | paginated archive |
| `/news` | citp.psb-test | Listing | news list |
| `/news/coverage-media` | citp.psb-test | Listing | filtered news list |
| `/people` | citp.psb-test | Listing | people landing |
| `/people/all-people` | citp.psb-test | Listing | full directory |
| `/people/fellows` | citp.psb-test | Listing | role-filtered directory |
| `/people/leadership-staff` | citp.psb-test | Listing | role-filtered directory |
| `/programs` | citp.psb-test | Listing | programs landing |

## Events (2008–2027) (14)

| Path | Source | Type | Why it's here |
|---|---|---|---|
| `/events/2008/computing-cloud` | citp.psb-test | Event | oldest era |
| `/events/2008/james-katz-how-are-mobile-phones-changing-families` | citp.psb-test | Event | oldest era |
| `/events/2014/diego-vicentin-mobile-broadband-reticulation-first-thoughts-and-research-notes` | citp.psb-test | Event |  |
| `/events/2014/steve-schultze-global-internet-freedom-where-do-we-stand` | citp.psb-test | Event |  |
| `/events/2019/citp-luncheon-speaker-series-eric-goldman-content-moderation-remedies` | citp.psb-test | Event | luncheon series |
| `/events/2019/citp-luncheon-speaker-series-timothy-b-lee-way-we-regulate-self-driving-cars-broken` | citp.psb-test | Event | luncheon series |
| `/events/2021/citp-seminar-arunesh-mathur-princeton-digital-ad-observatory` | citp.psb-test | Event | seminar |
| `/events/2021/citp-seminar-charlton-mcilwain-dreams-black-tech-futures-past` | citp.psb-test | Event | seminar |
| `/events/2022/citp-book-club-sorting-things-out` | citp.psb-test | Event | book club |
| `/events/2024/decentralized-social-media-workshop` | citp.psb-test | Event | workshop |
| `/events/2024/citp-seminar-molly-crockett-producing-more-while-knowing-less-epistemic-risks-ai` | citp.psb-test | Event | seminar |
| `/events/2025/citp-seminar-zeynep-tufekci-%E2%80%93-major-risks-artificial-good-enough-intelligence` | citp.psb-test | Event | percent-encoded slug |
| `/events/2026/marianne-aubin-le-qu%C3%A9r%C3%A9-balancing-epistemic-truth-wellbeing-age-generative-ai` | citp.psb-test | Event | non-ASCII slug |
| `/events/2027/andy-guess` | citp.psb-test | Event | upcoming event |

## News (2012–2026) (8)

| Path | Source | Type | Why it's here |
|---|---|---|---|
| `/news/2012/ed-felten-speaks-class-2016-about-tmi-information-identity-and-privacy` | citp.psb-test | News |  |
| `/news/2016/citp-postdoc-aylin-caliskan-islam-quoted-scientific-american-regarding-her-work-digital` | citp.psb-test | News |  |
| `/news/2020/student-projects-use-computing-ensure-technology-serves-society` | citp.psb-test | News |  |
| `/news/2023/digital-witness-lab-aids-news-probe-indian-journalists-murder` | citp.psb-test | News |  |
| `/news/2024/aleksandra-korolova-received-2024-early-career-sloan-research-fellowship` | citp.psb-test | News |  |
| `/news/2025/zeynep-tufekci-says-ai-presents-benefits-and-challenges-news-media` | citp.psb-test | News | media coverage |
| `/news/2025/citp-welcomes-new-assistant-professor-manoel-horta-ribeiro` | citp.psb-test | News |  |
| `/news/2026/mihir-kshirsagar-how-ftc-v-meta-reshapes-debate-social-media-and-first-amendment` | citp.psb-test | News |  |

## People (13)

| Path | Source | Type | Why it's here |
|---|---|---|---|
| `/people/affiliates` | citp.psb-test | Person | role list |
| `/people/emeritus` | citp.psb-test | Person | role list |
| `/people/graduate-students` | citp.psb-test | Person | role list |
| `/people/arvind-narayanan` | citp.psb-test | Person | faculty |
| `/people/sayash-kapoor` | citp.psb-test | Person |  |
| `/people/mihir-kshirsagar` | citp.psb-test | Person | staff |
| `/people/ed-felten` | citp.psb-test | Person |  |
| `/people/tithi-chattopadhyay` | citp.psb-test | Person | staff |
| `/people/brian-kernighan` | citp.psb-test | Person | faculty |
| `/people/zeynep-tufekci` | citp.psb-test | Person |  |
| `/people/varun-rao` | citp.psb-test | Person | student |
| `/people/andr%C3%A9s-monroy-hern%C3%A1ndez` | citp.psb-test | Person | non-ASCII slug; linked from blog posts |
| `/people/benedikt-str%C3%B6bl` | citp.psb-test | Person | non-ASCII slug |

## Programs (11)

| Path | Source | Type | Why it's here |
|---|---|---|---|
| `/programs/fellows-program` | citp.psb-test | Page |  |
| `/programs/citp-tech-policy-clinic` | citp.psb-test | Page | program with child pages |
| `/programs/citp-tech-policy-clinic/tech-policy-case-studies` | citp.psb-test | Page | child page |
| `/programs/reading-groups` | citp.psb-test | Page |  |
| `/programs/reading-groups/recommender-systems-reading-group` | citp.psb-test | Page | child page |
| `/programs/undergraduate-students` | citp.psb-test | Page |  |
| `/programs/undergraduate-students/ts-certificate-it-track/requirements` | citp.psb-test | Page | deep nesting |
| `/programs/siegel-public-interest-technology-summer-fellowship-pit-sf` | citp.psb-test | Page |  |
| `/programs/emerging-scholars-program` | citp.psb-test | Page |  |
| `/programs/graduate-students` | citp.psb-test | Page |  |
| `/pit-summer-fellowship-2026-student-bios` | citp.psb-test | Page | root-level program page |

## Publications & resource links (7)

| Path | Source | Type | Why it's here |
|---|---|---|---|
| `/our-work/publications-and-reports` | citp.psb-test | Publication | publications list |
| `/our-work/publications/working-papers` | citp.psb-test | Publication | list |
| `/our-work/publications/security-analysis-diebold-accuvote-ts-voting-machine` | citp.psb-test | Publication | publication with child pages |
| `/our-work/publications-and-reports/security-analysis-diebold-accuvote-ts-voting-machine/executive` | citp.psb-test | Publication | child under the inconsistent parent path |
| `/publications/ai-snake-oil-what-artificial-intelligence-can-do-what-it-can%E2%80%99t-and-how-tell-difference` | citp.psb-test | Publication | book; percent-encoded slug |
| `/resource-links/open-world-evaluations-measuring-frontier-ai-capabilities` | citp.psb-test | Publication | resource link |
| `/resource-links/leaderboard-illusion` | citp.psb-test | Publication | resource link |

## Policy posts & one-off pages (4)

| Path | Source | Type | Why it's here |
|---|---|---|---|
| `/posts/2026/companion-chatbots-102725` | citp.psb-test | Page | policy post |
| `/posts/2026/682026-information-authentication-deepfakes` | citp.psb-test | Page | policy post |
| `/state-ai-policy-forum` | citp.psb-test | Page | root-level event page |
| `/important-dates-2025-26` | citp.psb-test | Page | root-level page |

## Blog — archives (7)

| Path | Source | Type | Why it's here |
|---|---|---|---|
| `/` | blogs-qa | Listing | blog home |
| `/category/artificial-intelligence-data-science-society/` | blogs-qa | Listing | category archive |
| `/category/meet-the-researcher/` | blogs-qa | Listing | category archive |
| `/category/voting/` | blogs-qa | Listing | category archive |
| `/tag/election/` | blogs-qa | Listing | tag archive |
| `/author/feltencs-princeton-edu/` | blogs-qa | Listing | author archive |
| `/author/mihir-kshirsagar/` | blogs-qa | Listing | author archive |

## Blog — posts (2003–2026) (17)

| Path | Source | Type | Why it's here |
|---|---|---|---|
| `/2003/01/02/everything-not-possible/` | blogs-qa | Blog Post | oldest era |
| `/2003/01/07/dvd-jon-acquitted/` | blogs-qa | Blog Post | oldest era |
| `/2008/01/02/2007-predictions-scorecard/` | blogs-qa | Blog Post |  |
| `/2008/01/11/second-life-welcomes-bank-regulators/` | blogs-qa | Blog Post |  |
| `/2013/01/03/report-on-the-nsf-secure-and-trustworthy-cyberspace-pi-meeting/` | blogs-qa | Blog Post |  |
| `/2013/01/12/grieving-aaron-swartz/` | blogs-qa | Blog Post |  |
| `/2017/01/04/nyc-to-collect-gps-data-on-car-service-passengers-good-intentions-gone-awry-or-something-else/` | blogs-qa | Blog Post |  |
| `/2017/02/01/regulatory-questions-abound-as-mobile-payments-clamor-for-position-in-apps/` | blogs-qa | Blog Post |  |
| `/2020/01/07/the-unknown-history-of-digital-cash/` | blogs-qa | Blog Post |  |
| `/2020/03/06/ballot-level-comparison-audits-precinct-count/` | blogs-qa | Blog Post | voting |
| `/2023/02/16/unrecoverable-election-screwup-in-williamson-county-tx/` | blogs-qa | Blog Post | voting |
| `/2023/05/03/best-practices-for-sorting-mail-in-ballots/` | blogs-qa | Blog Post | voting |
| `/2025/02/26/fact-checking-or-community-notes-why-not-both-techtakes/` | blogs-qa | Blog Post |  |
| `/2025/03/06/meet-the-researcher-manoel-horta-ribeiro/` | blogs-qa | Blog Post | meet-the-researcher series |
| `/2026/01/16/internet-voting-is-insecure-and-should-not-be-used-in-public-elections/` | blogs-qa | Blog Post | recent |
| `/2026/03/04/introducing-the-new-citp-non-resident-technology-fellows-2026-cohort/` | blogs-qa | Blog Post | recent |
| `/2026/09/18/making-community-governance-legible-a-semester-with-bonfire/` | blogs-qa | Blog Post | newest |
