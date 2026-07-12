# exam-ingest — en student-facing pack

> This file is the en language pack for student-visible wording; behavior lives in [skills/exam-ingest/SKILL.md](../../../skills/exam-ingest/SKILL.md) (the control layer, single source of truth).

## Student-facing Output
A one-line setup receipt, e.g.:
  `Prep workspace initialized: 3 wiki chapters + 18 questions (including 2 marked ⚠️ AI-generated answer — not from your teacher or textbook). Progress file created. Next up: teaching Chapter 1.`
  Then hand control back to `exam-cram` for step two (teaching).

Dependency-preflight consent line (asked ONCE when the materials contain PDFs and a backend is missing):

> Your materials include PDFs; reading them needs one parsing library (a single command: `pip install pymupdf`, takes seconds). Install it now? If not, the PDF files will be skipped and only text materials imported.

Post-install receipt:

> Dependency installed — building your knowledge base. This was a one-time step; you won't be asked again.
