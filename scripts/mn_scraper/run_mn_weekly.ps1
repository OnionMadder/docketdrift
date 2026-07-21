# Weekly MN Court of Appeals opinion refresh (runs on the residential Windows box).
#
# CourtListener under-carries recent MN COA nonprecedential/order opinions, and
# mn.gov's law-library search is behind Radware Bot Manager, so MN COA opinions
# are pulled by a HEADED real-Chrome Playwright session here -- the same model
# as the NH scraper (scripts/nh_scraper/run_nh_weekly.ps1). This wraps the chain:
#
#   scrape recent COA opinions  ->  scp PDFs to NFSN  ->  ingest_pdfs
#
# ingest_pdfs dedups on (court, case_number), so the overlap window is a
# harmless no-op on opinions already in the corpus. New rows land
# embedding_pending=True; the NFSN overnight embed tick vectorizes them. MN
# freshness is then verified by the weekly check_freshness task.
#
# CAPTCHA NOTE: a single page-1 load passes the bot wall cleanly, and page 1 is
# the ~10 newest opinions -- enough for a weekly window on most weeks. Deeper
# pagination can trip a CAPTCHA; the scraper then WAITS for the logged-on human
# to solve it in the visible window (it never solves it itself) and otherwise
# proceeds with page 1. So run this ONLY WHEN LOGGED ON, like the NH task. Deep
# BACKFILL over many pages is a separate, attended manual sweep -- not this.
#
# NOTE: NOT $ErrorActionPreference='Stop' on purpose -- in Windows PowerShell
# 5.1 a native exe writing to stderr under 'Stop' is treated as terminating, so
# we check $LASTEXITCODE explicitly after each native call instead.

$repo    = 'C:\Users\kelly\docketdrift'
$py      = Join-Path $repo '.venv\Scripts\python.exe'
$scraper = Join-Path $repo 'scripts\mn_scraper\scrape_mn_coa.py'
$log     = Join-Path $repo 'scripts\mn_scraper\mn_weekly.log'
$pdfdir  = Join-Path $env:TEMP 'mn_coa_pdf'

function Log($m) { ("{0}  {1}" -f (Get-Date -Format o), $m) | Tee-Object -FilePath $log -Append }

Log '=== MN COA weekly run START ==='

# 14-day overlap window; ingest_pdfs dedups, so re-fetching the boundary is safe.
$since = (Get-Date).AddDays(-14).ToString('yyyy-MM-dd')
Log "scraping MN COA opinions since $since (headed Chrome)"

# Clear leftovers from a prior run so the downloaded-count below is accurate.
Remove-Item (Join-Path $pdfdir '*.pdf') -ErrorAction SilentlyContinue

# --max-pages 3 covers a busy week (page 1 is always captured; pages 2-3 are
# attempted and may prompt a one-time CAPTCHA solve in the visible window).
& $py $scraper --since $since --max-pages 3 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { Log "SCRAPER FAILED (exit $LASTEXITCODE) -- aborting"; exit 1 }

$pdfs = @(Get-ChildItem -Path $pdfdir -Filter *.pdf -ErrorAction SilentlyContinue)
if ($pdfs.Count -eq 0) { Log 'no PDFs downloaded (MN COA likely published nothing new). Done.'; exit 0 }
Log ("downloaded {0} PDF(s)" -f $pdfs.Count)

# Ship to NFSN staging.
& ssh docketdrift 'mkdir -p /tmp/mnpdf && rm -f /tmp/mnpdf/*.pdf' 2>&1 | Tee-Object -FilePath $log -Append
& scp @($pdfs.FullName) 'docketdrift:/tmp/mnpdf/' 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { Log "SCP FAILED (exit $LASTEXITCODE) -- aborting"; exit 1 }

# Ingest on NFSN (dedups; embed tick vectorizes new rows overnight), then clean.
& ssh docketdrift 'cd /home/private/docketdrift && source .venv/bin/activate && python manage.py ingest_pdfs --dir /tmp/mnpdf --state MN --court appeals; rm -rf /tmp/mnpdf' 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { Log "INGEST FAILED (exit $LASTEXITCODE)"; exit 1 }

Remove-Item (Join-Path $pdfdir '*.pdf') -ErrorAction SilentlyContinue
Log '=== MN COA weekly run DONE (new rows embed overnight on NFSN) ==='
