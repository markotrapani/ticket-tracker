# Ticket Tracker - Roadmap

## Phase 1: Foundation (Complete)

- [x] Project scaffolding (Flask, SQLAlchemy, SQLite, Click)
- [x] Database schema (tickets, tags, notes, score_history)
- [x] CSV importer for ZenDesk exports (898 tickets loaded)
- [x] Metadata-based auto-scoring engine (0-55)
- [x] Web UI: dashboard with stats overview
- [x] Web UI: searchable/filterable ticket list with pagination
- [x] Web UI: ticket detail view
- [x] Web UI: import page (CSV, PDF, manual, paste tabs)
- [x] Web UI: statistics page with Chart.js charts
- [x] Web UI: interview prep page
- [x] CLI: import-csv, show, list, search, top, stats, update, export, rescore
- [x] Bootstrap 5 dark theme
- [x] CLAUDE.md project documentation

## Phase 2: Enrichment (Complete)

- [x] Inline editing on ticket detail (auto-save via AJAX)
- [x] Notes system (add/delete per ticket)
- [x] Tags system (add/delete per ticket)
- [x] Star/bookmark toggle
- [x] PDF parser for ZenDesk print exports
- [x] PDF upload from ticket detail view
- [x] Paste content import
- [x] Manual ticket creation
- [x] Batch PDF import (upload multiple PDFs at once)
- [x] Auto-detect ticket ID from ZenDesk URL input
- [ ] Test PDF parser with real ZenDesk exports and tune extraction patterns

## Phase 3: Intelligence (Complete)

- [x] Content-based scoring engine (0-45) with keyword analysis
- [x] Score breakdown display on ticket detail
- [x] Manual score override
- [x] Category auto-suggestion based on ticket content keywords
- [x] STAR format generator (Situation, Task, Action, Result) for interview prep
- [x] Skill coverage gap analysis ("no high-score tickets for category X")
- [ ] Improve scoring keywords based on real ticket content
- [ ] Score recalibration after enriching a meaningful sample of tickets

## Phase 4: Search & Analytics (Complete)

- [x] Basic LIKE search across all text fields
- [x] SQLite FTS5 full-text search with LIKE fallback
- [x] Score distribution chart
- [x] Monthly volume chart
- [x] Resolution time distribution chart
- [x] Category breakdown chart
- [x] Year-over-year comparison charts (line chart by year)
- [x] Resolution time trends over time (quarterly averages)
- [x] "Best tickets to mention" auto-generated diverse list

## Phase 5: Export & Integration

- [x] JSON export API
- [x] Markdown export API
- [x] CSV export API
- [x] CLI export command
- [x] Bulk enrichment API (`/api/enrich/bulk`) for external automation
- [x] Unenriched tickets endpoint (`/api/tickets/unenriched`) for batch workflows
- [x] CLI `next-unenriched` command for sequential enrichment
- [x] Print-friendly interview prep view (print CSS)
- [ ] Bulk enrichment via Claude native app + MCP browser integration (see below)
- [ ] ZenDesk API integration (if API access becomes available)

### MCP Browser Integration (Planned)

The highest-impact enrichment path is using the Claude native app with an MCP browser
server (Puppeteer/Playwright) to scrape ticket content from an authenticated ZenDesk session.

**How it works:**

1. User opens Claude native app with Browser MCP server configured
2. Claude navigates to `https://redislabs.zendesk.com/agent/tickets/{id}` using the user's session
3. Claude extracts: subject, description, comments, tags, priority, customer name
4. Claude POSTs the extracted data to this app's `/api/enrich/bulk` endpoint
5. The app auto-scores the enriched ticket and updates enrichment_level

**Integration surface (already built):**

- `GET /api/tickets/unenriched` - returns ticket IDs that still need content (metadata_only)
- `POST /api/enrich/bulk` - accepts array of `{zendesk_id, subject, description, ...}` objects
- Both endpoints are designed for automation: no auth required (local-only app)

**Prompt template for Claude native app:**

```text
I have a ticket tracker running at http://localhost:5050.
First, GET http://localhost:5050/api/tickets/unenriched to get the list of tickets needing enrichment.
For each ticket, navigate to https://redislabs.zendesk.com/agent/tickets/{zendesk_id} in my browser.
Extract the subject, full description/conversation, any resolution notes, customer name, and tags.
Then POST the data to http://localhost:5050/api/enrich/bulk with the extracted content.
Process tickets in batches of 10.
```

## Phase 6: Polish

- [x] Loading spinner CSS animation
- [x] Print styles (hide nav, buttons, forms)
- [x] Database backup utility (API + stats page button)
- [x] FTS rebuild utility (API + stats page button)
- [ ] Mobile-responsive layout improvements
- [ ] Keyboard shortcuts for navigation
- [ ] Undo support for edits
- [ ] Unit tests for scoring, CSV import, PDF parser

## Phase 7: Data Enrichment & Insights

- [x] Related ticket auto-detection from PDF conversation cross-references
- [ ] CSAT/survey feedback import (from Zendesk Explore CSV export - not available in print PDFs)
- [ ] Ticket relationship graph visualization
- [ ] Auto-detect escalation patterns from cross-ticket references

## Needs Real Data

These items require actual ticket content to complete:

- [ ] Test PDF parser with real ZenDesk exports and tune extraction patterns
- [ ] Improve scoring keywords based on real ticket content
- [ ] Score recalibration after enriching a meaningful sample of tickets
