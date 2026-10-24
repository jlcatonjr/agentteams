# UI/UX Review Rubric — Documentation Site (MkDocs Material)

**Status:** tracked reference · **Compiled:** 2026-10-23 · **Used by:** the `@ux-reviewer` agent and any
review of changes to `docs_src/`, `mkdocs.yml` or the site's landing pages. It was first applied to the
2026-10 docs-site restructure (see `CHANGELOG.md`, "site restructure" entries).

**Why this file exists.** When it was written, no agent covered usability: the template library's
`visual-designer` archetype covers figures only. This rubric is the researched knowledge base for that
role.

**Provenance and limits.** It was compiled by web research on 2026-10-23. Every inline URL was fetched or
seen in search results at compile time. Items marked **unsourced** are practitioner rules of thumb
with no authoritative source found. Items marked *(search summary)* were seen only as search-result
abstracts, not read in full. Before citing any item in published docs, re-verify it and add it to the
reference DB (Constitutional Rule 5).

How to apply it: every criterion has an ID and is phrased as a pass/fail check. §1–§6 are general.
§7 binds them to this repo's integrity constraints, and §7 **wins on conflict**: no UX criterion
justifies breaking a mechanical guard. §8 sketches the agent.

---

## 1. Information architecture

**Findings**
- **Diátaxis.** Content falls on two axes: action vs cognition, and acquisition vs application. The four
  modes are tutorial, how-to, reference and explanation. Adjacent modes blur into each other, and the
  worst case is a tutorial collapsing into a how-to. Without a clear architecture, writers default to
  organising by product feature, which "rarely works". Reference should mirror the structure of the
  product. Don't build an empty four-box skeleton; grow the structure out of real content, in small
  increments. Sources: <https://diataxis.fr/compass/>, <https://diataxis.fr/map/>,
  <https://diataxis.fr/tutorials/>, <https://diataxis.fr/reference/>,
  <https://diataxis.fr/how-to-use-diataxis/>
- **Progressive disclosure.** More than two disclosure levels typically has low usability. Group advanced
  material into logical chunks, chosen by card sorting. <https://www.nngroup.com/articles/progressive-disclosure/>
- **Dev-tool landing pages** (a study of 100+ pages). The pattern that works: a hero with a bold headline
  and a code visual; two CTAs, one primary with specific wording plus a lighter secondary; trust signals
  right after the hero; features framed as problems solved; one final CTA.
  <https://evilmartians.com/chronicles/we-studied-100-devtool-landing-pages-here-is-what-actually-works-in-2025>
- **Information scent.** A landing page should tell visitors early whether they are in the right place.
  <https://www.nngroup.com/articles/information-scent/>
- **Top-level tab count.** No authoritative number was found; 5–7 is a rule of thumb (**unsourced**).

**Criteria**
- **IA-1** Every page is classifiable as exactly one Diátaxis mode. In a sample, no page's main body
  mixes step-by-step instructions with more than one paragraph of conceptual discussion.
- **IA-2** Tutorials offer no alternative paths, and every step produces a visible result.
- **IA-3** Reference pages contain no procedures or rationale; they link out for those.
- **IA-4** The reference nav mirrors the code's module structure, so a reader can predict a page's
  location from the import path.
- **IA-5** There are at most 7 top-level tabs (**unsourced** threshold), and each label is a noun the
  audience understands.
- **IA-6** Every tutorial or how-to is at most 2 disclosure levels from Home. Reference may go deeper,
  but only through section index pages.
- **IA-7** Home follows problem → solution (with a code snippet) → proof → a primary CTA plus a
  secondary CTA.
- **IA-8** The first two sentences of Home say what the project is and who it is for.
- **IA-9** Home routes visitors by audience (user, integrator, contributor, researcher), with one
  labelled entry point each (**unsourced** pattern).
- **IA-10** No empty or placeholder sections; every nav section has substantive content.

## 2. Usability and content

**Findings**
- **F-pattern scanning.** Put the key points in the first two paragraphs, front-load headings with
  information-carrying words, use bullets, and write descriptive link text.
  <https://www.nngroup.com/articles/f-shaped-pattern-reading-web-content/>
- **Inverted pyramid.** Lead with the conclusion and start each paragraph with its point.
  <https://www.nngroup.com/articles/inverted-pyramid/>
- **Labels.** Make them specific and jargon-free, and never "Learn more" or "More".
  <https://www.nngroup.com/articles/information-scent/>
- **Breadcrumbs.** They show hierarchy, not history. The current page is not a link, and every crumb is
  a real page. <https://www.nngroup.com/articles/breadcrumbs/>
- **Mega-menus and long sidebars.** Use medium-sized groups, list each option once, and keep search
  visible. <https://www.nngroup.com/articles/mega-menus-work-well/>. Vertical navigation suits broad,
  growing information architectures *(search summary)*: <https://www.nngroup.com/videos/vertical-navigation/>
- **Headings** (Google developer style). Sentence case; one h1; no skipped levels; task headings start
  with a bare infinitive; concept headings are noun phrases; no links in headings.
  <https://developers.google.com/style/headings>
- **Link text** (Google developer style). It must make sense on its own. No "click here" and no bare
  URLs. Two links on one page with the same text must not go to different destinations.
  <https://developers.google.com/style/link-text>

**Criteria**
- **UX-1** The first paragraph of every page says what the page covers and who it is for.
- **UX-2** Headings use sentence case, bare infinitives on task pages, and noun phrases on concept pages.
- **UX-3** Exactly one h1 per page and no skipped heading levels (automatable).
- **UX-4** No link text that is "click here", "here", "this page", "learn more" or a bare URL
  (automatable grep).
- **UX-5** Nav and card labels are front-loaded, and no two sibling labels start with the same word.
- **UX-6** Breadcrumbs reflect hierarchy and every crumb resolves to a real page.
- **UX-7** Search is on every page, and the 10 most common API symbols each return their reference page
  in the top 3 results.
- **UX-8** How-to and explanation paragraphs run to 4 sentences or fewer (**unsourced** heuristic).
  Any series of 3 or more items is marked up as a list.
- **UX-9** Card summaries add information beyond the title.

## 3. Accessibility (WCAG 2.2 AA)

**Findings.** The success criteria most relevant to a static docs site are: 1.1.1, 1.3.1, 1.4.3
(4.5:1 contrast), 1.4.10 (reflow at 320 CSS px), 1.4.11, 1.4.12, 1.4.13, 2.4.1 (bypass blocks), 2.4.2,
2.4.4, 2.4.5, 2.4.6, 2.4.7, **2.4.11 Focus Not Obscured** (new in 2.2), **2.5.8 Target Size 24×24 px**
(new in 2.2), 3.1.1 and 3.2.3. <https://www.w3.org/WAI/WCAG22/quickref/?versions=2.2&levels=aa>

- **Material skip link.** Material renders its "Skip to content" link only when the page has a
  table-of-contents entry (`{% if page.toc | first is defined %}`).
  <https://raw.githubusercontent.com/squidfunk/mkdocs-material/master/src/templates/base.html>.
  A home page with no headings has no skip link. Hiding the TOC sidebar with front matter does not
  remove `page.toc`, so a home page that keeps real headings keeps the link.
- **Sticky tabs.** `navigation.tabs.sticky` is the usual way a page fails 2.4.11.
- **Material contrast.** There has been contrast-issue history, and teams commonly patch it with custom
  CSS *(search summary)*: <https://docs.jetstream-cloud.org/faq/accessibility/>

**Criteria**
- **A11Y-1** Text contrast is at least 4.5:1 and UI/graphics contrast at least 3:1, in light and dark
  palettes, including header, footer, admonitions and code.
- **A11Y-2** Every page, Home included, has a working skip link as its first focusable element.
- **A11Y-3** Every figure has alt text that states its conclusion, and complex diagrams have a text
  equivalent nearby.
- **A11Y-4** At 320 px there is no horizontal page scroll; wide tables and code scroll inside their own
  containers.
- **A11Y-5** Focus is visible on cards, tabs and search, and is never fully hidden behind sticky chrome.
- **A11Y-6** Cards, buttons and nav targets are at least 24×24 CSS px.
- **A11Y-7** No skipped heading levels, and headings are never used just for visual size in card grids.
- **A11Y-8** `<html lang>` is correct, and each page has a unique, descriptive `<title>`.
- **A11Y-9** An automated pass (axe, Pa11y or Lighthouse) covers Home, one page per Diátaxis mode and
  3 reference pages, and is confirmed with a keyboard-only manual check.

## 4. Contributor onboarding

**Findings**
- **Steinmacher et al.: newcomer barriers.** Their model has 58 barriers in six categories: cultural
  differences, newcomer characteristics, reception issues, orientation, technical hurdles and
  documentation problems. A portal built on the model (FLOSScoach) lowered orientation and contribution
  barriers, but **not technical ones**.
  <https://dl.eusset.eu/items/d1e81529-914b-4140-b6f0-ee5fe7197644/full> (CSCW 2019, DOI
  10.1007/s10606-018-9335-z). The ICSE 2016 details are from a *(search summary)*.
- **Tan, Zhou & Sun: good first issues.** Nearly half of good first issues are not solved by newcomers.
  Good ones need informative descriptions, support, and limited scope.
  <https://2020.esec-fse.org/details/fse-2020-papers/172/A-First-Look-at-Good-First-Issues-on-GitHub>
- **Elazhary et al.: what CONTRIBUTING files cover.** Across 53 projects: submitting PRs 73.6%, style
  72.2%, issues 69.4%, branching 56.9%, tests 52.8%, local setup 48.6%, CI 30.6%. About 68% of
  projects diverge significantly from their written guidelines. The authors recommend documenting
  tacit workflow knowledge, such as what CI checks, over anything a linter can enforce.
  <https://ar5iv.arxiv.org/html/1908.02320>
- **GitHub Open Source Guides.** Newcomers judge a project by its license, README, CONTRIBUTING, code
  of conduct, responsiveness and tone. Guides should list non-code contributions and acknowledge PRs
  early. They also cite Mozilla's finding that a review within 48 hours raises return rates; the primary
  Mozilla data was **not located**. <https://opensource.guide/how-to-contribute/>,
  <https://opensource.guide/building-community/>
- **Where GitHub shows CONTRIBUTING.** GitHub surfaces a CONTRIBUTING file kept in the repo root,
  `docs/` or `.github/` on issue and PR creation and at `/contribute`.
  <https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/setting-guidelines-for-repository-contributors>
- **CHAOSS metrics.** Newcomer Experience and Time to First Response.
  <https://chaoss.community/?p=4891>, <https://chaoss.community/kb/metric-time-to-first-response/>
  *(search summary)*
- **Codes of conduct.** Tourani, Adams & Serebrenik (SANER 2017) is a bibliographic record only
  (<https://publications.polymtl.ca/37163/>). Any claim about a code of conduct's effect on
  contribution is **unsourced**.

**Criteria**
- **CON-1** Contribute is reachable in one click from every page, and Home has a CTA to it.
- **CON-2** Ways to contribute are ranked by effort, with non-code paths first.
- **CON-3** The first-PR walkthrough is a true tutorial: one path, verified end to end, with exact
  commands and the expected output.
- **CON-4** Dev setup is re-verified on a fresh environment every release, since technical barriers
  are the ones a portal does not fix.
- **CON-5** A first-response expectation is stated explicitly, with an escalation path.
- **CON-6** There is a live link to a filtered `good first issue` list, and every issue on it has a
  description, scope and support note.
- **CON-7** Welcome signals are visible: a named contact, a public Q&A channel, and a code of conduct
  with a reporting path.
- **CON-8** Tacit workflow is documented: what CI checks, how to read a failure, branch and PR
  conventions, issue linking, and a single command for style.
- **CON-9** The site's contribute docs and the repo's CONTRIBUTING have a single source, or a check
  that they match.
- **CON-10** At least one metric, time to first response or newcomer return, is tracked.

## 5. Material for MkDocs

**Findings** (from <https://squidfunk.github.io/mkdocs-material/setup/setting-up-navigation/> unless
noted; all of these are built in, no plugin required)
- **Tab and section features.** `navigation.tabs`; `navigation.tabs.sticky` (watch A11Y-5);
  `navigation.sections`; `navigation.indexes` (incompatible with `toc.integrate`);
  `navigation.path` (breadcrumbs; experimental); `navigation.prune` (cuts HTML by "33% or more";
  incompatible with `navigation.expand`).
- **Footer.** `navigation.footer`:
  <https://squidfunk.github.io/mkdocs-material/setup/setting-up-the-footer/>
- **Edit and view buttons.** `content.action.edit` and `content.action.view`. They need `repo_url`
  and `edit_uri`. <https://squidfunk.github.io/mkdocs-material/setup/adding-a-git-repository/>
- **Card grids.** `<div class="grid cards" markdown>`, which needs the `attr_list` and `md_in_html`
  extensions. <https://squidfunk.github.io/mkdocs-material/reference/grids/>
- **Page front matter and overrides.** Front matter `hide: [navigation, toc]` hides the sidebars. A
  custom home template uses `theme.custom_dir` plus a `template:` key.
  <https://squidfunk.github.io/mkdocs-material/customization/>
- **Search.** Built in. It must be re-listed if you declare `plugins:`. Front matter supports
  `search: boost` and `search: exclude`.
  <https://squidfunk.github.io/mkdocs-material/setup/setting-up-site-search/>
- **Redirects.** These need the **external** `mkdocs-redirects` plugin, which writes meta-refresh
  stubs that keep `#fragment`. <https://github.com/mkdocs/mkdocs-redirects>

**Verified locally (2026-10-23, mkdocs 1.6.1 / Material 9.7.6)**
- With `navigation.indexes`, a *titled* first child (`- Overview: why/index.md`) still attaches as the
  section index. So the `Title: path.md` nav form the tests require is compatible with it.
- `README.md` is also treated as an index. Existing handbook sections whose first child is a
  `README.md` will gain clickable section headers, and their "Overview"/"Contents" entries will
  disappear.
- Material 9.7.6 prints a "MkDocs 2.0" warning banner. It does not fail `--strict`. Keep the versions
  pinned.

**Criteria**
- **MAT-1** At most 7 tabs, ordered by the user journey.
- **MAT-2** The long reference section uses collapsed groups plus `navigation.prune`, and does not
  use `navigation.expand`.
- **MAT-3** `navigation.indexes` is on, and every section has an overview page, so breadcrumbs
  resolve. No `toc.integrate`.
- **MAT-4** `navigation.footer` is on.
- **MAT-5** Home is either a custom template that keeps its skip link and h1, *or* a Markdown page
  with `hide:` front matter and real headings. The latter is preferred here (§7).
- **MAT-6** `attr_list` and `md_in_html` are enabled before any card grid ships.
- **MAT-7** `content.action.edit` is on.
- **MAT-8** `site_url` is set.
- **MAT-9** If `plugins:` is declared, `search` is listed. Overview pages use `search: boost`.
- **MAT-10** The build is strict in CI.

## 6. URL stability

**Findings**
- **Cool URIs don't change.** Keep classification and status words out of paths.
  <https://www.w3.org/Provider/Style/URI>
- **GitHub Pages has no server-side 301.** Redirects on Pages are meta-refresh stubs *(search
  summary)*.

**Criteria**
- **URL-1** Every published URL from before the restructure is captured as a baseline.
- **URL-2** After the restructure, 100% of baseline URLs still resolve or have a redirect
  (automatable).
- **URL-3** Redirects point to the nearest equivalent page and keep anchors.
- **URL-4** New slugs carry no status words and no volatile category names.
- **URL-5** Reference URLs derive from module names.
- **URL-6** Links are checked on a schedule.
- **URL-7** The restructure is recorded in the CHANGELOG, and any redirects are permanent.

---

## 7. Binding to this repository (overrides §1–§6 on conflict)

| Guard (evidence) | Constraint | Rubric items affected |
|---|---|---|
| `tests/test_docs_nav_completeness.py:43` regex `:\s*([A-Za-z0-9_\-/]+\.md)\s*$` | Every nav entry is `Title: path.md`, unquoted, on one line. No bare entries. | MAT-3 (OK: titled index form verified) |
| same file, l.149–155 | `interoperability.md` stays a **top-level** nav entry at 2-space indent | IA-5 / MAT-1: it uses one of the 7 tabs |
| same file, l.112 | Every `api-reference/` nav page is linked from `api-reference/index.md` | IA-4 |
| `scripts/check_api_doc_parity.py:55`, `tests/test_module_doc_ratchet.py:39`, `tests/test_api_doc_signatures.py:35` | `docs_src/api-reference/` stays flat. Every non-index page there maps to a module. No new overview pages in that directory. | MAT-3: API groups cannot get their own `index.md` overview pages there, so they stay collapsed groups without overviews. The API `index.md` is the overview. |
| `agentteams/cli/artifacts.py:933`; `tests/test_memory_index_relevance.py:124` | Only top-level `docs_src/*.md` is memory-indexed, and the relevance tests score that corpus | New pages go in subdirectories (`why/`, `contribute/`), so they don't shift the BM25 tests |
| README / man page / `pyproject.toml` absolute URLs (13 paths) | No existing page moves | URL-2 is met by moving no files; no redirects plugin needed |
| `scripts/docs_freshness_watch.py` | A `custom_dir` override would count as "source" | MAT-5: prefer a Markdown home page over a template override |
| `mkdocs.yml:12` `#copyright-year` + `javascripts/current-year.js` | Any footer override must keep the span | MAT-5 |

## 8. The `@ux-reviewer` agent

- **Tier:** domain, read-only auditor (terse-mode roles).
- **Tools:** `Read, Grep, Glob, Bash(mkdocs build:*)`. Widening the toolset later needs `@security` (C-3).
- **Inputs:** a plan or diff touching `docs_src/`, `mkdocs.yml` or the home page.
- **Procedure:**
  1. Classify each changed page's Diátaxis mode.
  2. Run the automatable checks: UX-3, UX-4, A11Y-7 and MAT-10.
  3. Review the remaining IDs by judgement.
  4. Check §7 first, since it overrides everything else.
- **Output:** a findings table of ID | page | pass/fail | evidence. No rewrites.
- **Hand-offs:**
  - content fixes go to `@primary-producer`;
  - flow problems inside a section go to `@cohesion-repairer`;
  - claims of fact go to `@technical-validator`.
- **Remediation-log candidate (Rule 11b).** The template library has no UX/usability archetype for
  projects whose deliverables include a web UI or docs site. `visual-designer` covers figures only.
