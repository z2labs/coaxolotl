# Post-routing pipeline in the order srouter docs/USAGE.md asks for, then KiCad DRC.
#   powershell -ExecutionPolicy Bypass -File hardware\kicad\tools\postroute.ps1
# SROUTER_TOOLS: srouter tools folder (default: the srouter-jlc checkout, feat/jlc-fab: silk_dfm also clears via holes)
param([int]$From = 1)         # restart the pipeline at this step (e.g. -From 3 after a fix)
$ErrorActionPreference = "Continue"
$ctrl  = $env:COAX_VARIANT -eq "ctrl"          # 2-layer control board in hardware/kicad_ctrl
$kd    = if ($ctrl) { Resolve-Path "$PSScriptRoot\..\..\kicad_ctrl" } else { Resolve-Path "$PSScriptRoot\.." }
$plane = if ($ctrl) { "B.Cu" } else { "In1.Cu" }
$py    = "C:\Program Files\KiCad\10.0\bin\python.exe"
$cli   = "C:\Program Files\KiCad\10.0\bin\kicad-cli.exe"
$tools = if ($env:SROUTER_TOOLS) { $env:SROUTER_TOOLS } else { "C:\Users\RF\Documents\GitHub\srouter-jlc\tools" }
$bbt   = if ($env:BASSBERRY_TOOLS) { $env:BASSBERRY_TOOLS } else { "C:\Users\RF\Documents\GitHub\bassberry\hardware\tools" }   # via_off_pad
$board = "$kd\coaxolotl.kicad_pcb"
$log   = "$kd\route\postroute.log"
function step($name, [scriptblock]$b) {
  if ([int]($name.Split(" ")[0] -replace "[^0-9]", "") -lt $From) { return }
  "== $name  $(Get-Date -Format HH:mm:ss)" | Out-File -Append -Encoding utf8 $log
  & $b 2>&1 | Select-String -NotMatch "duplicate image handler|memory leak" | ForEach-Object { $_.Line } | Out-File -Append -Encoding utf8 $log
  "   exit $LASTEXITCODE" | Out-File -Append -Encoding utf8 $log
}
"" | Out-File -Encoding utf8 $log
if ($From -le 1) { Copy-Item $board "$kd\route\pre_tidy.kicad_pcb" }
step "1 track_tidy"   { & $py "$tools\track_tidy.py" $board }
step "1b repair"      { & $py "$PSScriptRoot\route.py" --repair }
step "1c via_off_pad" { & $py "$bbt\via_off_pad.py" $board }
step "2 zone fill"    { & $py -c "import pcbnew;b=pcbnew.LoadBoard(r'$board');pcbnew.ZONE_FILLER(b).Fill(b.Zones());pcbnew.SaveBoard(r'$board',b);print('filled')" }
step "3 gnd_stitch"   { & $py "$tools\gnd_stitch.py" $board --plane $plane }
step "4 via_rescue"   { & $py "$tools\via_rescue.py" $board --net GND }
step "5 stitch_audit" { & $py "$tools\stitch_audit.py" $board }
step "6 silk_dfm fix" { & $py "$tools\silk_dfm.py" $board --fix }
step "6 silk_dfm"     { & $py "$tools\silk_dfm.py" $board }
step "6a via_off_pad" { & $py "$bbt\via_off_pad.py" $board }
step "6d via_dfm"      { & $py "$PSScriptRoot\via_dfm.py" $board }
step "6c repair"      { & $py "$PSScriptRoot\route.py" --repair }
step "6e silk_dfm fix" { & $py "$tools\silk_dfm.py" $board --fix }    # again: vias moved by 6a-6c
step "6b zone refill" { & $py -c "import pcbnew;b=pcbnew.LoadBoard(r'$board');pcbnew.ZONE_FILLER(b).Fill(b.Zones());pcbnew.SaveBoard(r'$board',b);print('refilled')" }
step "7 DRC"          { & $cli pcb drc --schematic-parity --severity-all --format json -o "$kd\route\drc.json" $board;
                        & $py -c "import json,collections;d=json.load(open(r'$kd\route\drc.json'));v=d['violations'];u=d.get('unconnected_items',[]);p=d.get('schematic_parity',[]);c=collections.Counter((x['severity'],x['type']) for x in v);print('violations',len(v),'unconnected',len(u),'parity',len(p));[print(' ',k,n) for k,n in c.most_common()]" }
step "8 JLC DFM"      { & $py "$tools\jlc_dfm_check.py" $board }
"== done $(Get-Date -Format HH:mm:ss)" | Out-File -Append -Encoding utf8 $log
