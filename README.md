# Ticket Tracker

A personal ticket tracking application for cataloging ZenDesk support tickets, enriching them with technical details from PDF exports, and scoring their significance for interview preparation.

Built for a support engineer who needs to quickly identify and articulate their most impactful work from hundreds of resolved tickets.

## Features

### Ticket Management
- **CSV Import** - Bulk import from ZenDesk semicolon-delimited CSV exports
- **PDF Enrichment** - Parse ZenDesk print-view PDFs to extract full conversation threads, metadata, and resolution details
- **Batch PDF Upload** - Import multiple PDFs at once with auto-detected ticket IDs
- **Manual Entry** - Quick-add modal, paste content, or create tickets manually
- **Inline Editing** - Edit any ticket field directly from the detail view with auto-save
- **Notes & Tags** - Add notes, tags, and bookmarks to organize tickets
- **Related Tickets** - Auto-detect cross-referenced ticket IDs from conversation threads with surrounding context

### Significance Scoring (0-100)

Composite score from three sources that helps surface the most interview-worthy tickets:

| Component | Max Score | Source |
|-----------|-----------|--------|
| **Metadata Score** | 55 | Duration (0-30), status complexity (0-15), recency (0-10) |
| **Content Score** | 45 | Technical depth (0-20), business impact (0-15), resolution quality (0-10) |
| **Manual Override** | 100 | User-set score takes priority over computed scores |

- Metadata score is calculated automatically from CSV data
- Content score activates when tickets are enriched with descriptions and resolutions
- Auto-score is capped at 55 to incentivize enrichment

### Interview Preparation
- **STAR Format Generator** - Auto-generates Situation, Task, Action, Result summaries
- **Skill Gap Analysis** - Identifies categories with no high-scoring tickets
- **Best Tickets to Mention** - Curated diverse list across categories and complexity levels
- **Starred Bookmarks** - Flag top tickets for quick access
- **Print-Friendly View** - Clean layout for interview prep printouts

### Search & Analytics
- **Full-Text Search** - SQLite FTS5 with LIKE fallback across all text fields
- **Score Distribution** - Histogram of ticket scores
- **Monthly Volume** - Ticket creation trends over time
- **Resolution Times** - Distribution and quarterly trend analysis
- **Category Breakdown** - Tickets per category with average scores
- **Year-over-Year** - Line charts comparing ticket volumes by year

### MCP Server (Automated Scraping)
- **Browser Automation** - Playwright-based Zendesk navigation with persistent session
- **PDF Scraping** - Automated print-view PDF generation from authenticated Zendesk pages
- **Batch Processing** - Scrape 20+ tickets per batch with configurable delays
- **Ticket Discovery** - Find new tickets from Zendesk search results or saved views
- **AI Analysis** - Claude reads ticket conversations and generates STAR-format analysis via MCP tools

## Tech Stack

- **Backend:** Python 3.10+, Flask 3.1, SQLAlchemy 2.0, SQLite
- **Frontend:** Jinja2 templates, Bootstrap 5 (dark theme), vanilla JS, Chart.js
- **CLI:** Click 8.1
- **PDF Parsing:** pdfplumber
- **Browser Automation:** Playwright (MCP server)
- **Production Server:** Gunicorn

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

## Project Structure

```
ticket-tracker/
├── backend/
│   ├── app.py              # Flask app factory + all routes (pages + JSON API)
│   ├── config.py           # Configuration (DB path, directories)
│   ├── cli.py              # Click CLI commands
│   ├── models/
│   │   ├── database.py     # SQLAlchemy instance
│   │   └── ticket.py       # Ticket, TicketTag, TicketNote, ScoreHistory models
│   └── services/
│       ├── csv_importer.py # ZenDesk CSV import (semicolon-delimited)
│       ├── pdf_parser.py   # ZenDesk PDF parsing + conversation synthesis
│       ├── scoring.py      # Significance scoring engine (metadata + content)
│       ├── enrichment.py   # Shared enrichment pipeline (Flask + MCP)
│       └── stats.py        # Statistics, analytics, and interview prep queries
├── frontend/
│   ├── templates/          # 8 Jinja2 HTML templates
│   └── static/             # CSS + JS assets
├── mcp_server/
│   ├── server.py           # FastMCP server with 20+ tools
│   ├── scraper.py          # Zendesk PDF scraping + ticket discovery
│   └── browser.py          # Playwright browser context management
├── data/                   # SQLite DB + PDFs + backups (gitignored)
├── run.py                  # Web server entry point (port 5050)
└── requirements.txt
```

## CLI Commands

```bash
python backend/cli.py import-csv <file>          # Import ZenDesk CSV export
python backend/cli.py show <ticket_id>            # Display full ticket details
python backend/cli.py list [--min-score 25] [-n 20]  # List with filters
python backend/cli.py search "keyword"            # Full-text search
python backend/cli.py top [--min-score 20]        # Top significance tickets
python backend/cli.py stats                       # Summary statistics
python backend/cli.py update <id> --star --category "CRDB"  # Update fields
python backend/cli.py export --format markdown --starred     # Export tickets
python backend/cli.py next-unenriched [--limit 10]  # Tickets needing enrichment
python backend/cli.py rescore                     # Recalculate all scores
```

## Web API

The Flask app exposes a full JSON API alongside the web UI:

| Endpoint | Description |
|----------|-------------|
| `GET /api/tickets` | List tickets with filters (status, category, score, enrichment) |
| `GET /api/tickets/<id>` | Get single ticket |
| `PUT /api/tickets/<id>` | Update ticket fields |
| `POST /api/tickets` | Create new ticket |
| `POST /api/import/csv` | Upload CSV file |
| `POST /api/import/pdf` | Upload PDF for enrichment |
| `POST /api/import/pdf/batch` | Batch PDF upload |
| `GET /api/tickets/unenriched` | List tickets needing content |
| `POST /api/enrich/bulk` | Bulk enrich with JSON data |
| `GET /api/stats/overview` | Dashboard statistics |
| `GET /api/search?q=keyword` | Full-text search |
| `GET /api/export?format=json` | Export (json, csv, markdown) |
| `POST /api/db/backup` | Create timestamped backup |

## Data Flow

```
CSV Import ──> Metadata (ID, status, dates) ──> Auto-Score (0-55)
                          │
                          v
PDF Import ──> Description, comments, related tickets ──> Content-Score (0-45)
                          │
                          v
Manual Edit ──> Category, tags, notes ──> Enrichment tracking
                          │
                          v
AI Analysis ──> STAR summary, root cause, resolution ──> Full enrichment
```

**Enrichment Levels:**
- `metadata_only` - CSV data only (auto-score capped at 55)
- `partial` - Has description OR resolution
- `full` - Has description AND resolution (unlocks content scoring up to 100)

## MCP Server Integration

The MCP server enables Claude Code to automate Zendesk scraping through browser automation:

```bash
# Install MCP dependencies
pip install "mcp[cli]" playwright
playwright install chromium
```

The `.mcp.json` at the project root configures the server for Claude Code. Available tool categories:

- **Authentication** - `zendesk_login`, `check_auth`
- **Scraping** - `scrape_and_enrich`, `bulk_scrape_and_enrich`
- **Discovery** - `discover_tickets`, `discover_and_import`, `discover_my_closed_tickets`
- **Enrichment** - `enrich_from_pdf`, `get_ticket_for_analysis`, `save_ticket_analysis`
- **Status** - `list_unenriched`, `get_scraping_status`
- **Reset** - `reset_ticket_enrichment`

### Enrichment Workflow

```
1. zendesk_login + check_auth          # Authenticate browser session
2. discover_my_closed_tickets()        # Find new tickets not in DB
3. bulk_scrape_and_enrich(batch=20)    # Download PDFs + parse metadata
4. get_ticket_for_analysis(id)         # Read conversation for AI
5. save_ticket_analysis(id, ...)       # Store AI-generated STAR fields
```

## Production Deployment

```bash
gunicorn -w 4 -b 0.0.0.0:5050 'backend.app:create_app()'
```

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full project roadmap. Completed phases:

1. **Foundation** - Scaffolding, schema, CSV import, auto-scoring, web UI
2. **Enrichment** - Inline editing, notes, tags, PDF parsing, batch import
3. **Intelligence** - Content scoring, STAR format, skill gap analysis
4. **Search & Analytics** - FTS5, Chart.js dashboards, trend analysis
5. **Export & Integration** - JSON/CSV/Markdown export, bulk API, MCP server
6. **Polish** - Loading spinners, print CSS, backups, FTS rebuild
7. **Data Enrichment** - Related ticket auto-detection from conversations

## License

Private project - not licensed for redistribution.
