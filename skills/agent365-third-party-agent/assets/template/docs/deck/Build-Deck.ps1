# Rebuild docs\Agent365-Demo-Diagrams-clean.pptx from docs\00-diagrams.html.
# Needs: repo .venv with playwright, Microsoft Edge, Node + global pptxgenjs. -Render also needs PowerPoint (slide PNGs for review).
param([switch]$Render)
$ErrorActionPreference = 'Stop'
$repo = Resolve-Path "$PSScriptRoot\..\.."
$env:PYTHONIOENCODING = 'utf-8'
& "$repo\.venv\Scripts\python.exe" "$PSScriptRoot\export_diagrams.py"
if ($LASTEXITCODE) { throw 'export failed' }
$env:NODE_PATH = (npm root -g)
node "$PSScriptRoot\build_deck.js"
if ($LASTEXITCODE) { throw 'build failed' }
if ($Render) {
  $out = "$PSScriptRoot\build\render"; New-Item -ItemType Directory -Force $out | Out-Null
  Remove-Item "$out\*.png" -ErrorAction SilentlyContinue
  $pp = New-Object -ComObject PowerPoint.Application
  $p = $pp.Presentations.Open("$repo\docs\Agent365-Demo-Diagrams-clean.pptx", $true, $false, $false)
  $i = 1; foreach ($s in $p.Slides) { $s.Export("$out\slide-$('{0:D2}' -f $i).png", 'PNG', 1600, 900); $i++ }
  $p.Close()
  "rendered $($i-1) slides to $out"
}
