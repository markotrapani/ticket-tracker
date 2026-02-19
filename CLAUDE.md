# Ticket Tracker - CLAUDE.md

A personal ticket tracking application for cataloging ZenDesk support tickets, enriching them with details, and scoring their significance for interview preparation.

## Tech Stack

- **Backend:** Python 3.10+, Flask 3.1, SQLAlchemy 2.0, SQLite
- **Frontend:** Jinja2 templates, Bootstrap 5 (dark theme), vanilla JS, Chart.js
- **CLI:** Click 8.1
- **PDF Parsing:** pdfplumber
- **Browser Automation:** Playwright (MCP server)
- **Local LLM:** Ollama (optional, for RAG chat)

## Project Structure

```text
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
│       ├── pdf_parser.py   # ZenDesk PDF parsing for ticket enrichment
│       ├── enrichment.py   # Enrichment pipeline (shared by Flask + MCP)
│       ├── retrieval.py    # RAG query parsing + ticket retrieval
│       ├── scoring.py      # Significance scoring engine (metadata + content)
│       └── stats.py        # Statistics and dashboard queries
├── frontend/
│   ├── templates/          # 9 Jinja2 HTML templates
│   └── static/             # CSS + JS assets
├── mcp_server/
│   ├── server.py           # FastMCP server (20+ tools)
│   ├── scraper.py          # Zendesk PDF scraping
│   └── browser.py          # Playwright browser management
├── scripts/                # Batch operation utilities
├── data/                   # SQLite DB + PDFs (gitignored)
├── sample_data/            # Original CSV export + sample CSV
├── run.py                  # Web server entry point (port 5050)
└── requirements.txt
```

## Running the Application

```bash
# Setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Import tickets from CSV
python backend/cli.py import-csv sample_data/your_export.csv

# Start web UI
python run.py
# Visit http://localhost:5050

# CLI commands
python backend/cli.py stats
python backend/cli.py show <ticket_id>
python backend/cli.py list --min-score 25 -n 20
python backend/cli.py search "keyword"
python backend/cli.py top --min-score 20
python backend/cli.py update <ticket_id> --star --category "CRDB"
python backend/cli.py export --format markdown --starred
python backend/cli.py rescore
```

## Data Model — Where Ticket Data Lives

The `Ticket` model has two kinds of text data, stored in different places:

**Ticket table fields** (on the `Ticket` model directly):

- `subject`, `description` — from PDF page 1 metadata
- `summary`, `root_cause`, `steps_taken`, `resolution` — STAR analysis fields
- `category`, `customer_name`, `assignee`, `status`, etc.

**TicketNote table** (separate `ticket_notes` table, FK to `Ticket.id`):

- `note_type='conversation'` — full conversation thread extracted from PDF
- `note_type='general'` — user-added notes

**CRITICAL for searching:** The `query_tickets` MCP tool and FTS5 index
only search Ticket table fields, NOT TicketNote content. When searching
for keywords that may appear in conversations (e.g. "PoC", "outage",
customer names), you MUST also query the TicketNote table directly:

```python
from backend.models.ticket import Ticket, TicketNote
from sqlalchemy import or_

# Search both ticket fields AND conversation notes
pattern = '%search_term%'
field_matches = Ticket.query.filter(or_(
    Ticket.subject.ilike(pattern),
    Ticket.description.ilike(pattern),
    Ticket.summary.ilike(pattern),
    Ticket.root_cause.ilike(pattern),
    Ticket.steps_taken.ilike(pattern),
    Ticket.resolution.ilike(pattern),
)).all()

note_matches = TicketNote.query.filter(
    TicketNote.content.ilike(pattern)
).all()
```

## Significance Scoring (0-100)

Composite score from up to three sources:

**Metadata Auto-Score (0-55 max)** - calculated for all tickets from CSV data:

- Duration (0-30): days to resolve as complexity proxy
- Status Complexity (0-15): still-open long-lived tickets
- Recency (0-10): more recent = more interview-relevant

**Content Score (0-45 max)** - calculated when ticket is enriched:

- Technical Depth (0-20): keyword analysis for RCA, debugging, scripting
- Business Impact (0-15): outage keywords, escalation flags
- Resolution Quality (0-10): has root cause, resolution, custom scripts

**Manual Score (0-100)** - user override, takes priority over computed scores

## Data Flow

1. **CSV Import** -> basic metadata (ID, status, dates) -> auto-score (0-55)
2. **PDF Enrichment** -> subject, description, conversation notes -> content-score (0-45)
3. **AI Analysis** -> STAR fields (summary, root_cause, steps_taken, resolution)
4. **Manual Edit** -> category, tags, notes -> enrichment tracking
5. **Manual Score** -> override computed score

## MCP Server

The MCP server (`mcp_server/`) enables Claude Code to automate Zendesk
scraping and ticket analysis. Configured via `.mcp.json` at project root.

### Enrichment Workflow

1. **Scrape:** `bulk_scrape` downloads PDFs to `data/pdfs/`
2. **Enrich:** `enrich_from_pdf` parses PDF, stores metadata + conversation notes, returns formatted conversation
3. **Analyze:** Claude reads conversation, calls `save_ticket_analysis` with STAR fields

### Key Tools

- `query_tickets` — natural language search (STAR fields only, not notes)
- `get_ticket_for_analysis` — full conversation for a single ticket
- `bulk_scrape` / `scrape_ticket` — download PDFs from Zendesk
- `enrich_from_pdf` / `save_ticket_analysis` — parse + store analysis
- `get_scraping_status` / `list_unenriched` — progress tracking

## Key Design Decisions

- SQLite for zero-setup persistence (single file in data/)
- Enrichment levels: metadata_only -> partial -> full (tracks completeness)
- Score cascade: manual_score > (auto + content) > auto alone
- Auto-score capped at 55 to encourage enrichment
- All fields nullable except zendesk_id and created_date
- ZenDesk URL auto-generated from ticket ID
- Flask app context pattern: `app = create_app()` then `with app.app_context():`

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full project roadmap with current progress and planned features.

## Development Guidelines

- All code stays within /ticket-tracker/ directory
- No external project dependencies or references
- Follow conventional commit format
- Test changes by running `python run.py` and verifying in browser
- CLI and web UI share the same models and services layer
- Enrichment logic in `backend/services/enrichment.py` — shared by Flask routes and MCP server
