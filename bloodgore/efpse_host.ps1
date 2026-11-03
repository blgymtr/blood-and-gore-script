# Adds or removes decorations by running Easy FPS Editor's own classes, so the .dat files are written in the editor's own format.
# Needs 32-bit Windows PowerShell. The editor exe is only loaded (read only), it is never written and the editor window is not opened.
# Based on the host script of the companion EFPSE Bridge tool (MIT, same author).
# Usage: efpse_host.ps1 -Exe <EasyFPSEditor_CE.exe> -Proje <project folder> -Is list|apply [-Ops <operations.json>]
param([string]$Exe, [string]$Proje, [string]$Is, [string]$Ops = '')
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
if (-not (Test-Path -LiteralPath (Join-Path $Proje 'Game.dat'))) { throw 'not an Easy FPS Editor project folder' }
$engine = Split-Path $Exe
[Environment]::CurrentDirectory = $engine
Set-Location -LiteralPath $engine
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
[void][Reflection.Assembly]::LoadFrom($Exe)
$projDir = if ($Proje.EndsWith('\')) { $Proje } else { $Proje + '\' }
$res = New-Object EasyFPSEditor15.Resources
if (-not $res.LoadAllResources($projDir)) { throw 'The editor could not load the project' }

function SetField($o, $name, $val) {
  $f = $o.GetType().GetField($name)
  if (-not $f) { throw "no such field: $name" }
  $t = $f.FieldType
  if ($t -eq [bool]) { $f.SetValue($o, ($val -eq $true -or "$val" -in '1', 'true', 'True')); return }
  $f.SetValue($o, [Convert]::ChangeType($val, $t, [Globalization.CultureInfo]::InvariantCulture))
}

if ($Is -eq 'list') {
  ($res.decorations | ForEach-Object { $_.name }) -join "`n"
  return
}
if ($Is -ne 'apply') { throw 'unknown mode' }
$list = Get-Content -LiteralPath $Ops -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($op in @($list)) {
  if ($op.op -eq 'add') {
    $o = New-Object EasyFPSEditor15.ResourceDecoration
    $o.id = [EasyFPSEditor15.ResourceDecoration]::freeId
    [EasyFPSEditor15.ResourceDecoration]::freeId = $o.id + 1
    foreach ($p in $op.fields.PSObject.Properties) { SetField $o $p.Name $p.Value }
    [void]$res.decorations.Add($o)
  } elseif ($op.op -eq 'del') {
    $victim = $null
    foreach ($d in $res.decorations) { if ($d.name -eq $op.name) { $victim = $d } }
    if ($victim) { [void]$res.decorations.Remove($victim) }
  } else { throw "unknown operation $($op.op)" }
}
[void]$res.SaveAllResources($projDir)
'SAVED'
