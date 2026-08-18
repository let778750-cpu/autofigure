[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$InputPath,

    [string]$OutputRoot,

    [string]$ConfigPath,

    [string]$HostRuntimeConfigPath,

    [ValidatePattern('^[0-9A-Fa-f]{64}$')]
    [string]$ExpectedSourceSha256,

    [ValidatePattern('^(auto|cpu|gpu(:[0-9]+)?)$')]
    [string]$Device = 'auto',

    [ValidateSet('standard', 'strict')]
    [string]$PolicyProfile = 'standard',

    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{5,127}$')]
    [string]$ResumeRun,

    [switch]$Status,

    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{5,127}$')]
    [string]$RunId,

    [switch]$NoTiles,

    [switch]$NoQuarterTurnReview,

    [switch]$SkipAnalysis,

    [switch]$SkipSegmentation,

    [switch]$SkipAgentVisionPkg
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Resolve-SafeOutputPath([string]$RequestedPath, [string]$Label) {
    foreach ($devicePrefix in @('\\?\', '\\.\', '\??\')) {
        if ($RequestedPath.StartsWith($devicePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "$Label cannot use a Win32 or NT device namespace path: $RequestedPath"
        }
    }
    $fullPath = [System.IO.Path]::GetFullPath($RequestedPath)
    $pathRoot = [System.IO.Path]::GetPathRoot($fullPath)
    if ([string]::IsNullOrWhiteSpace($pathRoot)) {
        throw "$Label has no filesystem root: $RequestedPath"
    }
    $cursor = $pathRoot
    $relative = $fullPath.Substring($pathRoot.Length)
    foreach ($segment in ($relative -split '[\\/]' | Where-Object { $_ -ne '' })) {
        $cursor = Join-Path $cursor $segment
        if (-not (Test-Path -LiteralPath $cursor)) {
            break
        }
        $item = Get-Item -LiteralPath $cursor -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Label cannot traverse a symlink or junction: $cursor"
        }
    }
    return $fullPath
}

function Get-OpenStreamSha256([System.IO.FileStream]$Stream) {
    $OriginalPosition = $Stream.Position
    try {
        $Stream.Position = 0
        $Hasher = [System.Security.Cryptography.SHA256]::Create()
        try {
            $Digest = $Hasher.ComputeHash($Stream)
        }
        finally {
            $Hasher.Dispose()
        }
        return ([System.BitConverter]::ToString($Digest)).Replace('-', '').ToUpperInvariant()
    }
    finally {
        $Stream.Position = $OriginalPosition
    }
}

function Open-ReadOnlyEvidenceSnapshot(
    [string]$Path,
    [string]$Label,
    [switch]$IncludeBytes
) {
    $FullPath = [System.IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $FullPath -PathType Leaf)) {
        throw "$Label is missing: $FullPath"
    }
    $Stream = [System.IO.FileStream]::new(
        $FullPath,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    try {
        $SizeBytes = [long]$Stream.Length
        $Sha256 = Get-OpenStreamSha256 $Stream
        $Payload = $null
        if ($IncludeBytes) {
            if ($SizeBytes -gt [int]::MaxValue) {
                throw "$Label is too large for an in-memory JSON snapshot: $SizeBytes bytes"
            }
            $Payload = [byte[]]::new([int]$SizeBytes)
            $Stream.Position = 0
            $Offset = 0
            while ($Offset -lt $Payload.Length) {
                $ReadCount = $Stream.Read($Payload, $Offset, $Payload.Length - $Offset)
                if ($ReadCount -le 0) {
                    throw "$Label changed length while its snapshot was being read."
                }
                $Offset += $ReadCount
            }
            $Stream.Position = 0
        }
        return [pscustomobject]@{
            Label = $Label
            Path = $FullPath
            Stream = $Stream
            SizeBytes = $SizeBytes
            Sha256 = $Sha256
            Bytes = $Payload
        }
    }
    catch {
        $Stream.Dispose()
        throw
    }
}

function ConvertFrom-Utf8SnapshotJson([object]$Snapshot) {
    if ($null -eq $Snapshot.Bytes) {
        throw "$($Snapshot.Label) snapshot did not retain JSON bytes."
    }
    $Utf8 = [System.Text.UTF8Encoding]::new($false, $true)
    try {
        $RawJson = $Utf8.GetString([byte[]]$Snapshot.Bytes)
    }
    catch {
        throw "$($Snapshot.Label) is not strict UTF-8: $($_.Exception.Message)"
    }
    return $RawJson | ConvertFrom-Json
}

function Assert-EvidenceSnapshotUnchanged([object]$Snapshot) {
    if ([long]$Snapshot.Stream.Length -ne [long]$Snapshot.SizeBytes) {
        throw "$($Snapshot.Label) changed size after validation: $($Snapshot.Path)"
    }
    $CurrentHash = Get-OpenStreamSha256 $Snapshot.Stream
    if ($CurrentHash -cne [string]$Snapshot.Sha256) {
        throw "$($Snapshot.Label) changed bytes after validation: $($Snapshot.Path)"
    }
}

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ([string]::IsNullOrWhiteSpace($InputPath)) {
    $InputPath = Join-Path $ProjectRoot 'examples\target_figure.png'
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $ProjectRoot 'examples\generated\runs'
}
if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $ProjectRoot 'ocr-config.json'
}
if ([string]::IsNullOrWhiteSpace($HostRuntimeConfigPath)) {
    $HostRuntimeConfigPath = Join-Path $ProjectRoot 'host-runtime.json'
}

if ($Status) {
    if ([string]::IsNullOrWhiteSpace($RunId)) {
        throw '-Status requires -RunId.'
    }
    if (-not [string]::IsNullOrWhiteSpace($ResumeRun)) {
        throw '-Status and -ResumeRun are mutually exclusive.'
    }
    $StatusRuntimeConfig = (Resolve-Path -LiteralPath $HostRuntimeConfigPath).Path
    $StatusRuntime = Get-Content -LiteralPath $StatusRuntimeConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    $StatusPython = Join-Path ([string]$StatusRuntime.root) ([string]$StatusRuntime.python_relative_path)
    $StatusRunRoot = Resolve-SafeOutputPath $OutputRoot 'StatusOutputRoot'
    $StatusRunDirectory = Resolve-SafeOutputPath (Join-Path $StatusRunRoot $RunId) 'StatusRunDirectory'
    $ExpectedStatusPrefix = $StatusRunRoot.TrimEnd('\') + '\'
    if (-not $StatusRunDirectory.StartsWith($ExpectedStatusPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Requested status run escaped OutputRoot.'
    }
    & $StatusPython -I -B -X utf8 (Join-Path $ProjectRoot 'tools\run_state.py') status --run-dir $StatusRunDirectory
    exit $LASTEXITCODE
}

$Source = (Resolve-Path -LiteralPath $InputPath).Path
$Config = (Resolve-Path -LiteralPath $ConfigPath).Path
$HostRuntimeConfig = (Resolve-Path -LiteralPath $HostRuntimeConfigPath).Path
$CanonicalHostRuntimeConfig = [System.IO.Path]::GetFullPath(
    (Join-Path $ProjectRoot 'host-runtime.json')
)
if (-not [string]::Equals(
    [System.IO.Path]::GetFullPath($HostRuntimeConfig),
    $CanonicalHostRuntimeConfig,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Host runtime must use the canonical project contract: $CanonicalHostRuntimeConfig"
}
$ConfigObject = Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
$HostRuntimeObject = Get-Content -LiteralPath $HostRuntimeConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$HostRoot = [System.IO.Path]::GetFullPath([string]$HostRuntimeObject.root)
$HostPython = Join-Path $HostRoot ([string]$HostRuntimeObject.python_relative_path)
$PaddleRoot = [System.IO.Path]::GetFullPath([string]$ConfigObject.paddle_root)
$PaddlePython = Join-Path $PaddleRoot ([string]$ConfigObject.runtime.python_relative_path)
if (-not (Test-Path -LiteralPath $HostPython -PathType Leaf)) {
    throw "Pinned host CV interpreter is missing: $HostPython"
}
if (-not (Test-Path -LiteralPath $PaddlePython -PathType Leaf)) {
    throw "Pinned PaddleOCR interpreter is missing: $PaddlePython"
}
$OutputPolicyScript = Join-Path $ProjectRoot 'tools\output_policy.py'
function Invoke-SharedOutputPolicy(
    [string]$RequestedPath,
    [string]$PolicyProjectRoot,
    [string]$Label
) {
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $PolicyOutput = @(
            & $HostPython -I -S -B -X utf8 $OutputPolicyScript `
                --path $RequestedPath --project-root $PolicyProjectRoot 2>&1
        )
        $PolicyExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    if ($PolicyExitCode -ne 0 -or $PolicyOutput.Count -ne 1) {
        throw "$Label failed the shared output policy: $($PolicyOutput -join [Environment]::NewLine)"
    }
    $Canonical = ([string]$PolicyOutput[0]).Trim()
    if ($Canonical -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))') {
        throw "$Label policy returned a non-absolute or ambiguous path: $Canonical"
    }
    return Resolve-SafeOutputPath $Canonical $Label
}

$LexicalRunsRoot = Resolve-SafeOutputPath `
    (Join-Path $ProjectRoot 'examples\generated\runs') 'CanonicalRunsRoot'
$CanonicalRunsRoot = Invoke-SharedOutputPolicy `
    $LexicalRunsRoot $ProjectRoot 'CanonicalRunsRoot'
if (-not $CanonicalRunsRoot.EndsWith(
    'examples\generated\runs',
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "CanonicalRunsRoot lost its required examples\generated\runs suffix: $CanonicalRunsRoot"
}
$CanonicalExamplesRoot = Split-Path -Parent (Split-Path -Parent $CanonicalRunsRoot)
$ProjectRoot = Split-Path -Parent $CanonicalExamplesRoot
$OutputPolicyScript = Join-Path $ProjectRoot 'tools\output_policy.py'
$OutputRootWasAbsolute = $OutputRoot -match '^(?:[A-Za-z]:[\\/]|\\\\)'
$OutputRoot = Resolve-SafeOutputPath $OutputRoot 'OutputRoot'
$OutputRoot = Invoke-SharedOutputPolicy $OutputRoot $ProjectRoot 'OutputRoot'
$ProjectPrefix = $ProjectRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) +
    [System.IO.Path]::DirectorySeparatorChar
$RunsPrefix = $CanonicalRunsRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) +
    [System.IO.Path]::DirectorySeparatorChar
$OutputIsInsideProject = $OutputRoot.Equals(
    $ProjectRoot,
    [System.StringComparison]::OrdinalIgnoreCase
) -or $OutputRoot.StartsWith($ProjectPrefix, [System.StringComparison]::OrdinalIgnoreCase)
$OutputIsAllowedRunRoot = $OutputRoot.Equals(
    $CanonicalRunsRoot,
    [System.StringComparison]::OrdinalIgnoreCase
) -or $OutputRoot.StartsWith($RunsPrefix, [System.StringComparison]::OrdinalIgnoreCase)
if ($OutputIsInsideProject -and -not $OutputIsAllowedRunRoot) {
    throw "Project-local perception output must stay under $CanonicalRunsRoot"
}
if (-not $OutputIsInsideProject -and -not $OutputRootWasAbsolute) {
    throw 'OutputRoot outside the project must be an explicit absolute path'
}
if (-not [string]::IsNullOrWhiteSpace($ResumeRun)) {
    $ResumeDirectory = Resolve-SafeOutputPath (Join-Path $OutputRoot $ResumeRun) 'ResumeRunDirectory'
    $ResumePrefix = $OutputRoot.TrimEnd('\') + '\'
    if (-not $ResumeDirectory.StartsWith($ResumePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'ResumeRun escaped OutputRoot.'
    }
    $ResumeStatePath = Join-Path $ResumeDirectory 'run-state.json'
    $ResumeSummaryPath = Join-Path $ResumeDirectory 'gate-summary.json'
    if (-not (Test-Path -LiteralPath $ResumeStatePath -PathType Leaf)) {
        throw "ResumeRun has no canonical run-state.json: $ResumeDirectory"
    }
    if (-not (Test-Path -LiteralPath $ResumeSummaryPath -PathType Leaf)) {
        throw 'ResumeRun is partial. Safe in-place replay is refused; start a fresh run so stale stage bytes cannot be mistaken for evidence.'
    }
    & $HostPython -I -B -X utf8 (Join-Path $ProjectRoot 'tools\run_state.py') status --run-dir $ResumeDirectory
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Output "RESUMED_EXISTING_RUN=$ResumeRun"
    Write-Output "RUN_DIRECTORY=$ResumeDirectory"
    Write-Output "GATE_SUMMARY=$ResumeSummaryPath"
    exit 0
}
$ConfigDirectory = Split-Path -Parent $Config
$AcceptanceFixture = [System.IO.Path]::GetFullPath((Join-Path $ConfigDirectory ([string]$ConfigObject.acceptance_fixture_relative_path)))
if (-not (Test-Path -LiteralPath $AcceptanceFixture -PathType Leaf)) {
    throw "Canonical OCR acceptance fixture is missing: $AcceptanceFixture"
}
$FixtureObject = Get-Content -LiteralPath $AcceptanceFixture -Raw -Encoding UTF8 | ConvertFrom-Json
$FixtureDeclaredHash = ([string]$FixtureObject.sha256).ToUpperInvariant()
if ($FixtureDeclaredHash -notmatch '^[0-9A-F]{64}$') {
    throw "Canonical OCR acceptance fixture has an invalid source SHA-256: $FixtureDeclaredHash"
}
$FixtureReference = [System.IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $AcceptanceFixture) ([string]$FixtureObject.referenceFile)))
if (-not (Test-Path -LiteralPath $FixtureReference -PathType Leaf)) {
    throw "Canonical OCR acceptance reference is missing: $FixtureReference"
}
$FixtureReferenceHash = (Get-FileHash -LiteralPath $FixtureReference -Algorithm SHA256).Hash.ToUpperInvariant()
if ($FixtureReferenceHash -ne $FixtureDeclaredHash) {
    throw "Canonical OCR fixture/reference hash mismatch: declared $FixtureDeclaredHash, got $FixtureReferenceHash"
}
$SourceHash = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash.ToUpperInvariant()
$DefaultSource = $FixtureReference
if ([string]::IsNullOrWhiteSpace($ExpectedSourceSha256) -and
    [string]::Equals($Source, $DefaultSource, [System.StringComparison]::OrdinalIgnoreCase)) {
    $ExpectedSourceSha256 = $FixtureDeclaredHash
}
if (-not [string]::IsNullOrWhiteSpace($ExpectedSourceSha256)) {
    $ExpectedSourceSha256 = $ExpectedSourceSha256.ToUpperInvariant()
    if ($SourceHash -ne $ExpectedSourceSha256) {
        throw "Source hash mismatch: expected $ExpectedSourceSha256, got $SourceHash"
    }
}
$Timestamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$Nonce = [Guid]::NewGuid().ToString('N').Substring(0, 6)
$RunId = "perception-$Timestamp-$($SourceHash.Substring(0, 8).ToLowerInvariant())-$Nonce"
$RunDirectory = Join-Path $OutputRoot $RunId
try {
    # A non-forcing create is the ownership claim. An existing path (including a
    # junction planted by another process) must fail instead of being reused.
    New-Item -ItemType Directory -Path $RunDirectory -ErrorAction Stop | Out-Null
}
catch {
    throw "Refusing to claim a non-fresh perception run: $RunDirectory. $($_.Exception.Message)"
}
$ClaimedRunDirectory = Resolve-SafeOutputPath $RunDirectory 'RunDirectory'
if (-not [string]::Equals(
    $ClaimedRunDirectory,
    [System.IO.Path]::GetFullPath($RunDirectory),
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Run-directory ownership claim resolved to an unexpected path: $ClaimedRunDirectory"
}

$InputDirectory = Join-Path $RunDirectory 'input'
$AnalysisDirectory = Join-Path $RunDirectory 'analysis'
$SegmentDirectory = Join-Path $RunDirectory 'segmentation'
$OcrDirectory = Join-Path $RunDirectory 'ocr'
$GeometryDirectory = Join-Path $RunDirectory 'geometry'
$AgentVisionDirectory = Join-Path $RunDirectory 'agent-vision'
$RuntimeDirectory = Join-Path $RunDirectory 'runtime'
$LogDirectory = Join-Path $RunDirectory 'logs'
$CacheDirectory = Join-Path $RunDirectory 'runtime-cache'
$RunSubdirectories = @(
    $InputDirectory,
    $AnalysisDirectory,
    $SegmentDirectory,
    $OcrDirectory,
    $GeometryDirectory,
    $AgentVisionDirectory,
    $RuntimeDirectory,
    $LogDirectory,
    $CacheDirectory,
    (Join-Path $CacheDirectory 'tmp'),
    (Join-Path $CacheDirectory 'pycache'),
    (Join-Path $CacheDirectory 'paddle'),
    (Join-Path $CacheDirectory 'paddle-extension'),
    (Join-Path $CacheDirectory 'huggingface'),
    (Join-Path $CacheDirectory 'modelscope')
)
foreach ($RunSubdirectory in $RunSubdirectories) {
    try {
        New-Item -ItemType Directory -Path $RunSubdirectory -ErrorAction Stop | Out-Null
    }
    catch {
        throw "Refusing to reuse a pre-existing run subdirectory: $RunSubdirectory. $($_.Exception.Message)"
    }
    $ResolvedRunSubdirectory = Resolve-SafeOutputPath `
        $RunSubdirectory 'RunSubdirectory'
    $ExpectedRunSubdirectory = [System.IO.Path]::GetFullPath($RunSubdirectory)
    if (-not [string]::Equals(
        $ResolvedRunSubdirectory,
        $ExpectedRunSubdirectory,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Run subdirectory resolved to an unexpected path: $ResolvedRunSubdirectory"
    }
}

$Extension = [System.IO.Path]::GetExtension($Source)
if ([string]::IsNullOrWhiteSpace($Extension)) {
    $Extension = '.img'
}
$FrozenSource = Join-Path $InputDirectory ("source" + $Extension.ToLowerInvariant())
Copy-Item -LiteralPath $Source -Destination $FrozenSource
$EvidenceSnapshots = @()
$FrozenSourceSnapshot = Open-ReadOnlyEvidenceSnapshot `
    $FrozenSource 'frozen source'
$EvidenceSnapshots += $FrozenSourceSnapshot
$FrozenHash = [string]$FrozenSourceSnapshot.Sha256
if ($FrozenHash -ne $SourceHash) {
    throw "Frozen source hash mismatch: expected $SourceHash, got $FrozenHash"
}
$RunStateScript = Join-Path $ProjectRoot 'tools\run_state.py'
$RunStateInitLog = Join-Path $LogDirectory 'run-state-init.log'
& $HostPython -I -B -X utf8 $RunStateScript init `
    --run-dir $RunDirectory --source $FrozenSource --source-sha256 $FrozenHash `
    --policy-profile $PolicyProfile *> $RunStateInitLog
if ($LASTEXITCODE -ne 0) {
    throw "Failed to initialize run-state.json. See $RunStateInitLog"
}

# Keep mutable caches inside the isolated run. These settings suppress library download
# fallbacks; they are not an operating-system-level network sandbox.
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPYCACHEPREFIX = Join-Path $CacheDirectory 'pycache'
$env:PYTHONIOENCODING = 'utf-8'
$env:PADDLE_HOME = Join-Path $CacheDirectory 'paddle'
$env:PADDLE_EXTENSION_DIR = Join-Path $CacheDirectory 'paddle-extension'
$env:XDG_CACHE_HOME = $CacheDirectory
$env:HF_HOME = Join-Path $CacheDirectory 'huggingface'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:MODELSCOPE_CACHE = Join-Path $CacheDirectory 'modelscope'
$env:MODELSCOPE_OFFLINE = '1'
$env:PIP_NO_INDEX = '1'
$env:TEMP = Join-Path $CacheDirectory 'tmp'
$env:TMP = Join-Path $CacheDirectory 'tmp'

function Invoke-PythonStage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$Interpreter,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [int[]]$AllowedExitCodes = @(0)
    )

    $LogPath = Join-Path $LogDirectory ($Name + '.log')
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Interpreter -I -B -X utf8 @Arguments *> $LogPath
        $ExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    if ($AllowedExitCodes -notcontains $ExitCode) {
        $Tail = Get-Content -LiteralPath $LogPath -Tail 30 -ErrorAction SilentlyContinue
        throw "Stage '$Name' failed with exit code $ExitCode.`n$($Tail -join [Environment]::NewLine)"
    }
    return $ExitCode
}

$AnalyzeScript = Join-Path $ProjectRoot 'tools\analyze_target.py'
$SegmentScript = Join-Path $ProjectRoot 'tools\segment_panels.py'
$OcrScript = Join-Path $ProjectRoot 'tools\paddle_ocr_manifest.py'
$GeometryScript = Join-Path $ProjectRoot 'tools\geometry_refinement.py'
$GeometrySchema = Join-Path $ProjectRoot 'schemas\geometry-manifest.schema.json'
$HostRuntimeValidator = Join-Path $ProjectRoot 'tools\validate_host_runtime.py'
$HostRuntimeReceipt = Join-Path $RuntimeDirectory 'host-runtime-receipt.json'
foreach ($GeometryContractFile in @($GeometryScript, $GeometrySchema)) {
    if (-not (Test-Path -LiteralPath $GeometryContractFile -PathType Leaf)) {
        throw "Required geometry-refinement contract file is missing: $GeometryContractFile"
    }
}

[void](Invoke-PythonStage -Name 'host-runtime' -Interpreter $HostPython -Arguments @(
    $HostRuntimeValidator,
    '--config',
    $HostRuntimeConfig,
    '--output',
    $HostRuntimeReceipt,
    '--project-root',
    $ProjectRoot,
    '--run-id',
    $RunId,
    '--source-sha256',
    $FrozenHash
))
$HostRuntimeReceiptSnapshot = Open-ReadOnlyEvidenceSnapshot `
    $HostRuntimeReceipt 'host runtime receipt' -IncludeBytes
$EvidenceSnapshots += $HostRuntimeReceiptSnapshot
$HostRuntimeReceiptHash = [string]$HostRuntimeReceiptSnapshot.Sha256
$HostRuntimeReceiptObject = ConvertFrom-Utf8SnapshotJson $HostRuntimeReceiptSnapshot
if ([string]$HostRuntimeReceiptObject.status -ne 'PASS') {
    throw 'Host CV runtime receipt did not pass.'
}
if ([string]$HostRuntimeReceiptObject.context.run_id -ne $RunId -or
    ([string]$HostRuntimeReceiptObject.context.source_sha256).ToUpperInvariant() -ne $FrozenHash) {
    throw 'Host CV runtime receipt context is not bound to the current run/source.'
}
$ExpectedHostPython = [System.IO.Path]::GetFullPath($HostPython)
$ReceiptHostPython = [System.IO.Path]::GetFullPath(
    [string]$HostRuntimeReceiptObject.runtime.python_executable
)
if (-not [string]::Equals(
    $ReceiptHostPython,
    $ExpectedHostPython,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Host runtime receipt used the wrong interpreter: $ReceiptHostPython"
}
if ([string]$HostRuntimeReceiptObject.runtime.python_version -ne
    [string]$HostRuntimeObject.python_version) {
    throw 'Host runtime receipt Python version differs from host-runtime.json.'
}

function Assert-HostStageRuntime([string]$ArtifactPath, [string]$StageName) {
    $Payload = Get-Content -LiteralPath $ArtifactPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (([string]$Payload.source.sha256).ToUpperInvariant() -ne $FrozenHash) {
        throw "$StageName source hash is not bound to the frozen source."
    }
    $StagePython = [System.IO.Path]::GetFullPath([string]$Payload.runtime.python_executable)
    if (-not [string]::Equals(
        $StagePython,
        $ExpectedHostPython,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "$StageName ran under the wrong interpreter: $StagePython"
    }
    if ([string]$Payload.runtime.python -ne [string]$HostRuntimeObject.python_version) {
        throw "$StageName Python version differs from the host runtime contract."
    }
}

if (-not $SkipAnalysis) {
    [void](Invoke-PythonStage -Name 'analyze' -Interpreter $HostPython -Arguments @(
        $AnalyzeScript,
        $FrozenSource,
        '--output',
        $AnalysisDirectory
    ))
    Assert-HostStageRuntime (Join-Path $AnalysisDirectory 'inventory.json') 'analysis'
}

if (-not $SkipSegmentation) {
    [void](Invoke-PythonStage -Name 'segment' -Interpreter $HostPython -Arguments @(
        $SegmentScript,
        $FrozenSource,
        '--output',
        $SegmentDirectory
    ))
    Assert-HostStageRuntime (Join-Path $SegmentDirectory 'panels.json') 'segmentation'
}

$OcrArguments = @(
    $OcrScript,
    $FrozenSource,
    '--config',
    $Config,
    '--output-dir',
    $OcrDirectory,
    '--run-id',
    $RunId,
    '--device',
    $Device,
    '--host-runtime-dir',
    $RuntimeDirectory
)
if (-not $SkipAnalysis) {
    $OcrArguments += @('--analysis-dir', $AnalysisDirectory)
}
if (-not $SkipSegmentation) {
    $OcrArguments += @('--segment-dir', $SegmentDirectory)
}
if ($NoTiles) {
    $OcrArguments += '--no-tiles'
}
if ($NoQuarterTurnReview) {
    $OcrArguments += '--no-quarter-turn-review'
}
if ($SkipAnalysis) {
    $OcrArguments += @('--degraded-reason', 'ANALYSIS_SKIPPED_BY_CALLER')
}
if ($SkipSegmentation) {
    $OcrArguments += @('--degraded-reason', 'SEGMENTATION_SKIPPED_BY_CALLER')
}
$OcrExitCode = Invoke-PythonStage -Name 'ocr' -Interpreter $PaddlePython `
    -Arguments $OcrArguments -AllowedExitCodes @(0, 3)

$ManifestPath = Join-Path $OcrDirectory 'perception-manifest.json'
$ReviewPath = Join-Path $OcrDirectory 'text_review.md'
$OverlayPath = Join-Path $OcrDirectory 'ocr_overlay.png'
foreach ($Artifact in @($ManifestPath, $ReviewPath, $OverlayPath)) {
    if (-not (Test-Path -LiteralPath $Artifact -PathType Leaf)) {
        throw "Required perception artifact is missing: $Artifact"
    }
}
$PerceptionManifestSnapshot = Open-ReadOnlyEvidenceSnapshot `
    $ManifestPath 'OCR perception manifest' -IncludeBytes
$ReviewSnapshot = Open-ReadOnlyEvidenceSnapshot $ReviewPath 'OCR text review'
$OcrOverlaySnapshot = Open-ReadOnlyEvidenceSnapshot $OverlayPath 'OCR overlay'
$EvidenceSnapshots += @(
    $PerceptionManifestSnapshot,
    $ReviewSnapshot,
    $OcrOverlaySnapshot
)
$PerceptionManifestHash = [string]$PerceptionManifestSnapshot.Sha256
$PerceptionManifest = ConvertFrom-Utf8SnapshotJson $PerceptionManifestSnapshot
if ([string]$PerceptionManifest.run_id -ne $RunId) {
    throw "Manifest run_id mismatch: expected $RunId, got $($PerceptionManifest.run_id)"
}
if ([string]$PerceptionManifest.source.sha256 -ne $FrozenHash) {
    throw "Manifest source hash mismatch: expected $FrozenHash, got $($PerceptionManifest.source.sha256)"
}
if ($PerceptionManifest.policy.ocr_is_ground_truth -ne $false) {
    throw 'Manifest violated the OCR truth boundary.'
}
if ([string]$PerceptionManifest.policy.network_access -ne 'NETWORK_NOT_REQUESTED_BY_PIPELINE') {
    throw 'Manifest overclaimed or changed the network-access policy.'
}
$FixtureFileHash = (Get-FileHash -LiteralPath $AcceptanceFixture -Algorithm SHA256).Hash.ToUpperInvariant()
if ([string]$PerceptionManifest.configuration.acceptance_fixture.sha256 -ne $FixtureFileHash) {
    throw 'Manifest acceptance-fixture hash does not match the canonical fixture.'
}
$HostRuntimeStages = @(
    $PerceptionManifest.upstream_stages | Where-Object { [string]$_.name -eq 'host_runtime' }
)
if ($HostRuntimeStages.Count -ne 1) {
    throw 'Manifest must bind exactly one host_runtime upstream stage.'
}
$HostRuntimeRecords = @(
    $HostRuntimeStages[0].files | Where-Object {
        [string]$_.relative_path -eq 'host-runtime-receipt.json'
    }
)
if ($HostRuntimeRecords.Count -ne 1) {
    throw 'Manifest host_runtime stage is missing the canonical runtime receipt.'
}
if (([string]$HostRuntimeRecords[0].sha256).ToUpperInvariant() -ne $HostRuntimeReceiptHash) {
    throw 'Manifest host runtime receipt hash does not match the current receipt.'
}
$ManifestStatus = [string]$PerceptionManifest.status
if ($OcrExitCode -eq 0 -and $ManifestStatus -ne 'OCR_HYPOTHESES_REVIEW_REQUIRED') {
    throw "OCR exit/status mismatch: exit=0 status=$ManifestStatus"
}
if ($OcrExitCode -eq 3 -and $ManifestStatus -ne 'OCR_HYPOTHESES_INCONCLUSIVE') {
    throw "OCR exit/status mismatch: exit=3 status=$ManifestStatus"
}

$GeometryScriptSnapshot = Open-ReadOnlyEvidenceSnapshot `
    $GeometryScript 'geometry implementation script'
$GeometrySchemaSnapshot = Open-ReadOnlyEvidenceSnapshot `
    $GeometrySchema 'geometry manifest schema'
$EvidenceSnapshots += @($GeometryScriptSnapshot, $GeometrySchemaSnapshot)
$GeometryScriptHash = [string]$GeometryScriptSnapshot.Sha256
$GeometrySchemaHash = [string]$GeometrySchemaSnapshot.Sha256

$GeometryExitCode = Invoke-PythonStage -Name 'geometry' -Interpreter $HostPython `
    -AllowedExitCodes @(0, 3) -Arguments @(
    $GeometryScript,
    '--input',
    $FrozenSource,
    '--ocr-manifest',
    $ManifestPath,
    '--host-runtime-receipt',
    $HostRuntimeReceipt,
    '--output-dir',
    $GeometryDirectory,
    '--project-root',
    $ProjectRoot
)

$GeometryManifestPath = Join-Path $GeometryDirectory 'geometry-manifest.json'
$GeometryOverlayPath = Join-Path $GeometryDirectory 'geometry-overlay.png'
$GeometryLabelAtlasPath = Join-Path $GeometryDirectory 'geometry-label-atlas.png'
$GeometryAmbiguityMaskPath = Join-Path $GeometryDirectory 'geometry-ambiguity-mask.png'
$GeometryArtifacts = @(
    $GeometryManifestPath,
    $GeometryOverlayPath,
    $GeometryLabelAtlasPath,
    $GeometryAmbiguityMaskPath
)
foreach ($Artifact in $GeometryArtifacts) {
    if (-not (Test-Path -LiteralPath $Artifact -PathType Leaf)) {
        throw "Required geometry-refinement artifact is missing: $Artifact"
    }
}

$GeometryManifestSnapshot = Open-ReadOnlyEvidenceSnapshot `
    $GeometryManifestPath 'geometry manifest' -IncludeBytes
$GeometryOverlaySnapshot = Open-ReadOnlyEvidenceSnapshot `
    $GeometryOverlayPath 'geometry overlay'
$GeometryLabelAtlasSnapshot = Open-ReadOnlyEvidenceSnapshot `
    $GeometryLabelAtlasPath 'geometry label atlas'
$GeometryAmbiguityMaskSnapshot = Open-ReadOnlyEvidenceSnapshot `
    $GeometryAmbiguityMaskPath 'geometry ambiguity mask'
$EvidenceSnapshots += @(
    $GeometryManifestSnapshot,
    $GeometryOverlaySnapshot,
    $GeometryLabelAtlasSnapshot,
    $GeometryAmbiguityMaskSnapshot
)
$GeometryManifestHash = [string]$GeometryManifestSnapshot.Sha256
$GeometryOverlayHash = [string]$GeometryOverlaySnapshot.Sha256
$GeometryLabelAtlasHash = [string]$GeometryLabelAtlasSnapshot.Sha256
$GeometryAmbiguityMaskHash = [string]$GeometryAmbiguityMaskSnapshot.Sha256
[void](Invoke-PythonStage -Name 'geometry-contract-verify' -Interpreter $HostPython `
    -Arguments @($GeometryScript, '--verify-manifest', $GeometryManifestPath))
$GeometryManifest = ConvertFrom-Utf8SnapshotJson $GeometryManifestSnapshot
$GeometryStatus = [string]$GeometryManifest.status
if (@('GEOMETRY_OBSERVATIONS_READY', 'GEOMETRY_INCONCLUSIVE') -cnotcontains $GeometryStatus) {
    throw "Geometry manifest has an invalid status: $GeometryStatus"
}
if ($GeometryExitCode -eq 0 -and $GeometryStatus -cne 'GEOMETRY_OBSERVATIONS_READY') {
    throw "Geometry exit/status mismatch: exit=0 status=$GeometryStatus"
}
if ($GeometryExitCode -eq 3 -and $GeometryStatus -cne 'GEOMETRY_INCONCLUSIVE') {
    throw "Geometry exit/status mismatch: exit=3 status=$GeometryStatus"
}
if ([string]$GeometryManifest.mode -cne 'observation_only') {
    throw 'Geometry manifest must remain observation_only.'
}
if ([string]$GeometryManifest.schema_version -cne '1.0.0') {
    throw 'Geometry manifest schema_version is not the supported Phase-1 contract.'
}
if ([string]$GeometryManifest.coordinate_system.origin -cne 'TOP_LEFT' -or
    [string]$GeometryManifest.coordinate_system.unit -cne 'SOURCE_PIXEL' -or
    [string]$GeometryManifest.coordinate_system.box_convention -cne
    'HALF_OPEN_X0_Y0_X1_Y1' -or
    [string]$GeometryManifest.coordinate_system.pixel_distance_reference -cne
    'PIXEL_CENTER_EUCLIDEAN') {
    throw 'Geometry manifest coordinate-system contract mismatch.'
}
if ($GeometryManifest.policy.promotion_allowed -isnot [bool] -or
    $GeometryManifest.policy.promotion_allowed -ne $false) {
    throw 'Phase-1 geometry observations cannot authorize promotion.'
}
if ([string]$GeometryManifest.run_id -ne $RunId) {
    throw "Geometry manifest run_id mismatch: expected $RunId, got $($GeometryManifest.run_id)"
}
if (([string]$GeometryManifest.source.sha256).ToUpperInvariant() -ne $FrozenHash) {
    throw 'Geometry manifest source hash is not bound to the frozen source.'
}
$GeometrySourcePath = [System.IO.Path]::GetFullPath([string]$GeometryManifest.source.path)
if (-not [string]::Equals(
    $GeometrySourcePath,
    [System.IO.Path]::GetFullPath($FrozenSource),
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw 'Geometry manifest source path is not the frozen source.'
}
if ([long]$GeometryManifest.source.size_bytes -ne
    [long]$FrozenSourceSnapshot.SizeBytes) {
    throw 'Geometry manifest source size does not match the frozen source.'
}
if ([int]$GeometryManifest.source.width_px -ne [int]$PerceptionManifest.source.width_px -or
    [int]$GeometryManifest.source.height_px -ne [int]$PerceptionManifest.source.height_px -or
    [string]$GeometryManifest.source.pixel_mode -cne [string]$PerceptionManifest.source.pixel_mode -or
    [string]$GeometryManifest.source.format -cne 'PNG') {
    throw 'Geometry manifest source metadata differs from the OCR/frozen source.'
}
$GeometryPython = [System.IO.Path]::GetFullPath(
    [string]$GeometryManifest.runtime.python_executable
)
if (-not [string]::Equals(
    $GeometryPython,
    $ExpectedHostPython,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Geometry refinement ran under the wrong interpreter: $GeometryPython"
}
if ([string]$GeometryManifest.runtime.python_version -ne
    [string]$HostRuntimeObject.python_version) {
    throw 'Geometry refinement Python version differs from the host runtime contract.'
}
if ([string]$GeometryManifest.runtime.runtime_id -cne
    [string]$HostRuntimeReceiptObject.runtime.runtime_id) {
    throw 'Geometry refinement runtime_id differs from the host runtime receipt.'
}
if ($GeometryManifest.runtime.isolated -isnot [bool] -or
    $GeometryManifest.runtime.isolated -ne $true) {
    throw 'Geometry refinement did not record isolated Host Python execution.'
}
if (([string]$GeometryManifest.inputs.ocr_manifest.sha256).ToUpperInvariant() -ne
    $PerceptionManifestHash) {
    throw 'Geometry manifest is not bound to the current OCR manifest.'
}
$GeometryOcrInputPath = [System.IO.Path]::GetFullPath(
    [string]$GeometryManifest.inputs.ocr_manifest.path
)
if (-not [string]::Equals(
    $GeometryOcrInputPath,
    [System.IO.Path]::GetFullPath($ManifestPath),
    [System.StringComparison]::OrdinalIgnoreCase
) -or [long]$GeometryManifest.inputs.ocr_manifest.size_bytes -ne
    [long]$PerceptionManifestSnapshot.SizeBytes -or
    [string]$GeometryManifest.inputs.ocr_manifest.schema_version -cne
    [string]$PerceptionManifest.schema_version -or
    [string]$GeometryManifest.inputs.ocr_manifest.run_id -cne $RunId -or
    ([string]$GeometryManifest.inputs.ocr_manifest.source_sha256).ToUpperInvariant() -ne
    $FrozenHash) {
    throw 'Geometry OCR input binding is incomplete or stale.'
}
if (([string]$GeometryManifest.inputs.host_runtime_receipt.sha256).ToUpperInvariant() -ne
    $HostRuntimeReceiptHash) {
    throw 'Geometry manifest is not bound to the current host runtime receipt.'
}
$GeometryReceiptInputPath = [System.IO.Path]::GetFullPath(
    [string]$GeometryManifest.inputs.host_runtime_receipt.path
)
if (-not [string]::Equals(
    $GeometryReceiptInputPath,
    [System.IO.Path]::GetFullPath($HostRuntimeReceipt),
    [System.StringComparison]::OrdinalIgnoreCase
) -or [long]$GeometryManifest.inputs.host_runtime_receipt.size_bytes -ne
    [long]$HostRuntimeReceiptSnapshot.SizeBytes -or
    [string]$GeometryManifest.inputs.host_runtime_receipt.schema_version -cne
    [string]$HostRuntimeReceiptObject.schema_version -or
    [string]$GeometryManifest.inputs.host_runtime_receipt.status -cne 'PASS' -or
    [string]$GeometryManifest.inputs.host_runtime_receipt.context.run_id -cne $RunId -or
    ([string]$GeometryManifest.inputs.host_runtime_receipt.context.source_sha256).ToUpperInvariant() -ne
    $FrozenHash -or
    [string]$GeometryManifest.inputs.host_runtime_receipt.runtime.runtime_id -cne
    [string]$HostRuntimeReceiptObject.runtime.runtime_id -or
    -not [string]::Equals(
        [System.IO.Path]::GetFullPath(
            [string]$GeometryManifest.inputs.host_runtime_receipt.runtime.python_executable
        ),
        $ExpectedHostPython,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    [string]$GeometryManifest.inputs.host_runtime_receipt.runtime.python_version -cne
    [string]$HostRuntimeObject.python_version) {
    throw 'Geometry host runtime receipt binding is incomplete or stale.'
}
if (([string]$GeometryManifest.implementation.script.sha256).ToUpperInvariant() -ne
    $GeometryScriptHash) {
    throw 'Geometry manifest script hash does not match the current implementation.'
}
if (([string]$GeometryManifest.implementation.schema.sha256).ToUpperInvariant() -ne
    $GeometrySchemaHash) {
    throw 'Geometry manifest schema hash does not match the current contract.'
}
$GeometryImplementationExpectations = [ordered]@{
    script = [ordered]@{
        path = $GeometryScript
        relative_path = 'tools/geometry_refinement.py'
        size_bytes = [long]$GeometryScriptSnapshot.SizeBytes
    }
    schema = [ordered]@{
        path = $GeometrySchema
        relative_path = 'schemas/geometry-manifest.schema.json'
        size_bytes = [long]$GeometrySchemaSnapshot.SizeBytes
    }
}
foreach ($ImplementationName in $GeometryImplementationExpectations.Keys) {
    $ExpectedImplementation = $GeometryImplementationExpectations[$ImplementationName]
    $ImplementationRecord = $GeometryManifest.implementation.PSObject.Properties[
        $ImplementationName
    ].Value
    if (-not [string]::Equals(
        [System.IO.Path]::GetFullPath([string]$ImplementationRecord.path),
        [System.IO.Path]::GetFullPath([string]$ExpectedImplementation.path),
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or [string]$ImplementationRecord.relative_path -cne
        [string]$ExpectedImplementation.relative_path -or
        [long]$ImplementationRecord.size_bytes -ne
        [long]$ExpectedImplementation.size_bytes) {
        throw "Geometry implementation '$ImplementationName' path/size binding is stale."
    }
}

function Assert-ExactJsonPropertySet(
    [object]$Value,
    [string[]]$ExpectedNames,
    [string]$Label
) {
    $ActualNames = @($Value.PSObject.Properties.Name | Sort-Object)
    $SortedExpected = @($ExpectedNames | Sort-Object)
    if ($ActualNames.Count -ne $SortedExpected.Count) {
        throw "$Label property set mismatch: expected $($SortedExpected -join ','), got $($ActualNames -join ',')"
    }
    for ($Index = 0; $Index -lt $SortedExpected.Count; $Index++) {
        if ([string]$ActualNames[$Index] -cne [string]$SortedExpected[$Index]) {
            throw "$Label property set mismatch: expected $($SortedExpected -join ','), got $($ActualNames -join ',')"
        }
    }
}

$GeometryPolicyExpectations = [ordered]@{
    promotion_allowed = $false
    human_review_required = $true
    ocr_text_is_ground_truth = $false
    ink_bottom_alignment_is_font_baseline = $false
    frame_semantics_verified = $false
    arrow_detection_performed = $false
}
Assert-ExactJsonPropertySet $GeometryManifest.policy `
    @($GeometryPolicyExpectations.Keys) 'geometry policy'
foreach ($PolicyName in $GeometryPolicyExpectations.Keys) {
    $PolicyValue = $GeometryManifest.policy.PSObject.Properties[$PolicyName].Value
    if ($PolicyValue -isnot [bool] -or
        $PolicyValue -ne [bool]$GeometryPolicyExpectations[$PolicyName]) {
        throw "Geometry observation-only policy '$PolicyName' mismatch."
    }
}

Assert-ExactJsonPropertySet $GeometryManifest.artifacts `
    @('overlay', 'label_atlas', 'ambiguity_mask') 'geometry artifacts'
$GeometryArtifactExpectations = [ordered]@{
    overlay = [ordered]@{
        path = $GeometryOverlayPath
        relative_path = 'geometry-overlay.png'
        sha256 = $GeometryOverlayHash
        size_bytes = [long]$GeometryOverlaySnapshot.SizeBytes
        encoding = 'rgb8_png'
        extra_name = $null
        extra_value = $null
    }
    label_atlas = [ordered]@{
        path = $GeometryLabelAtlasPath
        relative_path = 'geometry-label-atlas.png'
        sha256 = $GeometryLabelAtlasHash
        size_bytes = [long]$GeometryLabelAtlasSnapshot.SizeBytes
        encoding = 'uint16_label_png'
        extra_name = 'background_label'
        extra_value = 0
    }
    ambiguity_mask = [ordered]@{
        path = $GeometryAmbiguityMaskPath
        relative_path = 'geometry-ambiguity-mask.png'
        sha256 = $GeometryAmbiguityMaskHash
        size_bytes = [long]$GeometryAmbiguityMaskSnapshot.SizeBytes
        encoding = 'uint8_binary_png'
        extra_name = 'ambiguous_value'
        extra_value = 255
    }
}
foreach ($ArtifactName in $GeometryArtifactExpectations.Keys) {
    $ExpectedArtifact = $GeometryArtifactExpectations[$ArtifactName]
    $ArtifactRecord = $GeometryManifest.artifacts.PSObject.Properties[$ArtifactName].Value
    $ExpectedProperties = @(
        'relative_path',
        'size_bytes',
        'sha256',
        'media_type',
        'width_px',
        'height_px',
        'encoding'
    )
    if (-not [string]::IsNullOrWhiteSpace([string]$ExpectedArtifact.extra_name)) {
        $ExpectedProperties += [string]$ExpectedArtifact.extra_name
    }
    Assert-ExactJsonPropertySet $ArtifactRecord $ExpectedProperties `
        "geometry artifact '$ArtifactName'"
    if ([string]$ArtifactRecord.relative_path -cne [string]$ExpectedArtifact.relative_path) {
        throw "Geometry artifact '$ArtifactName' has the wrong relative path."
    }
    $ResolvedArtifactPath = [System.IO.Path]::GetFullPath(
        (Join-Path $GeometryDirectory ([string]$ArtifactRecord.relative_path))
    )
    if (-not [string]::Equals(
        $ResolvedArtifactPath,
        [System.IO.Path]::GetFullPath([string]$ExpectedArtifact.path),
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Geometry artifact '$ArtifactName' escaped its canonical path."
    }
    if ([long]$ArtifactRecord.size_bytes -ne
        [long]$ExpectedArtifact.size_bytes) {
        throw "Geometry artifact '$ArtifactName' size does not match its manifest record."
    }
    if (([string]$ArtifactRecord.sha256).ToUpperInvariant() -ne
        [string]$ExpectedArtifact.sha256) {
        throw "Geometry artifact '$ArtifactName' hash does not match its manifest record."
    }
    if ([string]$ArtifactRecord.media_type -cne 'image/png' -or
        [string]$ArtifactRecord.encoding -cne [string]$ExpectedArtifact.encoding) {
        throw "Geometry artifact '$ArtifactName' media contract mismatch."
    }
    if ([int]$ArtifactRecord.width_px -ne [int]$PerceptionManifest.source.width_px -or
        [int]$ArtifactRecord.height_px -ne [int]$PerceptionManifest.source.height_px) {
        throw "Geometry artifact '$ArtifactName' dimensions differ from the frozen source."
    }
    if (-not [string]::IsNullOrWhiteSpace([string]$ExpectedArtifact.extra_name)) {
        $ActualExtraValue = $ArtifactRecord.PSObject.Properties[
            [string]$ExpectedArtifact.extra_name
        ].Value
        if ([int]$ActualExtraValue -ne [int]$ExpectedArtifact.extra_value) {
            throw "Geometry artifact '$ArtifactName' encoding sentinel mismatch."
        }
    }
}

$GeometrySummaryFields = @(
    'candidate_count',
    'measured_ink_count',
    'inconclusive_ink_count',
    'reliable_ink_bottom_alignment_count',
    'neighbor_pair_count',
    'frame_candidate_count',
    'measured_frame_count',
    'ambiguous_pixel_count',
    'degradations'
)
Assert-ExactJsonPropertySet $GeometryManifest.summary $GeometrySummaryFields `
    'geometry summary'
foreach ($CountName in ($GeometrySummaryFields | Where-Object { $_ -ne 'degradations' })) {
    $CountValue = $GeometryManifest.summary.PSObject.Properties[$CountName].Value
    if ($CountValue -isnot [int] -and $CountValue -isnot [long]) {
        throw "Geometry summary '$CountName' must be an integer."
    }
    if ([long]$CountValue -lt 0) {
        throw "Geometry summary '$CountName' cannot be negative."
    }
}
$GeometrySummaryDegradations = @($GeometryManifest.summary.degradations)
foreach ($Degradation in $GeometrySummaryDegradations) {
    if ($Degradation -isnot [string] -or [string]::IsNullOrWhiteSpace($Degradation)) {
        throw 'Geometry summary degradations must contain non-empty strings only.'
    }
}
if (@($GeometrySummaryDegradations | Sort-Object -Unique).Count -ne
    $GeometrySummaryDegradations.Count) {
    throw 'Geometry summary degradations must be unique.'
}
$GeometryTopLevelDegradations = @($GeometryManifest.degradations)
if ($GeometryTopLevelDegradations.Count -ne $GeometrySummaryDegradations.Count) {
    throw 'Geometry summary/top-level degradations differ.'
}
for ($Index = 0; $Index -lt $GeometrySummaryDegradations.Count; $Index++) {
    if ([string]$GeometrySummaryDegradations[$Index] -cne
        [string]$GeometryTopLevelDegradations[$Index]) {
        throw 'Geometry summary/top-level degradations differ.'
    }
}
if ($GeometryStatus -ceq 'GEOMETRY_INCONCLUSIVE' -and
    $GeometrySummaryDegradations.Count -eq 0) {
    throw 'GEOMETRY_INCONCLUSIVE requires an explicit degradation reason.'
}
$DerivedGeometryCounts = [ordered]@{
    candidate_count = @($GeometryManifest.text_geometry).Count
    measured_ink_count = @(
        $GeometryManifest.text_geometry |
            Where-Object { [string]$_.status -ceq 'MEASURED' }
    ).Count
    inconclusive_ink_count = @(
        $GeometryManifest.text_geometry |
            Where-Object { [string]$_.status -ceq 'INCONCLUSIVE' }
    ).Count
    reliable_ink_bottom_alignment_count = @(
        $GeometryManifest.text_geometry |
            Where-Object { [string]$_.baseline.status -ceq 'MEASURED' }
    ).Count
    neighbor_pair_count = @($GeometryManifest.neighbor_pairs).Count
    frame_candidate_count = @($GeometryManifest.frame_candidates).Count
    measured_frame_count = @(
        $GeometryManifest.frame_candidates |
            Where-Object { [string]$_.status -ceq 'MEASURED' }
    ).Count
}
$OcrCandidateIds = @($PerceptionManifest.text_candidates | ForEach-Object { [string]$_.candidate_id })
$GeometryCandidateIds = @($GeometryManifest.text_geometry | ForEach-Object { [string]$_.candidate_id })
if ($OcrCandidateIds.Count -ne $GeometryCandidateIds.Count) {
    throw 'Geometry text records do not cover the OCR candidate sequence.'
}
for ($Index = 0; $Index -lt $OcrCandidateIds.Count; $Index++) {
    if ([string]$OcrCandidateIds[$Index] -cne [string]$GeometryCandidateIds[$Index]) {
        throw 'Geometry text records do not preserve the OCR candidate sequence.'
    }
}
foreach ($CountName in $DerivedGeometryCounts.Keys) {
    $DeclaredCount = [long]$GeometryManifest.summary.PSObject.Properties[$CountName].Value
    $DerivedCount = [long]$DerivedGeometryCounts[$CountName]
    if ($DeclaredCount -ne $DerivedCount) {
        throw "Geometry summary '$CountName' mismatch: declared $DeclaredCount, derived $DerivedCount."
    }
}
if ([long]$DerivedGeometryCounts.candidate_count -ne
    [long](@($PerceptionManifest.text_candidates).Count)) {
    throw 'Geometry summary candidate_count differs from the OCR manifest.'
}
if ([long]$DerivedGeometryCounts.candidate_count -ne
    ([long]$DerivedGeometryCounts.measured_ink_count +
     [long]$DerivedGeometryCounts.inconclusive_ink_count)) {
    throw 'Geometry text records are not partitioned into MEASURED/INCONCLUSIVE.'
}

# Agent-vision task package: the protocolized hand-off point for the outer
# agent's native-vision observations. The stage is an enhancement layer — its
# INCONCLUSIVE never downgrades the perception gate status.
$AgentVisionScript = Join-Path $ProjectRoot 'tools\prepare_agent_vision_task.py'
$AgentVisionConfig = Join-Path $ProjectRoot 'agent-vision-config.json'
$AgentVisionTaskPackagePath = Join-Path $AgentVisionDirectory 'task-package.json'
$AgentVisionInstructionsPath = Join-Path $AgentVisionDirectory 'INSTRUCTIONS.md'
$AgentVisionStageStatus = 'SKIPPED'
$AgentVisionPkgRan = $false
$AgentVisionTaskPackageHash = $null
$AgentVisionTaskPackageArtifact = $null
if (-not $SkipAgentVisionPkg) {
    foreach ($AgentVisionContractFile in @($AgentVisionScript, $AgentVisionConfig)) {
        if (-not (Test-Path -LiteralPath $AgentVisionContractFile -PathType Leaf)) {
            throw "Required agent-vision contract file is missing: $AgentVisionContractFile"
        }
    }
    $AgentVisionArguments = @(
        $AgentVisionScript,
        '--input',
        $FrozenSource,
        '--ocr-manifest',
        $ManifestPath,
        '--geometry-manifest',
        $GeometryManifestPath,
        '--host-runtime-receipt',
        $HostRuntimeReceipt,
        '--config',
        $AgentVisionConfig,
        '--output-dir',
        $AgentVisionDirectory,
        '--run-id',
        $RunId,
        '--project-root',
        $ProjectRoot
    )
    if (-not $SkipSegmentation) {
        $AgentVisionArguments += @('--segment-dir', $SegmentDirectory)
    }
    else {
        $AgentVisionArguments += @('--degraded-reason', 'SEGMENTATION_SKIPPED_BY_CALLER')
    }
    $AgentVisionPkgExitCode = Invoke-PythonStage -Name 'agent-vision-pkg' `
        -Interpreter $HostPython -Arguments $AgentVisionArguments -AllowedExitCodes @(0, 3)
    $AgentVisionPkgRan = $true
    if ($AgentVisionPkgExitCode -eq 0) {
        foreach ($AgentVisionArtifact in @($AgentVisionTaskPackagePath, $AgentVisionInstructionsPath)) {
            if (-not (Test-Path -LiteralPath $AgentVisionArtifact -PathType Leaf)) {
                throw "Required agent-vision artifact is missing: $AgentVisionArtifact"
            }
        }
        $AgentVisionTaskPackageSnapshot = Open-ReadOnlyEvidenceSnapshot `
            $AgentVisionTaskPackagePath 'agent-vision task package' -IncludeBytes
        $EvidenceSnapshots += $AgentVisionTaskPackageSnapshot
        $AgentVisionTaskPackageHash = [string]$AgentVisionTaskPackageSnapshot.Sha256
        $AgentVisionTaskPackage = ConvertFrom-Utf8SnapshotJson $AgentVisionTaskPackageSnapshot
        if ([string]$AgentVisionTaskPackage.run_id -cne $RunId) {
            throw "Agent-vision task package run_id mismatch: expected $RunId"
        }
        if ([string]$AgentVisionTaskPackage.source.sha256 -cne $FrozenHash) {
            throw 'Agent-vision task package is not bound to the frozen source.'
        }
        if (([string]$AgentVisionTaskPackage.inputs.ocr_manifest.sha256).ToUpperInvariant() -ne
            $PerceptionManifestHash) {
            throw 'Agent-vision task package is not bound to the current OCR manifest.'
        }
        if (([string]$AgentVisionTaskPackage.inputs.geometry_manifest.sha256).ToUpperInvariant() -ne
            $GeometryManifestHash) {
            throw 'Agent-vision task package is not bound to the current geometry manifest.'
        }
        if ($AgentVisionTaskPackage.policy.vlm_is_ground_truth -isnot [bool] -or
            $AgentVisionTaskPackage.policy.vlm_is_ground_truth -ne $false) {
            throw 'Agent-vision task package violated the vision truth boundary.'
        }
        $AgentVisionDirectoryPrefix = [System.IO.Path]::GetFullPath(
            $AgentVisionDirectory
        ).TrimEnd([System.IO.Path]::DirectorySeparatorChar) +
            [System.IO.Path]::DirectorySeparatorChar
        foreach ($AgentVisionQuery in @($AgentVisionTaskPackage.queries)) {
            $AgentVisionCropPath = [System.IO.Path]::GetFullPath(
                (Join-Path $AgentVisionDirectory ([string]$AgentVisionQuery.image.relative_path))
            )
            if (-not $AgentVisionCropPath.StartsWith(
                $AgentVisionDirectoryPrefix,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
                throw "Agent-vision crop escaped its stage directory: $AgentVisionCropPath"
            }
            if (-not (Test-Path -LiteralPath $AgentVisionCropPath -PathType Leaf)) {
                throw "Agent-vision crop is missing: $AgentVisionCropPath"
            }
        }
        [void](Invoke-PythonStage -Name 'agent-vision-contract-verify' `
            -Interpreter $HostPython `
            -Arguments @($AgentVisionScript, '--verify-package', $AgentVisionTaskPackagePath))
        $AgentVisionStageStatus = 'COMPLETE_TASK_PACKAGE_READY'
        $AgentVisionTaskPackageArtifact = [ordered]@{
            path = $AgentVisionTaskPackagePath
            sha256 = $AgentVisionTaskPackageHash
        }
    }
    else {
        $AgentVisionStageStatus = 'INCONCLUSIVE'
    }
}

foreach ($EvidenceSnapshot in $EvidenceSnapshots) {
    Assert-EvidenceSnapshotUnchanged $EvidenceSnapshot
}

$GateStatus = if (
    $ManifestStatus -eq 'OCR_HYPOTHESES_REVIEW_REQUIRED' -and
    $GeometryStatus -eq 'GEOMETRY_OBSERVATIONS_READY'
) {
    'PERCEPTION_REVIEW_REQUIRED'
}
else {
    'PERCEPTION_INCONCLUSIVE'
}

$RunPrefix = [System.IO.Path]::GetFullPath($RunDirectory).TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar
) + [System.IO.Path]::DirectorySeparatorChar
$RemovedRuntimeCaches = @()
foreach ($RuntimeCache in @($CacheDirectory, (Join-Path $OcrDirectory 'runtime-cache'))) {
    $RuntimeCacheFull = [System.IO.Path]::GetFullPath($RuntimeCache)
    if (-not $RuntimeCacheFull.StartsWith(
        $RunPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Runtime-cache cleanup target escaped the owned run: $RuntimeCacheFull"
    }
    if (Test-Path -LiteralPath $RuntimeCacheFull) {
        Remove-Item -LiteralPath $RuntimeCacheFull -Recurse -Force -ErrorAction Stop
        $RemovedRuntimeCaches += $RuntimeCacheFull
    }
}

$HostCvStages = @('host-runtime', 'analysis', 'segmentation', 'geometry')
if ($AgentVisionPkgRan) {
    $HostCvStages += 'agent-vision-pkg'
}

$Summary = [ordered]@{
    schema_version = '1.3.0'
    run_id = $RunId
    status = $GateStatus
    policy_profile = $PolicyProfile
    created_at_utc = [DateTime]::UtcNow.ToString('o')
    source = [ordered]@{
        original_path = $Source
        frozen_path = $FrozenSource
        sha256 = $FrozenHash
    }
    stages = [ordered]@{
        host_runtime = 'COMPLETE'
        analysis = if ($SkipAnalysis) { 'SKIPPED' } else { 'COMPLETE' }
        segmentation = if ($SkipSegmentation) { 'SKIPPED' } else { 'COMPLETE' }
        ocr = if ($ManifestStatus -eq 'OCR_HYPOTHESES_REVIEW_REQUIRED') {
            'COMPLETE_REVIEW_REQUIRED'
        } else {
            'INCONCLUSIVE'
        }
        geometry = if ($GeometryStatus -eq 'GEOMETRY_OBSERVATIONS_READY') {
            'COMPLETE_OBSERVATION_ONLY'
        } else {
            'INCONCLUSIVE'
        }
        agent_vision_pkg = $AgentVisionStageStatus
    }
    runtime_bindings = [ordered]@{
        host_cv = [ordered]@{
            interpreter = $ExpectedHostPython
            config = $HostRuntimeConfig
            config_sha256 = (Get-FileHash -LiteralPath $HostRuntimeConfig -Algorithm SHA256).Hash.ToUpperInvariant()
            receipt = $HostRuntimeReceipt
            receipt_sha256 = $HostRuntimeReceiptHash
            stages = $HostCvStages
        }
        paddle_ocr = [ordered]@{
            interpreter = [System.IO.Path]::GetFullPath($PaddlePython)
            config = $Config
            config_sha256 = (Get-FileHash -LiteralPath $Config -Algorithm SHA256).Hash.ToUpperInvariant()
            stages = @('ocr')
        }
    }
    geometry_binding = [ordered]@{
        stage = 'geometry_refinement'
        status = $GeometryStatus
        mode = [string]$GeometryManifest.mode
        promotion_allowed = $false
        implementation = [ordered]@{
            script = [ordered]@{
                path = $GeometryScript
                sha256 = $GeometryScriptHash
            }
            schema = [ordered]@{
                path = $GeometrySchema
                sha256 = $GeometrySchemaHash
            }
        }
        inputs = [ordered]@{
            perception_manifest_sha256 = $PerceptionManifestHash
            host_runtime_receipt_sha256 = $HostRuntimeReceiptHash
        }
    }
    maintenance = [ordered]@{
        ephemeral_runtime_cache_policy = 'REMOVED_AFTER_STAGE'
        removed_paths = $RemovedRuntimeCaches
    }
    artifacts = [ordered]@{
        host_runtime_receipt = [ordered]@{
            path = $HostRuntimeReceipt
            sha256 = $HostRuntimeReceiptHash
        }
        perception_manifest = [ordered]@{
            path = $ManifestPath
            sha256 = $PerceptionManifestHash
        }
        text_review = [ordered]@{
            path = $ReviewPath
            sha256 = [string]$ReviewSnapshot.Sha256
        }
        overlay = [ordered]@{
            path = $OverlayPath
            sha256 = [string]$OcrOverlaySnapshot.Sha256
        }
        geometry_manifest = [ordered]@{
            path = $GeometryManifestPath
            sha256 = $GeometryManifestHash
        }
        geometry_overlay = [ordered]@{
            path = $GeometryOverlayPath
            sha256 = $GeometryOverlayHash
        }
        geometry_label_atlas = [ordered]@{
            path = $GeometryLabelAtlasPath
            sha256 = $GeometryLabelAtlasHash
        }
        geometry_ambiguity_mask = [ordered]@{
            path = $GeometryAmbiguityMaskPath
            sha256 = $GeometryAmbiguityMaskHash
        }
    }
}
if ($null -ne $AgentVisionTaskPackageArtifact) {
    $Summary['artifacts']['agent_vision_task_package'] = $AgentVisionTaskPackageArtifact
    $Summary['artifacts']['agent_vision_instructions'] = [ordered]@{
        path = $AgentVisionInstructionsPath
    }
}
foreach ($EvidenceSnapshot in $EvidenceSnapshots) {
    Assert-EvidenceSnapshotUnchanged $EvidenceSnapshot
}
$SummaryPath = Join-Path $RunDirectory 'gate-summary.json'
$TemporarySummary = Join-Path $RunDirectory ('.gate-summary.' + [Guid]::NewGuid().ToString('N') + '.tmp')
$SummaryJson = $Summary | ConvertTo-Json -Depth 8
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($TemporarySummary, $SummaryJson + [Environment]::NewLine, $Utf8NoBom)
[System.IO.File]::Move($TemporarySummary, $SummaryPath)

$RunStateAdvanceLog = Join-Path $LogDirectory 'run-state-perception-complete.log'
& $HostPython -I -B -X utf8 $RunStateScript advance `
    --run-dir $RunDirectory --to-state PERCEPTION_COMPLETE --actor runner `
    --stage perception --evidence $SummaryPath `
    --note 'Canonical OCR, Phase-1 geometry, and task-package stages completed.' `
    *> $RunStateAdvanceLog
if ($LASTEXITCODE -ne 0) {
    throw "Failed to advance run-state.json. See $RunStateAdvanceLog"
}

Write-Output "RUN_ID=$RunId"
Write-Output "RUN_DIRECTORY=$RunDirectory"
Write-Output "MANIFEST=$ManifestPath"
Write-Output "TEXT_REVIEW=$ReviewPath"
Write-Output "OVERLAY=$OverlayPath"
Write-Output "GEOMETRY_MANIFEST=$GeometryManifestPath"
Write-Output "GEOMETRY_OVERLAY=$GeometryOverlayPath"
if ($AgentVisionStageStatus -eq 'COMPLETE_TASK_PACKAGE_READY') {
    Write-Output "AGENT_VISION_TASK=$AgentVisionTaskPackagePath"
    Write-Output "AGENT_VISION_INSTRUCTIONS=$AgentVisionInstructionsPath"
}
if ($GateStatus -eq 'PERCEPTION_INCONCLUSIVE') {
    exit 3
}
