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
- [x] CLI: import-csv, show, list, search, top, stats, update,
  export, rescore
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
- [x] PDF parser tested and tuned with 898 real ZenDesk exports
- [x] Wrapped subject line handling for multi-line PDF subjects

## Phase 3: Intelligence (Complete)

- [x] Content-based scoring engine (0-45) with keyword analysis
- [x] Score breakdown display on ticket detail
- [x] Manual score override
- [x] Category auto-suggestion based on ticket content keywords
- [x] STAR format generator for interview prep
- [x] Skill coverage gap analysis
- [x] Scoring keywords tuned for Redis/ZenDesk support content
  (RCA, CRDB, cluster ops, failover, TLS, etc.)
- [x] Score recalibration with 889 fully enriched tickets

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

## Phase 5: Export & Integration (Complete)

- [x] JSON export API
- [x] Markdown export API
- [x] CSV export API
- [x] CLI export command
- [x] Bulk enrichment API (`/api/enrich/bulk`)
- [x] Unenriched tickets endpoint (`/api/tickets/unenriched`)
- [x] CLI `next-unenriched` command for sequential enrichment
- [x] Print-friendly interview prep view (print CSS)

## Phase 6: MCP Server & Automation (Complete)

Built a full MCP server (`mcp_server/`) with Playwright browser
automation and 20+ tools for Claude Code integration:

- [x] Persistent Chromium browser context (SSO/MFA login)
- [x] Automated PDF scraping from authenticated Zendesk sessions
- [x] Batch processing (20+ tickets per batch, configurable delay)
- [x] Ticket discovery from Zendesk search results and saved views
- [x] Two-phase decoupled architecture (scrape PDFs, then parse +
  AI analysis)
- [x] AI-generated STAR analysis via `save_ticket_analysis`
- [x] Natural language ticket query via `query_tickets`
- [x] All 898 tickets scraped and enriched (889 fully enriched)

## Phase 7: RAG Chat (Complete)

- [x] Natural language query interface (`/chat` page)
- [x] Smart query parser (categories, severity, product filters,
  time ranges, keywords)
- [x] FTS5 retrieval with structured filter layering
- [x] Ollama integration with streaming SSE responses
- [x] Multi-turn conversation with follow-up detection
- [x] Retrieve-only mode (no LLM required)
- [x] Markdown rendering for LLM responses
- [x] Model auto-detection and selector

## Phase 8: Data Quality & Insights (Complete)

- [x] Production outage detection from conversation content
  (36 phrases, 147/898 flagged)
- [x] Related ticket auto-detection from PDF cross-references
- [x] Bot message filtering (Redis Support Bot, Analyzer Bot,
  PagerDuty)
- [x] Zendesk metadata extraction (JIRA IDs, subscription IDs,
  BDB IDs, endpoints, additional products)

## Phase 9: Polish (Partial)

- [x] Loading spinner CSS animation
- [x] Print styles (hide nav, buttons, forms)
- [x] Database backup utility (API + stats page button)
- [x] FTS rebuild utility (API + stats page button)
- [x] Manage page (rescore, reset, backup, danger zone)
- [ ] Mobile-responsive layout improvements
- [ ] Keyboard shortcuts for navigation
- [ ] Undo support for edits
- [ ] Unit tests for scoring, CSV import, PDF parser

## Future Ideas

- [ ] RAG chat: search conversation notes (TicketNote table) in
  addition to STAR fields — enables queries like "which tickets are
  for eCommerce companies?" by searching customer signatures, email
  domains, DB names, and full conversation content
- [ ] ZenDesk API integration (if API access becomes available)
- [ ] CSAT/survey feedback import (not available in print PDFs)
- [ ] Ticket relationship graph visualization
- [ ] Auto-detect escalation patterns from cross-ticket references
- [ ] Cloud deployment (Docker, fly.io, etc.)
