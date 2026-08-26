# Weekly Minnesota opinion refresh (runs on the residential Windows box).
#
# REDESIGNED 2026-08-06 onto the backfill pipeline's split: the browser only
# LISTS (mn.gov's search is behind Radware and needs a real headed Chrome),
# and NFSN DOWNLOADS (the PDF host serves a datacenter IP unwalled -- proven
# on ~14,000 PDFs during the 2017-2025 backfill). The old design downloaded
# via in-page fetch, which broke 2026-08-06 with "TypeError: Failed to fetch"
# on every PDF while the listing still worked -- the split also survives
# exactly that class of browser-side breakage.
#
#   list last 14 days (headed Chrome, manifest only)
#     -> scp manifest to NFSN
#     -> NFSN fetch_manifest downloads the PDFs
#     -> ingest_pdfs (appeals AND supreme -- the filedate strategy sweeps all
#        weekdays/categories, so this now forward-fills the Supreme Court too,
#        which the old COA-only weekly never did)
#     -> stamp the freshness beacon (checked weekly by freshness_check.sh)
#
# ingest_pdfs dedups on (court, case_number), so the 14-day overlap window and
# the CL-cron overlap are harmless no-ops. New rows land embedding_pending=True
# and vectorize in the overnight embed tick.
#
# CAPTCHA NOTE: ~2 batched queries for a 14-day window rarely trip the wall,
# but a challenge is NEVER auto-solved -- the scraper waits for the logged-on
# human. Run ONLY WHEN LOGGED ON (Task Scheduler: run only when logged on),
# and never while an attended backfill sweep holds the same Chrome profile
# (that collision is what killed the 2026-08-03 run with Last Result 1).
#
# NOTE: NOT $ErrorActionPreference='Stop' on purpose -- in Windows PowerShell
# 5.1 a native exe writing to stderr under 'Stop' is treated as terminating, so
# we check $LASTEXITCODE explicitly after each native call instead.

$repo     = 'C:\Users\kelly\docketdrift'
$py       = Join-Path $repo '.venv\Scripts\python.exe'
$scraper  = Join-Path $repo 'scripts\mn_scraper\backfill_mn_archive.py'
$log      = Join-Path $repo 'scripts\mn_scraper\mn_weekly.log'
$manifest = Join-Path $env:TEMP 'mn_weekly_manifest.tsv'

function Log($m) { ("{0}  {1}" -f (Get-Date -Format o), $m) | Tee-Object -FilePath $log -Append }

Log '=== MN weekly run START (manifest mode) ==='

$since = (Get-Date).AddDays(-14).ToString('yyyy-MM-dd')
$until = (Get-Date).ToString('yyyy-MM-dd')
Log "listing MN opinions $since..$until (headed Chrome, no downloads)"

Remove-Item $manifest -ErrorAction SilentlyContinue

& $py -u $scraper --strategy filedate --since $since --until $until `
    --no-download --manifest $manifest 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { Log "LISTING FAILED (exit $LASTEXITCODE) -- aborting"; exit 1 }

if (-not (Test-Path $manifest) -or (Get-Item $manifest).Length -eq 0) {
    Log 'manifest empty (courts likely published nothing new). Done.'
    & ssh docketdrift 'date -u +%s > /home/private/docketdrift/.scrape_mn_last' 2>&1 | Out-Null
    exit 0
}
$rows = @(Get-Content $manifest).Count
Log ("manifest holds {0} opinion(s)" -f $rows)

# Ship the manifest; NFSN downloads the PDFs itself (datacenter IP is not
# walled for the PDF host) and ingests both courts.
& scp $manifest 'docketdrift:/tmp/mn_weekly.tsv' 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { Log "SCP FAILED (exit $LASTEXITCODE) -- aborting"; exit 1 }

# Call a real shell script on the server rather than building a bash
# one-liner in a PowerShell string. PowerShell mangles quotes on the way to
# a native exe, which silently broke the ingest branch for two weeks while
# the wrapper reported success -- see the header of nfsn_ingest_manifest.sh.
# One remote command means $LASTEXITCODE actually means something.
& ssh docketdrift '/bin/sh /home/private/docketdrift/scripts/mn_scraper/nfsn_ingest_manifest.sh /tmp/mn_weekly.tsv /tmp/mn_weekly' 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { Log "NFSN FETCH/INGEST FAILED (exit $LASTEXITCODE) -- beacon NOT stamped"; exit 1 }

Remove-Item $manifest -ErrorAction SilentlyContinue
Log '=== MN weekly run DONE (new rows embed overnight on NFSN) ==='
# Freshness beacon: stamp success on NFSN so freshness_check.sh can alert
# within a week if this task starts silently failing.
& ssh docketdrift 'date -u +%s > /home/private/docketdrift/.scrape_mn_last' 2>&1 | Out-Null
