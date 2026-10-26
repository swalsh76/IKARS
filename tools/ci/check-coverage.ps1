<#
  Fails (exit 1) if any production assembly's line coverage is below its minimum: -MinLinePercent, or the stricter value
  in -AssemblyMinimum for the assemblies where a defect costs most (Ikars.Core: money and fees, once they arrive).

  Every test project's Cobertura report also lists the assemblies it merely references, so a single report says
  little about them (an assembly can look poorly covered in another project's report and fully covered in its own). This script therefore
  merges the reports first: a line counts as covered if ANY report covers it, then each assembly is judged on the
  union. Test infrastructure (-Exclude) is not production code and is not judged; neither are third-party assemblies
  that coverlet happens to instrument, so only assemblies matching -Include are judged.

  coverlet.MTP's own threshold option is not yet supported, so CI uses this instead. One threshold, one place.

    ./tools/ci/check-coverage.ps1 -Path TestResults -MinLinePercent 90
#>
param(
    [Parameter(Mandatory)][string]$Path,
    [ValidateRange(0, 100)][double]$MinLinePercent = 90,
    [string[]]$Exclude = @('Ikars.TestSupport'),
    [string[]]$Include = @('Ikars.*'),
    [hashtable]$AssemblyMinimum = @{ 'Ikars.Core' = 95 },
    # Every production project must be measured: by default each src\<name>\<name>.csproj. A project that is
    # missing from every report (no test project references it) or has no instrumented lines fails the gate,
    # unless it is listed in -AllowEmpty (for example a contracts-only assembly with nothing to execute).
    [string[]]$Require = @(Get-ChildItem -Path (Join-Path $PSScriptRoot '..\..\src') -Filter '*.csproj' -Recurse -ErrorAction SilentlyContinue |
        ForEach-Object { $_.BaseName }),
    [string[]]$AllowEmpty = @()
)

$ErrorActionPreference = 'Stop'
$reports = @(Get-ChildItem -Path $Path -Recurse -Filter 'coverage.cobertura*.xml' -ErrorAction SilentlyContinue)
if ($reports.Count -eq 0) {
    Write-Error "No Cobertura coverage reports found under '$Path'. Did the tests run with --coverlet?"
    exit 1
}

# A class's filename is relative to its report's <sources> root, and different reports can use different roots,
# so each line is keyed on the full path: the same source line is then merged however each report spells it.
function Resolve-SourcePath([string]$filename, [string[]]$roots) {
    if ([IO.Path]::IsPathRooted($filename)) { return [IO.Path]::GetFullPath($filename).ToLowerInvariant() }
    foreach ($root in $roots) {
        $candidate = Join-Path $root $filename
        if (Test-Path -LiteralPath $candidate) { return [IO.Path]::GetFullPath($candidate).ToLowerInvariant() }
    }
    if ($roots.Count -eq 0) { return [IO.Path]::GetFullPath($filename).ToLowerInvariant() }   # no <sources>: as given
    return [IO.Path]::GetFullPath((Join-Path $roots[0] $filename)).ToLowerInvariant()
}

# assembly -> ("full path:line" -> highest hit count seen in any report)
$assemblies = @{}
foreach ($report in $reports) {
    $xml = [xml](Get-Content -LiteralPath $report.FullName -Raw)
    $roots = @($xml.coverage.sources.source | Where-Object { $_ })
    foreach ($package in $xml.coverage.packages.package) {
        if ($Exclude -contains $package.name) { continue }
        if (-not ($Include | Where-Object { $package.name -like $_ })) { continue }
        if (-not $assemblies.ContainsKey($package.name)) { $assemblies[$package.name] = @{} }
        $lines = $assemblies[$package.name]
        foreach ($class in $package.classes.class) {
            $file = Resolve-SourcePath $class.filename $roots
            foreach ($line in $class.lines.line) {
                $key = '{0}:{1}' -f $file, $line.number
                $hits = [int64]$line.hits
                if (-not $lines.ContainsKey($key) -or $hits -gt $lines[$key]) { $lines[$key] = $hits }
            }
        }
    }
}

$failed = $false
foreach ($name in ($Require | Sort-Object -Unique)) {
    if ($Exclude -contains $name) { continue }
    if (-not $assemblies.ContainsKey($name)) {
        'FAIL {0,-22} not measured: no test project covers it' -f $name
        $failed = $true
    }
}

if ($assemblies.Count -eq 0) {
    Write-Error "The reports under '$Path' list no assemblies to judge (included: $($Include -join ', '), excluded: $($Exclude -join ', '))."
    exit 1
}

foreach ($name in ($assemblies.Keys | Sort-Object)) {
    $lines = $assemblies[$name]
    $total = $lines.Count
    if ($total -eq 0) {
        if ($AllowEmpty -contains $name) { 'ok   {0,-22} no instrumented lines (allowed)' -f $name; continue }
        'FAIL {0,-22} no instrumented lines: missing symbols, or list it in -AllowEmpty' -f $name
        $failed = $true
        continue
    }
    $covered = @($lines.Values | Where-Object { $_ -gt 0 }).Count
    $percent = 100.0 * $covered / $total
    $minimum = if ($AssemblyMinimum.ContainsKey($name)) { [double]$AssemblyMinimum[$name] } else { $MinLinePercent }
    $status = if ($percent -ge $minimum) { 'ok  ' } else { 'FAIL' }
    '{0} {1,-22} {2,6:N1}% lines ({3} of {4}), minimum {5:N1}%' -f $status, $name, $percent, $covered, $total, $minimum
    if ($percent -lt $minimum) { $failed = $true }
}
"merged $($reports.Count) report(s); judged: $($Include -join ', '); not judged: $($Exclude -join ', ')"

if ($failed) {
    Write-Error "The coverage gate failed for at least one assembly (see the FAIL lines above)."
    exit 1
}
