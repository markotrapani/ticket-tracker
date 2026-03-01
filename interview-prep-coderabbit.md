# CodeRabbit Solutions Engineer Interview Prep — Marko Trapani

> Context for Claude: This document contains Marko's customer stories, metrics, and tool-building examples mined from 898 Zendesk support tickets at Redis. Use these as raw material for scenario-based interview preparation for a Solutions Engineer role at CodeRabbit (AI code review platform). The role requires: technical depth (APIs, cloud, CI/CD, K8s, Terraform), customer-facing communication, startup grit, and the ability to read code and tie it to customer outcomes.

---

## TOP 10 CUSTOMER STORIES (Ranked by Interview Impact)

### 1. ArgoCD Sync Hook — Reusable K8s Upgrade Pattern

- **Customer:** Avaya (Premium Enterprise SLA)
- **Ticket:** #153806
- **Problem:** ArgoCD syncs failed during Redis Enterprise K8s upgrades because the admission webhook blocks REDB modifications while the REC is frozen for rolling restart. The customer's CI/CD pipeline broke every upgrade cycle.
- **What I Did:** Reviewed their Helm chart and ArgoCD application spec. Identified 18,289 failed resource_mgr sub-tasks in CCS. Rather than suggesting they disable the webhook (risky), I designed a Kubernetes Sync Hook Job at wave 15 that polls REC status via the K8s API, only proceeding when `status.state == Running`. Built the full YAML (Job + RBAC ServiceAccount/Role/RoleBinding), lab-tested it in a Kind cluster, and delivered production-ready manifests.
- **Outcome:** Customer confirmed the job "seems to work well." VP of Support (Zohar) personally recognized the work: "You didn't just fix a symptom; you built a pattern the customer can reuse." This is now a reusable artifact for any ArgoCD + Redis Enterprise deployment.
- **Interview Angle:** Architecture guidance + tool building + GitOps/K8s depth. Shows designing reusable solutions instead of one-off fixes — exactly what an SE does when building reference architectures for prospects. Demonstrates deep K8s API, RBAC, and ArgoCD sync-wave knowledge.

---

### 2. ElastiCache-to-Redis Cloud Migration — Won Business from AWS

- **Customer:** bwell (Business SLA)
- **Ticket:** #153756
- **Problem:** Customer had 2.1M keys in AWS ElastiCache and couldn't migrate to Redis Cloud because key names exceeded 64KB (they stored full English text as key names). RIOT-X failed at 7%. The `--skip` flag was useless because Spring Batch classifies this error as non-skippable.
- **What I Did:** Reproduced the issue in a lab (created 128KB key in OSS Redis, confirmed rejection by Redis Enterprise proxy). Researched Spring Batch internals to explain why `--skip` doesn't work for this error class. Built custom migration scripts (bash + Python) to identify, export, and remove oversized keys. Then proposed a SHA-256 key hashing architecture — deterministic hashes producing fixed 64-char hex strings as key names — as the sustainable solution.
- **Outcome:** Customer adopted the SHA-256 architecture, modified their application, and successfully migrated all production Redis instances from AWS ElastiCache to Redis Cloud. Direct competitive win — customer moved off AWS onto Redis' platform.
- **Interview Angle:** Customer architecture guidance + competitive win + startup grit. Standard tooling said "no" and I found a way to say "yes." Didn't just unblock the migration — improved their data architecture. Maps directly to an SE helping prospects overcome adoption blockers.

---

### 3. REAADB CNI/HostPrefix Recovery — Custom Disaster Recovery Procedure

- **Customer:** Bankinter (Enterprise SLA, managed by Kyndryl)
- **Ticket:** #154276 (83 comments, multi-week engagement)
- **Problem:** During an OpenShift CNI + HostPrefix network migration, a customer lost their Active-Active database (REAADB) on one cluster. The K8s operator interpreted a transient API 404 during quorum loss as intentional deletion and removed the REAADB resource. CCS data was lost because the iSCSI volume reattachment was delayed 33 minutes after pod restart.
- **What I Did:** Traced the full incident timeline through operator logs, CCS snapshots, envoy access logs, crdb_coordinator logs, and VolumeAttachment YAML timestamps. Identified the exact 33-minute gap between pod restart (12:58) and volume attachment (13:31). Discovered CCS started empty (0 keys vs 5,460), proving the volume wasn't mounted when the container started. Wrote a step-by-step recovery procedure (remove participant from REAADB, backup CCS, perform migration, recover from CCS backup, re-add participant). Reviewed the customer's own runbook and provided corrections. Filed a GitHub docs issue for incorrect REAADB removal procedure.
- **Outcome:** Customer successfully used the procedure on their pre-ALC cluster: "everything related to Redis worked without any issues. The procedure was applied correctly." Procedure is now reusable across their production clusters.
- **Interview Angle:** Deep technical investigation + customer-facing communication. Forensic debugging (tracing through 6+ log sources to build a timeline), ability to write production runbooks customers can execute independently, and collaborative communication across a multi-stakeholder account (Bankinter, Kyndryl, Redis TAM/SA).

---

### 4. LDAP Empty-Password Vulnerability — Security Tool Building

- **Customer:** Raymond James (Financial Services)
- **Tickets:** #130165, #149546
- **Problem:** Customer's Redis Enterprise LDAP authentication was accepting logins with empty passwords — a critical security vulnerability for a major financial services firm. The unauthenticated bind was treated as a successful anonymous bind by Active Directory.
- **What I Did:** Built a custom two-phase LDAP bind testing tool (`ldap_bind_test.py`) that reproduces Redis Enterprise's exact authentication flow outside of Redis. Phase 1: admin bind test to verify connectivity. Phase 2: user bind with empty password to verify unauthenticated binds are rejected. Proved the issue was both a customer AD configuration issue AND an insecure practice in how Redis' LDAP client was implemented.
- **Outcome:** Tool verified that disabling unauthenticated LDAP binds on the customer's AD servers secured the authentication pathway. Identified a product-level security concern for the engineering team. Customer used the tool to validate their fix across environments.
- **Interview Angle:** Security/compliance + tool building. Financial services customer, authentication vulnerability, built a diagnostic tool. Bridges security concerns between customer config and product behavior — critical for an SE selling to security-conscious enterprises.

---

### 5. Redis ACL Builder — Public Tool Born from a Support Ticket

- **Customer:** Originated from ticket #138687 (Luca Sinico) — ACL namespace visibility issue
- **Problem:** ACL rule syntax and command category system was confusing for customers and engineers, especially with Redis 7 vs 8 differences where `@search` doesn't exist before Redis 8. Also surfaced in tickets #118081 (Banco Azteca) and #124237 (United Wholesale Mortgage) where FT.CREATE couldn't be added to ACL rules — a known limitation (RED-85802) requiring workaround ACL patterns.
- **What I Did:** Built and shipped Redis ACL Builder (https://github.com/markotrapani/redis-acl-builder) — a public web + Electron desktop application for building, validating, and testing Redis ACL rules. Supports Redis 7 and 8 command categories. Published on GitHub. Presented on ACLs at a Support team meeting. Collaborated with KB team on two Redis 8 ACL knowledge base articles. Flagged a Redis 8 bug (RED-176878) affecting module ACL categories.
- **Outcome:** Adopted across all support tiers. L3 engineer (Collin) messaged unsolicited: "Your ACL builder made a ticket of mine much easier just now." Invited by Docs team manager to participate in a Docs Usability Study — the only Support Engineer asked.
- **Interview Angle:** Tool building + product knowledge + initiative. Turned a single ticket into a public product. Shows the SE mindset of building scalable solutions and creating enablement resources. Cross-functional impact (KB articles, bug filing, docs usability study) shows operating beyond the defined role.

---

### 6. Avaya Account-Wide Pattern Discovery — crdb_coordinator Memory Leak

- **Customer:** Avaya (Premium Enterprise, coordinated with Brent, Brandon, Yves)
- **Problem:** Multiple tickets across the Avaya account showed a pattern of repeated "stuck enslave task" failures. Each ticket appeared isolated.
- **What I Did:** Participated in a coordinated account review. Built a shared tracking spreadsheet across multiple tickets. Analyzed logs across tickets to identify the systemic root cause: a memory leak in the crdb_coordinator process that eventually caused the master node to be OOM killed. Preserved logs and correlated timestamps across tickets.
- **Outcome:** Turned isolated tickets into a documented pattern. Informed both the customer relationship strategy and engineering escalation. The pattern would have continued causing outages without cross-ticket correlation.
- **Interview Angle:** Cross-team collaboration + pattern recognition. Zooming out from individual incidents to find systemic issues — exactly what an SE does when analyzing a prospect's architecture for reliability risks. Also directly relevant to CodeRabbit's value prop: finding patterns across code that humans miss.

---

### 7. Azure ACRE RediSearch Index Disappearing — Multi-Layer Root Cause

- **Customer:** Azure ACRE customer (ticket #119391, score: 77, production outage)
- **Problem:** Customer's FT.SEARCH intermittently returned "not allowed" errors and their RediSearch index kept mysteriously disappearing mid-day.
- **What I Did:** Traced a multi-layer causal chain: low node RAM caused max-process-mem to be set too low, which prevented BGSAVE for master-slave replication for over a week. When the master shard crashed (SIGSEGV in jemalloc), the slave was promoted but had stale/missing data — including the RediSearch index. Identified 628 FT.DROP commands in commandstats. Analyzed SLOWLOG showing aggressive FT.SEARCH queries running 10+ seconds. Found full /logfs filesystems on 3/5 nodes.
- **Outcome:** R&D completed RCA via RED-128006 confirming the causal chain. Provided the Azure team with Prometheus alert rules for proactive monitoring.
- **Interview Angle:** Deep technical investigation + cloud platform. Multi-layer RCA across memory management, replication, crash analysis, and search indexing. Reasoning through complex failure cascades — relevant for helping CodeRabbit customers debug CI/CD pipeline failures.

---

### 8. OpenShift Upgrade Crash Loop — CCS Pinning Script Failure

- **Customer:** Silicon Valley Bank (SVB) — Gold Enterprise
- **Ticket:** #114461 (score: 68)
- **Problem:** Redis Enterprise cluster upgrade on OpenShift 4.12 from 6.4.2-8 to 7.2.4-12 failed. The bootstrapper container on the third REC pod entered a crash loop. Customer was using Helm charts managed by ArgoCD (not officially supported).
- **What I Did:** Collected debug info packages and CCS RDB files from all pods. Identified that the CCS pinning script (`/opt/redislabs/sbin/pin_ccs.sh`) failed due to an unbound PLATFORM environment variable. Navigated the customer's security concerns around providing full logs (they pushed back on `-m all` flag).
- **Outcome:** Identified the root cause as an env variable bug in the bootstrap process. Informed the customer that Helm chart deployment was not officially supported, while still providing a path forward.
- **Interview Angle:** K8s/GitOps + navigating customer constraints. Debugging at the container bootstrap level and working within a customer's security limitations — relevant for enterprise SEs who can't always get full access.

---

### 9. Cluster Gossip Certificate Expiry — Full Cluster Recovery on a Live Call

- **Customer:** NCC Media
- **Ticket:** #115811 (score: 56, production outage)
- **Problem:** Redis Enterprise cluster completely unresponsive — web UI returning upstream connect errors, CCS unreachable, rladmin showing "cluster not responding." All 3 nodes had been rebooted but services didn't recover.
- **What I Did:** Initiated a live Zoom call when standard log collection failed (debuginfo couldn't connect to CCS). Traced the root cause to failed gossip certificate rotations on 2 of 3 nodes months apart (September 2023 and February 2024). When both certs expired, inter-node communication broke, causing quorum loss. CCS backup was empty — no recovery possible.
- **Outcome:** Performed a fresh cluster installation live on the call. Installed RS 6.4.2-81, joined all three nodes, handed off to TAM for database creation. Customer confirmed QA/Dev applications were back online.
- **Interview Angle:** Real-time troubleshooting under pressure + security/TLS. Diagnosing and resolving a complete outage live on a call — the kind of composure an SE needs during prospect POCs and customer escalations.

---

### 10. GT Logs Helper — Internal CLI Tool, 18 Releases from Team Feedback

- **Customer:** Internal (Redis Support team, all tiers)
- **Problem:** Support engineers across L1, L2, and L3 needed to frequently upload/download support packages to an S3 GT Logs bucket, with no streamlined workflow.
- **What I Did:** Built and shipped GT Logs Helper (https://github.com/markotrapani/gtlogs-link-generator) — an internal CLI for uploading/downloading support packages. Iterated through 18 releases based on ongoing team feedback. Designed for common L1/L2 workflows.
- **Outcome:** Adopted across all support tiers. 18 releases shipped from real user feedback. Reduced friction in the most common daily workflow for the team.
- **Interview Angle:** Startup grit + iteration + internal tooling. Identified pain points and built solutions without being asked. 18 releases = listening to users and iterating. Maps directly to how a startup SE operates — see a gap, build a tool, ship it.

---

## HARD METRICS

### From the Ticket Database (898 tickets, Sept 2023 — Feb 2026)

| Metric | Value |
|--------|-------|
| Total tickets managed | 898 |
| Production-impacting tickets | 343 (38%) |
| Production outages specifically | 147 (16%) |
| Median resolution time | 9 days |
| Average resolution time | 13.5 days |
| Same-day resolutions | 148 (16.6%) |
| Tickets with Jira escalations | 150 (16.7%) |
| High-significance tickets (score 50+) | 391 (43.5%) |
| Critical-significance tickets (score 70+) | 27 (3.0%) |

### Category Breakdown

| Category | Count | % |
|----------|-------|---|
| Cluster | 263 | 29% |
| Connectivity | 185 | 21% |
| Configuration | 79 | 9% |
| Performance | 70 | 8% |
| Upgrade/Migration | 63 | 7% |
| Monitoring | 31 | 3% |
| ACL/Auth | 30 | 3% |
| TLS/SSL | 23 | 3% |
| CRDB/Active-Active | 21 | 2% |

### Priority Breakdown

| Priority | Count |
|----------|-------|
| Normal | 735 |
| High | 107 |
| Urgent | 29 |
| Low | 27 |

### Notable Enterprise Customers (Repeat Engagements)

Avaya, Silicon Valley Bank (SVB), Raymond James, Bankinter/Kyndryl, NCC Media, Banco Azteca, United Wholesale Mortgage, bwell, Intact Financial Corporation, FedEx

### Confirmed Bug Reports / Jira Issues Filed

RED-176878, RED-128006, RED-173464, RED-179769, RED-109365, RED-77141, RED-85802 (at least 7 confirmed from ticket data + self-assessment)

### Tools Built and Shipped

| Tool | Type | Impact |
|------|------|--------|
| Redis ACL Builder | Public web + desktop app | Adopted across all support tiers, L3 endorsement |
| GT Logs Helper | Internal CLI | 18 releases, adopted across L1/L2/L3 |
| LDAP Bind Tester | Diagnostic tool | Used by Raymond James to validate security fix |
| Migration scripts | Customer-facing | Enabled bwell's ElastiCache-to-Redis Cloud migration |
| ArgoCD Sync Hook YAML | Customer-facing | Reusable K8s upgrade pattern for Avaya |
| REAADB Recovery Procedure | Customer-facing | Reusable runbook for Bankinter's production clusters |
| Ticket Tracker | Personal project | Full-stack app: Flask/SQLite/Playwright/AI enrichment, 898 tickets analyzed |

### Metrics NOT Available (Be Honest About These in Interview)

- **CSAT scores** — Not in the export data. Check Zendesk Explore directly.
- **Resolution time vs team average** — Only have personal tickets, no team benchmark.
- **ARR / revenue impact** — The bwell migration is a clear competitive win but no dollar figures tracked.
- **First response time** — Would need Zendesk SLA metrics export.

---

## THE TICKET TRACKER TOOL (Project Story)

### What It Is

A full-stack application built with Claude Code to catalog, enrich, and analyze 898 Zendesk support tickets for interview preparation and career development.

### Tech Stack

- Backend: Python 3.10+, Flask 3.1, SQLAlchemy 2.0, SQLite
- Frontend: Jinja2 templates, Bootstrap 5 (dark theme), vanilla JS, Chart.js
- Browser Automation: Playwright via MCP (Model Context Protocol) server
- AI Enrichment: Claude generates STAR analysis for every ticket
- Local LLM: Ollama integration for RAG chat
- CLI: Click 8.1 for command-line interface

### How It Works

1. **CSV Import** — Imported Zendesk export (898 tickets with metadata)
2. **PDF Scraping** — MCP server automates Playwright to log into Zendesk, navigate to each ticket, click Print, capture PDF (898 PDFs, 630MB total)
3. **AI Enrichment** — Each PDF parsed for metadata + full conversation thread, then Claude generates STAR analysis (summary, root_cause, steps_taken, resolution)
4. **Significance Scoring** — Composite 0-100 score: metadata (0-55: duration, complexity, recency) + content (0-45: technical depth, business impact, resolution quality)
5. **Search + Query** — FTS5 full-text search, natural language query via MCP tools, web UI with filters/sorting/dashboard

### Interview Pitch

> "I had 898 support tickets and needed to prepare for interviews. Instead of manually reading through them, I built a full-stack application with a Python/Flask backend, SQLite database, browser automation via Playwright, and AI-powered analysis using Claude. It enriched every ticket with STAR-format analysis and scored them by significance. That's how I work — I see a problem, I build a tool."

---

## SELF-ASSESSMENT HIGHLIGHTS (2025 Annual Review)

### Key Accomplishments (Manager-Validated)

1. Led technical resolution, PoC advising, and cross-team collaboration on complex K8s/OpenShift cases (Avaya, Bankinter)
2. Built and shipped three internal/public tools (ACL Builder, GT Logs Helper, LDAP Bind Tester)
3. Advised customer through ElastiCache-to-Redis Cloud migration, winning business from a key competitor
4. Authored Confluence articles, reviewed KB articles, presented on ACLs, flagged Redis 8 bugs
5. Invited to Docs Usability Study by Docs team manager — only Support Engineer asked

### Manager/Peer Quotes

- **Zohar (VP of Support):** "You didn't just fix a symptom; you built a pattern the customer can reuse to keep their upgrades stable. That kind of thinking directly impacts customer trust and how Redis Support is perceived."
- **Collin (L3 Engineer):** "Your ACL builder made a ticket of mine much easier just now."

### Strongest Principle

"Go above and beyond" — Whether it's building a reusable Kubernetes Sync Hook instead of a one-off workaround, turning a single ticket into a public tool, or proposing an architecture change during a migration, my instinct is to look past the immediate ask and deliver something with lasting value.

### Self-Identified Growth Area

Slowing down to ask "what is the customer's goal?" before jumping into solving. Example: On the ElastiCache migration, wrote custom scripts before stepping back to realize architectural guidance was the real value. Got to the right answer, but could have gotten there faster.

---

## MAPPING STORIES TO CODERABBIT INTERVIEW QUESTIONS

| Interview Theme | Best Stories | Key Talking Points |
|----------------|-------------|-------------------|
| **Technical Depth (APIs, CI/CD, K8s)** | #1 ArgoCD, #3 REAADB Recovery, #7 RediSearch, #8 SVB OpenShift | K8s API, RBAC, sync-waves, operator logs, CCS internals, container bootstrap |
| **Customer Architecture Guidance** | #2 ElastiCache Migration, #1 ArgoCD, #3 REAADB Recovery | SHA-256 key design, sync hook pattern, disaster recovery runbooks |
| **Tool Building / Automation** | #5 ACL Builder, #10 GT Logs, #4 LDAP Tester, Ticket Tracker | Public tools, 18 releases from feedback, diagnostic tooling, full-stack app |
| **Startup Grit** | #2 Competitive Win, #10 GT Logs (18 releases), Ticket Tracker | Found a "yes" when tooling said "no," built tools without being asked, iterated fast |
| **Reading Code + Customer Outcomes** | #2 Spring Batch internals, #7 jemalloc crash, #8 CCS pinning script | Traced through framework source code to explain why flags don't work |
| **Cross-Team Collaboration** | #6 Avaya Pattern, #3 Bankinter (multi-stakeholder), #5 ACL (KB team) | Coordinated account reviews, multi-team RCA, knowledge base collaboration |
| **Security / Compliance** | #4 LDAP Vulnerability, #9 Certificate Expiry, #5 ACL Builder | Financial services auth vulnerability, TLS/gossip cert forensics |
| **Cloud Platform** | #2 AWS ElastiCache, #7 Azure ACRE, #8 OpenShift | AWS-to-Redis migration, Azure monitoring, OpenShift SCC/upgrade |
