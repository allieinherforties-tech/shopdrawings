"""Server-rendered explanation pages for states the review UI can't show.

The failed-extraction page mirrors the escalation-package philosophy: it
says exactly what failed, why that likely happened, and what to do next —
never a silent guess, never a traceback.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from shop_drawings.studio.jobs import JobPaths, read_failure

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Shop Drawings — extraction failed</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <header>
    <div class="header-row">
      <div>
        <h1>Shop Drawings</h1>
        <p class="meta">job {job_id}</p>
      </div>
      <div class="header-actions">
        <a class="button-link" href="/">Upload another design</a>
      </div>
    </div>
  </header>

  <main>
    <section class="card">
      <h2>Extraction failed</h2>
      <p class="lede">We couldn't read <strong>{source_name}</strong> into a
      spec. Nothing was guessed — the job is saved below with the failure
      recorded.</p>

      <h3>What failed</h3>
      <pre class="error-detail">{error}</pre>

      <h3>Why this likely happened, and what to do</h3>
      <ul>
        <li>Make sure the file is really the design drawing — a full sheet
        with part callouts and dimensions, not a screenshot of one.</li>
        <li>If the file is corrupt or password-protected, re-export it from
        your design tool as a PDF with a text layer (not scanned images).</li>
        <li>v1 reads panel-and-case construction — cabinets, shelving, cases
        built from sheet-goods panels. Carved or sculpted geometry isn't
        supported yet; those designs need a new drawing of the panel parts.</li>
      </ul>

      <p class="lede">If the file looks right, upload it again — and if it
      still fails, send the file along with the error above.</p>
    </section>
  </main>
</body>
</html>
"""


def failed_job_page(jobs_dir: Path, job_id: str) -> str:
    """The failed-extraction page for one job."""
    paths = JobPaths(jobs_dir, job_id)
    failure = read_failure(paths)
    source_name, error = failure if failure is not None else ("unknown file", "")
    return _PAGE.format(
        job_id=escape(job_id),
        source_name=escape(source_name),
        error=escape(error) or "(no detail was recorded)",
    )
