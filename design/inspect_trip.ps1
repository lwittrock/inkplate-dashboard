$j = Get-Content 'c:\AAA\python-projects\inkplate_test\Dashboard\design\trip_gvc_to_tbu.json' -Raw | ConvertFrom-Json
Write-Host ('Trip count: ' + $j.trips.Count)
for ($ti = 0; $ti -lt [Math]::Min(2, $j.trips.Count); $ti++) {
  $t = $j.trips[$ti]
  Write-Host ''
  Write-Host ('### Trip ' + $ti + ' — status=' + $t.status + ' transfers=' + $t.transfers + ' legs=' + $t.legs.Count)
  for ($i = 0; $i -lt $t.legs.Count; $i++) {
    $l = $t.legs[$i]
    Write-Host ('  leg ' + $i + ': travelType=' + $l.travelType + ' name=' + $l.name + ' cancelled=' + $l.cancelled + ' partCancelled=' + $l.partCancelled)
    $o = $l.origin; $d = $l.destination
    if ($o) { Write-Host ('    O: ' + $o.name + ' planned=' + $o.plannedDateTime + ' actual=' + $o.actualDateTime + ' plannedTrack=' + $o.plannedTrack + ' actualTrack=' + $o.actualTrack) }
    if ($d) { Write-Host ('    D: ' + $d.name + ' planned=' + $d.plannedDateTime + ' actual=' + $d.actualDateTime) }
  }
}
Write-Host ''
Write-Host '### Field-name discovery (top-level leg keys, trip 0 leg 0):'
$t = $j.trips[0]
$t.legs[0].PSObject.Properties | ForEach-Object { Write-Host ('  ' + $_.Name) }
Write-Host ''
Write-Host '### Origin keys (leg 0):'
$t.legs[0].origin.PSObject.Properties | ForEach-Object { Write-Host ('  ' + $_.Name) }
