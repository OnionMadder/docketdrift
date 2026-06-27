# Weekly NH Supreme Court opinion refresh (runs on the residential Windows box).
#
# courts.nh.gov is Akamai-blocked server-side, so NH opinions can only be pulled
# by a HEADED real-Chrome Playwright session here (see CLAUDE.md + the
# project_nh_two_ingest_pipelines memory). This wraps the full chain:
#
#   scrape recent slip opinions  ->  scp PDFs to NFSN  ->  ingest_pdfs
#
# ingest_pdfs dedups on (court, case_number), so the 30-day overlap window is a
# harmless no-op on opinions already in the corpus. New rows land
# embedding_pending=True; the NFSN overnight embed tick vectorizes them. NH
# freshness is then verified by the weekly check_freshness task.
#
# Registered via Windows Task Scheduler, RUN ONLY WHEN THE USER IS LOGGED ON
# (a headed browser needs an interactive desktop). A headed Chrome window will
# briefly appear while it runs.
#
# NOTE: NOT $ErrorActionPreference='Stop' on purpose -- in Windows PowerShell
# 5.1 a native exe writing to stderr under 'Stop' is treated as terminating, so
# we check $LASTEXITCODE explicitly after each native call instead.

$repo    = 'C:\Users\kelly\docketdrift'
$py      = Join-Path $repo '.venv\Scripts\python.exe'
$scraper = Join-Path $repo 'scripts\nh_scraper\scrape_nh_opinions.py'
$log     = Join-Path $repo 'scripts\nh_scraper\nh_weekly.log'
$pdfdir  = Join-Path $env:TEMP 'nh_opinions_pdf'

function Log($m) { ("{0}  {1}" -f (Get-Date -Format o), $m) | Tee-Object -FilePath $log -Append }

Log '=== NH weekly run START ==='

# 30-day overlap window; ingest_pdfs dedups, so re-fetching the boundary is safe.
$since = (Get-Date).AddDays(-30).ToString('yyyy-MM-dd')
Log "scraping NH opinions since $since (headed Chrome)"

# Clear leftovers from a prior run so the downloaded-count below is accurate.
Remove-Item (Join-Path $pdfdir '*.pdf') -ErrorAction SilentlyContinue

& $py $scraper --since $since 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { Log "SCRAPER FAILED (exit $LASTEXITCODE) -- aborting"; exit 1 }

$pdfs = @(Get-ChildItem -Path $pdfdir -Filter *.pdf -ErrorAction SilentlyContinue)
if ($pdfs.Count -eq 0) { Log 'no PDFs downloaded (NH likely published nothing new). Done.'; exit 0 }
Log ("downloaded {0} PDF(s)" -f $pdfs.Count)

# Ship to NFSN staging.
& ssh docketdrift 'mkdir -p /tmp/nhpdf && rm -f /tmp/nhpdf/*.pdf' 2>&1 | Tee-Object -FilePath $log -Append
& scp @($pdfs.FullName) 'docketdrift:/tmp/nhpdf/' 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { Log "SCP FAILED (exit $LASTEXITCODE) -- aborting"; exit 1 }

# Ingest on NFSN (dedups; embed tick vectorizes new rows overnight), then clean.
& ssh docketdrift 'cd /home/private/docketdrift && source .venv/bin/activate && python manage.py ingest_pdfs --dir /tmp/nhpdf --state NH --court supreme; rm -rf /tmp/nhpdf' 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { Log "INGEST FAILED (exit $LASTEXITCODE)"; exit 1 }

Remove-Item (Join-Path $pdfdir '*.pdf') -ErrorAction SilentlyContinue
Log '=== NH weekly run DONE (new rows embed overnight on NFSN) ==='
