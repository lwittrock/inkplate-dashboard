$ErrorActionPreference = 'Stop'

# Dev helper: spike the NS Trip Planner v3 from PowerShell so the JSON shape
# can be inspected before wiring it into the firmware. Reads the API key from
# ../secrets.h (which is gitignored). Writes responses next to this script.

$repoRoot = Split-Path -Parent $PSScriptRoot
$secrets  = Join-Path $repoRoot 'secrets.h'
$m = Select-String -Path $secrets -Pattern 'NS_API_KEY\s*=\s*"([^"]+)"'
if (-not $m) { Write-Host 'KEY_NOT_FOUND'; exit 1 }
$key = $m.Matches[0].Groups[1].Value
Write-Host ("Key length: " + $key.Length)

function Hit($from, $to, $label) {
  Write-Host ""
  Write-Host ("=== " + $label + " (from=" + $from + " to=" + $to + ") ===")
  $url = "https://gateway.apiportal.ns.nl/reisinformatie-api/api/v3/trips?fromStation=$from&toStation=$to&maxJourneys=3"
  $headers = @{ 'Ocp-Apim-Subscription-Key' = $key }
  try {
    $r = Invoke-WebRequest -Uri $url -Headers $headers -UseBasicParsing -TimeoutSec 20
    Write-Host ("HTTP " + $r.StatusCode)
    Write-Host ("Body bytes: " + $r.RawContentLength)
    $out = Join-Path $PSScriptRoot ("trip_" + $label + ".json")
    [System.IO.File]::WriteAllText($out, $r.Content)
    Write-Host ("Saved to: " + $out)
    Write-Host "--- first 1500 chars ---"
    Write-Host $r.Content.Substring(0, [Math]::Min(1500, $r.Content.Length))
  } catch {
    Write-Host ("ERROR: " + $_.Exception.Message)
    if ($_.Exception.Response) {
      Write-Host ("Status: " + [int]$_.Exception.Response.StatusCode)
      try {
        $stream = $_.Exception.Response.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($stream)
        $body = $reader.ReadToEnd()
        Write-Host "--- error body ---"
        Write-Host $body
      } catch {}
    }
  }
}

Hit 'GVC' 'TBU' 'gvc_to_tbu'
Hit 'GV'  'TBU' 'gv_to_tbu'
Hit 'GVH' 'TBU' 'gvh_to_tbu'
