# Ticket Tracker

> A personal toolkit for cataloging ZenDesk support tickets, enriching them with technical details, and scoring their significance for interview preparation.

Built for a support engineer who needs to quickly surface and articulate their most impactful work from hundreds of resolved tickets.

---

## Table of Contents

- [Core Features](#core-features)
- [Tech Stack](#tech-stack)
- [Quick Start](#quick-start)
- [Web Interface](#web-interface)
- [Significance Scoring](#significance-scoring-0-100)
- [Interview Preparation](#interview-preparation)
- [RAG Chat](#rag-chat)
- [CLI Commands](#cli-commands)
- [API Reference](#api-reference)
- [MCP Server Integration](#mcp-server-integration)
- [Data Flow](#data-flow)
- [Project Structure](#project-structure)
- [Production Deployment](#production-deployment)
- [Roadmap](#roadmap)

---

## Core Features

### Ticket Management

- **CSV Import** - Bulk import from ZenDesk semicolon-delimited CSV exports
- **PDF Enrichment** - Parse ZenDesk print-view PDFs to extract full conversation threads, metadata, and resolution details
- **Batch PDF Upload** - Import multiple PDFs at once with auto-detected ticket IDs
- **Manual Entry** - Quick-add modal accessible from any page, paste content, or create tickets manually
- **Inline Editing** - Edit any ticket field directly from the detail view with auto-save
- **Notes & Tags** - Add general notes, conversation notes, and tags to organize tickets
- **Related Tickets** - Auto-detect cross-referenced ticket IDs from conversation threads with surrounding context
- **Full-Text Search** - SQLite FTS5 index across all text fields with LIKE fallback

### Automated Enrichment (MCP Server)

- **Browser Automation** - Playwright-based Zendesk navigation with persistent session (SSO/MFA support)
- **PDF Scraping** - Automated print-view PDF generation from authenticated Zendesk pages
- **Batch Processing** - Scrape 20+ tickets per batch with configurable delays
- **Ticket Discovery** - Find new tickets from Zendesk search results or saved views
- **AI Analysis** - Claude reads ticket conversations and generates STAR-format analysis via MCP tools

### RAG-Powered Chat

- **Natural Language Queries** - Ask questions about your tickets in plain English
- **Smart Retrieval** - Parses queries to extract categories, severity, product filters, and keywords
- **Ollama Integration** - Stream responses from a local LLM with full ticket context
- **Multi-Turn Conversations** - Follow-up detection reuses context across turns
- **Retrieve-Only Mode** - Browse matching tickets without needing an LLM

### Analytics & Visualization

- Score distribution histogram
- Monthly ticket volume trends
- Resolution time distribution and quarterly trends
- Category breakdown with average scores
- Year-over-year comparison charts

---

## Tech Stack

| Layer                  | Technology                                                 |
| ---------------------- | ---------------------------------------------------------- |
| **Backend**            | Python 3.10+, Flask 3.1, SQLAlchemy 2.0, SQLite            |
| **Frontend**           | Jinja2, Bootstrap 5 (dark theme), vanilla JS, Chart.js     |
| **CLI**                | Click 8.1                                                  |
| **PDF Parsing**        | pdfplumber                                                 |
| **Browser Automation** | Playwright (MCP server)                                    |
| **Local LLM**          | Ollama (optional, for RAG chat)                            |
| **Production**         | Gunicorn                                                   |

---

## Quick Start

```bash
# Clone
git clone https://github.com/markotrapani/ticket-tracker.git
cd ticket-tracker

# Setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Import tickets from CSV
python backend/cli.py import-csv sample_data/your_export.csv

# Start web UI
python run.py
# Visit http://localhost:5050
```

---

## Web Interface

The app provides **9 pages** with a consistent dark theme:

| Page               | Route              | Description                                                                        |
| ------------------ | ------------------ | ---------------------------------------------------------------------------------- |
| **Dashboard**      | `/`                | Overview stats, top tickets, recent activity                                       |
| **Tickets**        | `/tickets`         | Paginated list with live filters, search, and sorting                              |
| **Ticket Detail**  | `/tickets/<id>`    | Full view with inline editing, score breakdown, conversation thread, notes, tags   |
| **Import**         | `/import`          | 4-tab interface: CSV upload, PDF upload, paste content, quick add                  |
| **Statistics**     | `/stats`           | 6 Chart.js visualizations, database tools, stats playground                        |
| **Interview Prep** | `/interview-prep`  | Starred tickets, top-30 by score, print-friendly layout                            |
| **Chat**           | `/chat`            | RAG-powered natural language ticket querying                                       |
| **Manage**         | `/manage`          | Bulk operations: rescore, FTS rebuild, reset enrichment, backups                   |

**Ticket List Features:**

- Filter by status, category, enrichment level, starred, minimum score
- Sort by score, date, or resolution time
- Color-coded score badges (gray < 20 < yellow < 40 < orange < 60 < red < 80 < magenta)
- Click any row to open the detail view

---

## Significance Scoring (0-100)

A composite score from up to three sources that surfaces the most interview-worthy tickets:

| Component           | Max | What It Measures                                                                   |
| ------------------- | --- | ---------------------------------------------------------------------------------- |
| **Metadata Score**  | 55  | Duration (0-30), status complexity (0-15), recency (0-10)                          |
| **Content Score**   | 45  | Technical depth (0-20), business impact (0-15), resolution quality (0-10)          |
| **Manual Override** | 100 | User-set score takes priority over computed scores                                 |

**How scoring works:**

- **Metadata score** is calculated automatically from CSV data for all tickets
- **Content score** activates when tickets are enriched with descriptions and resolutions
- Auto-score is **capped at 55** to incentivize enrichment
- **Manual score** overrides everything when set
- Score changes are tracked in an audit history

---

## Interview Preparation

The app includes purpose-built tools for turning ticket history into interview talking points:

- **STAR Format Generator** - Auto-generates Situation, Task, Action, Result summaries from ticket data
- **Best Tickets to Mention** - Curated diverse list across categories and complexity (default 15 tickets)
- **Skill Gap Analysis** - 10 skill categories analyzed against your enriched/starred tickets to find coverage gaps
- **Starred Bookmarks** - Flag your top tickets for quick access
- **Print-Friendly View** - Clean layout that hides navigation, buttons, and forms for printouts
- **Export** - Markdown export with STAR sections, perfect for interview prep documents

**Skill categories tracked:** Debugging & RCA, CRDB/Active-Active, Cluster Management, Performance Tuning, Security (TLS/ACL), Data Recovery, Migration/Upgrade, Scripting/Automation, Customer Escalation, Monitoring/Alerting

---

## RAG Chat

The `/chat` page provides a natural language interface for querying your ticket database:

```text
"What CRDB tickets involved data loss?"
"Show me P1 production outages from 2024"
"Top scored tickets about TLS certificate issues"
"Find tickets where we created a custom script"
```

**How it works:**

1. Your question is parsed to extract categories, severity, product filters, time ranges, and keywords
2. FTS5 search finds matching tickets, then structured filters are applied
3. Results are ranked by score and formatted with full STAR analysis data
4. Optionally streamed through a local Ollama model for a synthesized answer

**Two modes:**

- **Ollama mode** - Full LLM responses with streaming, using your local model (mistral-nemo default)
- **Retrieve-only** - Returns matching ticket cards without needing an LLM

---

## CLI Commands

```bash
# Import
python backend/cli.py import-csv <file>                    # Import ZenDesk CSV export

# View
python backend/cli.py show <ticket_id>                     # Full ticket details
python backend/cli.py list [--min-score 25] [-n 20]        # List with filters
python backend/cli.py search "keyword"                     # Full-text search
python backend/cli.py top [--min-score 20]                 # Top significance tickets
python backend/cli.py stats                                # Summary statistics
python backend/cli.py next-unenriched [--limit 10]         # Tickets needing enrichment

# Edit
python backend/cli.py update <id> --star --category "CRDB" # Update fields

# Export
python backend/cli.py export --format markdown --starred   # Export tickets

# Maintenance
python backend/cli.py rescore                              # Recalculate all scores
```

**List command filters:** `--status`, `--min-score`, `--starred`, `--category`, `--limit/-n`, `--sort [score|date|resolution_time]`

**Update command options:** `--subject`, `--category`, `--customer`, `--star/--unstar`, `--score`, `--tag`, `--note`

**Export formats:** `json`, `csv`, `markdown` with optional `--starred` and `--min-score` filters

---

## API Reference

The Flask app exposes a full JSON API alongside the web UI:

### Tickets

| Method   | Endpoint                            | Description                                                              |
| -------- | ----------------------------------- | ------------------------------------------------------------------------ |
| `GET`    | `/api/tickets`                      | List with filters (status, category, score, enrichment, starred, search) |
| `GET`    | `/api/tickets/<id>`                 | Single ticket with notes                                                 |
| `POST`   | `/api/tickets`                      | Create new ticket                                                        |
| `PUT`    | `/api/tickets/<id>`                 | Update ticket fields                                                     |
| `DELETE` | `/api/tickets/<id>`                 | Delete ticket                                                            |
| `GET`    | `/api/tickets/unenriched`           | List tickets needing enrichment                                          |
| `POST`   | `/api/tickets/<id>/reset-enrichment`| Reset to metadata_only                                                   |

### Notes, Tags & Stars

| Method   | Endpoint                              | Description                          |
| -------- | ------------------------------------- | ------------------------------------ |
| `POST`   | `/api/tickets/<id>/notes`             | Add note (general or conversation)   |
| `DELETE` | `/api/tickets/<id>/notes/<note_id>`   | Delete note                          |
| `POST`   | `/api/tickets/<id>/tags`              | Add tags (array)                     |
| `DELETE` | `/api/tickets/<id>/tags/<tag>`        | Remove tag                           |
| `POST`   | `/api/tickets/<id>/star`              | Toggle star                          |

### Import & Enrichment

| Method | Endpoint                      | Description                             |
| ------ | ----------------------------- | --------------------------------------- |
| `POST` | `/api/import/csv`             | Upload CSV file                         |
| `POST` | `/api/import/pdf`             | Upload PDF with auto-ID detection       |
| `POST` | `/api/import/pdf/batch`       | Batch PDF upload                        |
| `POST` | `/api/import/paste`           | Paste content with optional STAR fields |
| `POST` | `/api/enrich/bulk`            | Bulk enrich from JSON array             |
| `POST` | `/api/utils/parse-zendesk-url`| Extract ticket ID from URL              |

### Analytics & Intelligence

| Method | Endpoint                              | Description                             |
| ------ | ------------------------------------- | --------------------------------------- |
| `GET`  | `/api/stats/overview`                 | Dashboard statistics                    |
| `GET`  | `/api/stats/timeline`                 | Monthly volume data                     |
| `GET`  | `/api/stats/score-distribution`       | Score histogram                         |
| `GET`  | `/api/stats/categories`               | Category breakdown with averages        |
| `GET`  | `/api/stats/resolution-times`         | Resolution time buckets                 |
| `GET`  | `/api/stats/resolution-trends`        | Quarterly averages                      |
| `GET`  | `/api/stats/year-over-year`           | Multi-year comparison                   |
| `GET`  | `/api/stats/skill-gaps`               | Skill coverage analysis                 |
| `GET`  | `/api/stats/best-tickets`             | Curated diverse list for interviews     |
| `POST` | `/api/tickets/<id>/suggest-category`  | Auto-suggest category from content      |
| `GET`  | `/api/tickets/<id>/star-format`       | Generate STAR format summary            |

### Search, Chat & Export

| Method | Endpoint                  | Description                                   |
| ------ | ------------------------- | --------------------------------------------- |
| `GET`  | `/api/search?q=keyword`   | FTS5 full-text search                         |
| `POST` | `/api/chat/retrieve`      | Smart ticket retrieval from natural language  |
| `POST` | `/api/chat/ollama`        | Stream LLM response via SSE                   |
| `GET`  | `/api/chat/ollama/health` | Check Ollama availability + list models       |
| `GET`  | `/api/export?format=json` | Export (json, csv, markdown)                  |

### Database Operations

| Method | Endpoint                      | Description                                     |
| ------ | ----------------------------- | ----------------------------------------------- |
| `POST` | `/api/bulk/score`             | Rescore all tickets                             |
| `POST` | `/api/bulk/rebuild-fts`       | Rebuild FTS5 index                              |
| `POST` | `/api/bulk/reset-enrichment`  | Reset all enrichment (requires confirmation)    |
| `POST` | `/api/db/backup`              | Create timestamped backup                       |
| `GET`  | `/api/db/backups`             | List available backups                          |
| `POST` | `/api/db/clear-all`           | Delete everything (auto-backup + confirmation)  |

---

## MCP Server Integration

The MCP server enables Claude Code to automate Zendesk scraping and ticket analysis through browser automation.

### Setup

```bash
# Install MCP dependencies
pip install "mcp[cli]" playwright
playwright install chromium
```

The `.mcp.json` at the project root configures the server for Claude Code.

### Available Tools (20+)

| Category           | Tools                                                                                                |
| ------------------ | ---------------------------------------------------------------------------------------------------- |
| **Authentication** | `zendesk_login`, `check_auth`                                                                        |
| **Scraping**       | `scrape_ticket`, `bulk_scrape`                                                                       |
| **Enrichment**     | `enrich_from_pdf`, `get_ticket_for_analysis`, `save_ticket_analysis`                                 |
| **Discovery**      | `discover_tickets`, `import_discovered_tickets`, `discover_and_import`, `discover_my_closed_tickets` |
| **Status**         | `list_unenriched`, `get_scraping_status`                                                             |
| **Query**          | `query_tickets` (natural language search)                                                            |
| **Reset**          | `reset_ticket_enrichment`                                                                            |
| **Manual Steps**   | `navigate_to_ticket`, `click_ticket_actions`, `click_print_ticket`, `click_print_confirm_and_save`   |

### Enrichment Workflow

The two-phase workflow separates scraping from analysis:

```text
Phase 1: Scrape PDFs
  zendesk_login + check_auth        # Authenticate browser session
  discover_my_closed_tickets()      # Find new tickets not in DB
  bulk_scrape(batch_size=20)        # Download PDFs only (no DB writes)

Phase 2: Enrich + AI Analysis
  enrich_from_pdf(ticket_id)        # Parse PDF, store metadata + conversation
  [Claude reads the conversation]   # AI generates STAR fields
  save_ticket_analysis(ticket_id, summary, root_cause, steps_taken, resolution)
```

---

## Data Flow

```text
CSV Import ──> Metadata (ID, status, dates) ──> Auto-Score (0-55)
                        |
                        v
PDF Import ──> Description, comments, related tickets ──> Content-Score (0-45)
                        |
                        v
Manual Edit ──> Category, tags, notes ──> Enrichment tracking
                        |
                        v
AI Analysis ──> STAR summary, root cause, resolution ──> Full enrichment (0-100)
```

**Enrichment Levels:**

| Level           | Description                    | Max Score |
| --------------- | ------------------------------ | --------- |
| `metadata_only` | CSV data only                  | 55        |
| `partial`       | Has description OR resolution  | 55+       |
| `full`          | Has description AND resolution | 100       |

---

## Project Structure

```text
ticket-tracker/
├── backend/
│   ├── app.py                # Flask app factory + all routes (pages + JSON API)
│   ├── config.py             # Configuration (DB path, directories)
│   ├── cli.py                # Click CLI commands
│   ├── models/
│   │   ├── database.py       # SQLAlchemy instance
│   │   └── ticket.py         # Ticket, TicketTag, TicketNote, ScoreHistory
│   └── services/
│       ├── csv_importer.py   # ZenDesk CSV import (semicolon-delimited)
│       ├── pdf_parser.py     # ZenDesk PDF parsing + conversation synthesis
│       ├── scoring.py        # Significance scoring (metadata + content)
│       ├── enrichment.py     # Shared enrichment pipeline (Flask + MCP)
│       ├── retrieval.py      # RAG query parsing + smart ticket retrieval
│       └── stats.py          # Statistics, analytics, interview prep queries
├── frontend/
│   ├── templates/            # 9 Jinja2 HTML templates
│   └── static/               # CSS + JS assets
├── mcp_server/
│   ├── server.py             # FastMCP server with 20+ tools
│   ├── scraper.py            # Zendesk PDF scraping + ticket discovery
│   └── browser.py            # Playwright browser context management
├── scripts/                  # Utility scripts for batch operations
├── data/                     # SQLite DB + PDFs + backups (gitignored)
├── sample_data/              # Original CSV export
├── run.py                    # Web server entry point (port 5050)
└── requirements.txt
```

---

## Production Deployment

```bash
gunicorn -w 4 -b 0.0.0.0:5050 'backend.app:create_app()'
```

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full project roadmap with current progress and planned features.

**Completed phases:**

1. **Foundation** - Scaffolding, schema, CSV import, auto-scoring, web UI
2. **Enrichment** - Inline editing, notes, tags, PDF parsing, batch import
3. **Intelligence** - Content scoring, STAR format, skill gap analysis
4. **Search & Analytics** - FTS5, Chart.js dashboards, trend analysis
5. **Export & Integration** - JSON/CSV/Markdown export, bulk API, MCP server
6. **Polish** - Loading spinners, print CSS, backups, FTS rebuild
7. **Data Enrichment** - Related ticket auto-detection, conversation parsing
8. **RAG Chat** - Natural language queries with Ollama integration

---

## License

Private project - not licensed for redistribution.
