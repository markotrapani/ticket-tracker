# ZenDesk PDF Format (Print View)

The ticket tracker expects PDF exports from ZenDesk's **print view** (Ctrl+P / Cmd+P from a ticket page).

## Expected PDF Structure

### Page 1: Ticket Metadata

```
2/5/26, 2:15 PM  #153756 - Unable to migrate data from AWS Elasticache

#153756  Unable to migrate data from AWS Elasticache

Requester  Naveen Kumar <naveen.kumar@example.com>

Solved Solved - Normal Support - L3 Marko Trapani

[Metadata table rows - pdfplumber flattens multi-column layout:]

Customer Impact Issue  Timezone  Problem Summary
Other  India /Kolkata (UTC +05:30)  Migration fails due to Redis key too long error...

Resolution Summary  Additional Products
The solution was migrating using SHA256 hashed keys...  additional_products_riot

This is a production environment:  Yes
```

### Pages 2+: Conversation Thread

```
Naveen Kumar  January 10, 2026 at 8:30 AM
Hi Redis team,
We are migrating our production AWS ElastiCache data to Redis Cloud...

Marko Trapani  January 10, 2026 at 3:15 PM
Internal note
[Investigation notes visible only to support team]

Marko Trapani  January 11, 2026 at 10:00 AM
Hi Naveen,
After investigating, the root cause is...
```

## Key Patterns the Parser Looks For

| Pattern | Purpose |
|---------|---------|
| `#NNNNNN` in filename or page 1 | Ticket ID extraction |
| `Name <email>` near "Requester" | Customer name |
| `Problem Summary` header row + next line | Root cause metadata |
| `Resolution Summary` header row + next line | Resolution metadata |
| `Author Name  Month DD, YYYY at HH:MM AM/PM` | Comment headers |
| `Internal note` after comment header | Internal note marker |
| `M/D/YY, H:MM PM #NNNNNN` | Page headers (filtered out) |
| `about:blank N/NN` | Page footers (filtered out) |

## Naming Convention

Name PDFs as: `#TICKET_ID - Subject.pdf`

Example: `#153756 - Unable to migrate data from AWS Elasticache.pdf`

This allows the parser to extract the ticket ID from the filename even if the PDF content is hard to parse.

## How It Maps to STAR Fields

| PDF Source | STAR Field |
|------------|-----------|
| Metadata + first customer message | **Summary** (Situation) |
| Problem Summary + diagnostic internal notes | **Root Cause** |
| All internal notes | **Steps Taken** (Action) |
| Resolution Summary + engineer public messages | **Resolution** (Result) |
