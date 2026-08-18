[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedPlanPath,

    [Parameter(Mandatory = $true)]
    [string]$InjectionReportPath,

    [Parameter(Mandatory = $true)]
    [string]$ReceiptPath,

    [Parameter(Mandatory = $true)]
    [string]$RenderDirectory,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$Challenge,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, [int]::MaxValue)]
    [int]$ParentProcessId,

    [Parameter(Mandatory = $false)]
    [ValidateSet('standard', 'strict')]
    [string]$AuditProfile = 'standard'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$minimumContrastRatioRequired = if ($AuditProfile -eq 'strict') { 4.5 } else { 1.8 }

Add-Type @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

public static class AutofigureNativeWindow {
    [DllImport("user32.dll", SetLastError = true)]
    public static extern uint GetWindowThreadProcessId(IntPtr windowHandle, out uint processId);
}

public static class AutofigureNativePath {
    private const uint FileShareReadWriteDelete = 0x00000007;
    private const uint OpenExisting = 3;
    private const uint FileFlagBackupSemantics = 0x02000000;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(
        string fileName,
        uint desiredAccess,
        uint shareMode,
        IntPtr securityAttributes,
        uint creationDisposition,
        uint flagsAndAttributes,
        IntPtr templateFile
    );

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandle(
        SafeFileHandle file,
        StringBuilder filePath,
        uint filePathLength,
        uint flags
    );

    public static string GetFinalPath(string path) {
        using (SafeFileHandle handle = CreateFile(
            path,
            0,
            FileShareReadWriteDelete,
            IntPtr.Zero,
            OpenExisting,
            FileFlagBackupSemantics,
            IntPtr.Zero
        )) {
            if (handle.IsInvalid) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            StringBuilder buffer = new StringBuilder(512);
            uint length = GetFinalPathNameByHandle(handle, buffer, (uint)buffer.Capacity, 0);
            if (length == 0) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            if (length >= buffer.Capacity) {
                buffer = new StringBuilder((int)length + 1);
                length = GetFinalPathNameByHandle(handle, buffer, (uint)buffer.Capacity, 0);
                if (length == 0 || length >= buffer.Capacity) {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
            }
            return buffer.ToString();
        }
    }
}
'@

function Get-FullPath([string]$PathValue) {
    return [System.IO.Path]::GetFullPath($PathValue)
}

function Test-PathWithin([string]$Candidate, [string]$Parent) {
    $candidateNormalized = $Candidate.TrimEnd('\')
    $parentNormalized = $Parent.TrimEnd('\')
    if ([StringComparer]::OrdinalIgnoreCase.Equals($candidateNormalized, $parentNormalized)) {
        return $true
    }
    return $candidateNormalized.StartsWith(
        $parentNormalized + '\',
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Assert-OrdinaryWin32Components([string]$RequestedPath, [string]$Label) {
    $ordinarySpelling = $RequestedPath.Replace('/', '\')
    foreach ($component in $ordinarySpelling.Split(@('\'), [System.StringSplitOptions]::RemoveEmptyEntries)) {
        if ($component -match '^[A-Za-z]:$') { continue }
        if ($component -eq '.' -or $component -eq '..') { continue }
        if ($component.TrimEnd([char[]]@(' ', '.')) -ne $component) {
            throw "$Label contains a trailing-space or trailing-dot Win32 alias component: $component"
        }
        if ($component.Contains(':')) {
            throw "$Label must not use an NTFS alternate data stream: $component"
        }
        $baseName = $component.Split('.')[0]
        if ($baseName -match '^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])$') {
            throw "$Label contains a reserved Win32 device component: $component"
        }
    }
}

function Get-CanonicalFilesystemPath([string]$PathValue, [string]$Label) {
    $fullPath = Get-FullPath $PathValue
    $root = [System.IO.Path]::GetPathRoot($fullPath)
    $working = $fullPath.TrimEnd('\')
    if ([string]::IsNullOrWhiteSpace($working) -or
        [StringComparer]::OrdinalIgnoreCase.Equals($working, $root.TrimEnd('\'))) {
        $working = $root
    }
    $tail = New-Object System.Collections.Generic.List[string]
    while (-not (Test-Path -LiteralPath $working)) {
        $leaf = [System.IO.Path]::GetFileName($working)
        if ([string]::IsNullOrWhiteSpace($leaf)) {
            throw "$Label has no existing ancestor that can be canonicalized: $fullPath"
        }
        $tail.Insert(0, $leaf)
        $parent = [System.IO.Directory]::GetParent($working)
        if ($null -eq $parent) {
            throw "$Label has no existing ancestor that can be canonicalized: $fullPath"
        }
        $working = $parent.FullName
    }
    $canonical = [AutofigureNativePath]::GetFinalPath($working)
    if ($canonical.StartsWith('\\?\UNC\', [System.StringComparison]::OrdinalIgnoreCase)) {
        $canonical = '\\' + $canonical.Substring(8)
    }
    elseif ($canonical.StartsWith('\\?\', [System.StringComparison]::OrdinalIgnoreCase)) {
        $canonical = $canonical.Substring(4)
    }
    else {
        throw "$Label could not be reduced to an ordinary Win32 path: $canonical"
    }
    foreach ($component in $tail) {
        $canonical = Join-Path $canonical $component
    }
    return Get-FullPath $canonical
}

function Assert-NoReparsePoint([string]$PathValue, [string]$Label) {
    $root = [System.IO.Path]::GetPathRoot($PathValue)
    if ([string]::IsNullOrWhiteSpace($root)) {
        throw "$Label has no filesystem root: $PathValue"
    }
    $cursor = $root
    $relative = $PathValue.Substring($root.Length)
    foreach ($component in $relative.Split(@('\'), [System.StringSplitOptions]::RemoveEmptyEntries)) {
        $cursor = Join-Path $cursor $component
        if (-not (Test-Path -LiteralPath $cursor)) {
            break
        }
        $item = Get-Item -LiteralPath $cursor -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Label cannot traverse a symlink or junction: $cursor"
        }
    }
}

function Resolve-AuthorizedOutputPath(
    [string]$RequestedPath,
    [string]$Label,
    [string]$ProjectRoot,
    [string]$AllowedGeneratedRoot
) {
    if ([string]::IsNullOrWhiteSpace($RequestedPath)) {
        throw "$Label cannot be empty."
    }
    $ordinarySpelling = $RequestedPath.Replace('/', '\').ToLowerInvariant()
    foreach ($prefix in @('\\?\', '\\.\', '\??\', '\\??\', '\device\', '\global??\', '\dosdevices\')) {
        if ($ordinarySpelling.StartsWith($prefix)) {
            throw "$Label must not use a Win32 or NT device namespace path: $RequestedPath"
        }
    }
    Assert-OrdinaryWin32Components $RequestedPath $Label
    $wasExplicitlyAbsolute = $RequestedPath -match '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))'
    $fullPath = Get-FullPath $RequestedPath
    Assert-NoReparsePoint $fullPath $Label

    $canonicalProjectRoot = Get-CanonicalFilesystemPath $ProjectRoot 'project root'
    $canonicalAllowedRoot = Get-CanonicalFilesystemPath $AllowedGeneratedRoot 'allowed generated root'
    $fullPath = Get-CanonicalFilesystemPath $fullPath $Label
    if ($fullPath.StartsWith('\\')) {
        throw "$Label must use a canonical local drive path; UNC and mapped-drive outputs are unsupported: $fullPath"
    }

    $insideProject = Test-PathWithin $fullPath $canonicalProjectRoot
    if ($insideProject -and -not (Test-PathWithin $fullPath $canonicalAllowedRoot)) {
        throw "$Label inside the AI AutoFigure project must be under examples\generated: $fullPath"
    }
    if (-not $insideProject -and -not $wasExplicitlyAbsolute) {
        throw "$Label outside the AI AutoFigure project must be explicitly absolute: $RequestedPath"
    }
    return $fullPath
}

function Get-Sha256([string]$PathValue) {
    return (Get-FileHash -LiteralPath $PathValue -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-StringSha256([string]$Value) {
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
        return ([System.BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }
}

function Write-AtomicUtf8Json([string]$PathValue, [object]$Payload) {
    if (Test-Path -LiteralPath $PathValue) {
        throw "Receipt destination already exists: $PathValue"
    }
    $parent = Split-Path -Parent $PathValue
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        [System.IO.Directory]::CreateDirectory($parent) | Out-Null
    }
    $temporary = Join-Path $parent ('.' + [System.IO.Path]::GetFileName($PathValue) + '.' + [guid]::NewGuid().ToString('N') + '.tmp')
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($temporary, ($Payload | ConvertTo-Json -Depth 30), $utf8)
    [System.IO.File]::Move($temporary, $PathValue)
}

function Test-FormulaText([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    $patterns = @(
        '\\(?:frac|sqrt|sum|prod|int|alpha|beta|gamma|theta|mathrm|mathbf|mathcal)\b',
        '(?:\\\(|\\\[|\$\$?).+(?:\\\)|\\\]|\$\$?)',
        '[A-Za-z\u0370-\u03ff0-9]\s*(?:=|\u2260|\u2248|\u2264|\u2265|<|>)\s*[A-Za-z\u0370-\u03ff0-9]',
        '[A-Za-z0-9)]\s*[\^_]\s*[{(]?[A-Za-z0-9]',
        '[\u2211\u220f\u222b\u221a\u2202\u2207\u221e]\s*[A-Za-z0-9({]',
        '[A-Za-z0-9][\u00b2\u00b3\u2070-\u209f]',
        '^\s*[A-Za-z\u0370-\u03ff](?:\s*[_^]\s*[{(]?[A-Za-z0-9]+[})]?)?\s*$'
    )
    foreach ($pattern in $patterns) {
        if ([regex]::IsMatch($Value, $pattern)) { return $true }
    }
    return $false
}

function Get-OverlapRatio([object]$Subject, [object]$Other) {
    $area = [double]$Subject.width * [double]$Subject.height
    if ($area -le 0) { return 0.0 }
    $left = [Math]::Max([double]$Subject.left, [double]$Other.left)
    $top = [Math]::Max([double]$Subject.top, [double]$Other.top)
    $right = [Math]::Min([double]$Subject.left + [double]$Subject.width, [double]$Other.left + [double]$Other.width)
    $bottom = [Math]::Min([double]$Subject.top + [double]$Subject.height, [double]$Other.top + [double]$Other.height)
    return [Math]::Max(0.0, $right - $left) * [Math]::Max(0.0, $bottom - $top) / $area
}

function Get-LinearColorComponent([double]$Value) {
    $normalized = $Value / 255.0
    if ($normalized -le 0.04045) { return $normalized / 12.92 }
    return [Math]::Pow(($normalized + 0.055) / 1.055, 2.4)
}

function Get-ContrastRatio([int64]$FirstRgb, [int64]$SecondRgb) {
    if ($FirstRgb -lt 0 -or $FirstRgb -gt 16777215 -or $SecondRgb -lt 0 -or $SecondRgb -gt 16777215) {
        return $null
    }
    $firstR = Get-LinearColorComponent ($FirstRgb -band 255)
    $firstG = Get-LinearColorComponent (($FirstRgb -shr 8) -band 255)
    $firstB = Get-LinearColorComponent (($FirstRgb -shr 16) -band 255)
    $secondR = Get-LinearColorComponent ($SecondRgb -band 255)
    $secondG = Get-LinearColorComponent (($SecondRgb -shr 8) -band 255)
    $secondB = Get-LinearColorComponent (($SecondRgb -shr 16) -band 255)
    $firstLuminance = 0.2126 * $firstR + 0.7152 * $firstG + 0.0722 * $firstB
    $secondLuminance = 0.2126 * $secondR + 0.7152 * $secondG + 0.0722 * $secondB
    $lighter = [Math]::Max($firstLuminance, $secondLuminance)
    $darker = [Math]::Min($firstLuminance, $secondLuminance)
    return ($lighter + 0.05) / ($darker + 0.05)
}

function Convert-HexToOfficeRgb([string]$HexColor) {
    if ($HexColor -notmatch '^#[0-9A-Fa-f]{6}$') {
        throw "Invalid target font color: $HexColor"
    }
    $red = [Convert]::ToInt32($HexColor.Substring(1, 2), 16)
    $green = [Convert]::ToInt32($HexColor.Substring(3, 2), 16)
    $blue = [Convert]::ToInt32($HexColor.Substring(5, 2), 16)
    return [int64]($red + 256 * $green + 65536 * $blue)
}

function Get-TextInkEvidence([object]$TextRange, [int64]$BackgroundRgb) {
    $minimumFontSize = [double]::PositiveInfinity
    $minimumContrast = [double]::PositiveInfinity
    $maximumTransparency = 0.0
    $colors = New-Object System.Collections.Generic.HashSet[int64]
    $visibleCharacterCount = 0
    try {
        $length = [int]$TextRange.Length
        for ($characterIndex = 1; $characterIndex -le $length; $characterIndex++) {
            $character = $TextRange.Characters($characterIndex, 1)
            if ([string]::IsNullOrWhiteSpace([string]$character.Text)) { continue }
            $fontSize = [double]$character.Font.Size
            $transparency = [double]$character.Font.Fill.Transparency
            $fontRgb = [int64]$character.Font.Fill.ForeColor.RGB
            $contrast = Get-ContrastRatio $fontRgb $BackgroundRgb
            if ($fontSize -le 0 -or $transparency -lt 0 -or $transparency -gt 1 -or $null -eq $contrast) {
                throw 'A character has an unresolved font size, transparency, or color.'
            }
            $minimumFontSize = [Math]::Min($minimumFontSize, $fontSize)
            $minimumContrast = [Math]::Min($minimumContrast, [double]$contrast)
            $maximumTransparency = [Math]::Max($maximumTransparency, $transparency)
            [void]$colors.Add($fontRgb)
            $visibleCharacterCount++
        }
        if ($visibleCharacterCount -lt 1) { throw 'The native-math shape has no visible characters.' }
        return [pscustomobject][ordered]@{
            minimum_font_size = $minimumFontSize
            minimum_contrast_ratio = $minimumContrast
            maximum_character_transparency = $maximumTransparency
            color_rgb_values = @($colors | Sort-Object)
            checked_character_count = $visibleCharacterCount
            error = $null
        }
    }
    catch {
        return [pscustomobject][ordered]@{
            minimum_font_size = $null
            minimum_contrast_ratio = $null
            maximum_character_transparency = $null
            color_rgb_values = @()
            checked_character_count = 0
            error = $_.Exception.Message
        }
    }
}

$projectRoot = Get-FullPath (Join-Path $PSScriptRoot '..')
$allowedGeneratedRoot = Get-FullPath (Join-Path $projectRoot 'examples\generated')
$inputFull = Get-FullPath $InputPath
$outputFull = Resolve-AuthorizedOutputPath $OutputPath 'OutputPath' $projectRoot $allowedGeneratedRoot
$planFull = Get-FullPath $ExpectedPlanPath
$injectionReportFull = Get-FullPath $InjectionReportPath
$receiptFull = Resolve-AuthorizedOutputPath $ReceiptPath 'ReceiptPath' $projectRoot $allowedGeneratedRoot
$renderFull = Resolve-AuthorizedOutputPath $RenderDirectory 'RenderDirectory' $projectRoot $allowedGeneratedRoot

foreach ($required in @($inputFull, $planFull, $injectionReportFull)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required finalization input does not exist: $required"
    }
}
if ([System.IO.Path]::GetExtension($inputFull).ToLowerInvariant() -ne '.pptx' -or
    [System.IO.Path]::GetExtension($outputFull).ToLowerInvariant() -ne '.pptx') {
    throw 'InputPath and OutputPath must both be .pptx files.'
}
if ([StringComparer]::OrdinalIgnoreCase.Equals($inputFull, $outputFull)) {
    throw 'The roundtrip output must differ from the injected input.'
}
if (Test-Path -LiteralPath $receiptFull) {
    throw "Runtime ReceiptPath must be a fresh path: $receiptFull"
}
$renderRoot = [System.IO.Path]::GetPathRoot($renderFull)
if ([StringComparer]::OrdinalIgnoreCase.Equals($renderFull.TrimEnd('\'), $renderRoot.TrimEnd('\'))) {
    throw 'RenderDirectory cannot be a filesystem root.'
}
$injectionReportText = [System.IO.File]::ReadAllText($injectionReportFull, [System.Text.Encoding]::UTF8)
$injectionReport = $injectionReportText | ConvertFrom-Json
$planText = [System.IO.File]::ReadAllText($planFull, [System.Text.Encoding]::UTF8)
$expectedPlan = $planText | ConvertFrom-Json
$inputHash = Get-Sha256 $inputFull
$planHash = Get-Sha256 $planFull
$injectionReportHash = Get-Sha256 $injectionReportFull
if ($injectionReport.document_type -ne 'NATIVE_OFFICE_MATH_INJECTION_REPORT' -or
    $injectionReport.status -ne 'INJECTED_REQUIRES_POWERPOINT_ROUNDTRIP' -or
    $injectionReport.output_sha256 -ne $inputHash -or
    $injectionReport.plan_sha256 -ne $planHash) {
    throw 'Injection report is not bound to the exact input PPTX and expected plan.'
}
$expectedFormulaStyles = @{}
$styleOperations = @()
foreach ($operation in @($expectedPlan.operations)) {
    $hasTargetSize = $null -ne $operation.PSObject.Properties['target_font_size_pt']
    $hasTargetColor = $null -ne $operation.PSObject.Properties['target_font_color']
    if ($hasTargetSize -ne $hasTargetColor) {
        throw "Expected plan has an incomplete target font style for $($operation.placeholder_name)."
    }
    if (-not $hasTargetSize) { continue }
    $targetSize = [double]$operation.target_font_size_pt
    $targetColor = [string]$operation.target_font_color
    if ([double]::IsNaN($targetSize) -or [double]::IsInfinity($targetSize) -or
        $targetSize -lt 6.0 -or $targetSize -gt 72.0) {
        throw "Expected plan has an invalid target font size for $($operation.placeholder_name)."
    }
    $styleKey = ([string]$operation.slide_index) + "`n" + [string]$operation.placeholder_name
    if ($expectedFormulaStyles.ContainsKey($styleKey)) {
        throw "Expected plan repeats a target font style for $($operation.placeholder_name)."
    }
    $style = [pscustomobject][ordered]@{
        slide_index = [int]$operation.slide_index
        shape_name = [string]$operation.placeholder_name
        target_font_size_pt = $targetSize
        target_font_color = $targetColor.ToUpperInvariant()
        target_font_color_rgb = Convert-HexToOfficeRgb $targetColor
    }
    $expectedFormulaStyles[$styleKey] = $style
    $styleOperations += $style
}
if (Test-Path -LiteralPath $outputFull) {
    throw "Output already exists; this probe requires a fresh path: $outputFull"
}
if (Test-Path -LiteralPath $renderFull) {
    throw "Render directory already exists; this probe requires a fresh path: $renderFull"
}
foreach ($target in @($inputFull, $outputFull)) {
    $ownerFile = Join-Path (Split-Path -Parent $target) ('~$' + [System.IO.Path]::GetFileName($target))
    if (Test-Path -LiteralPath $ownerFile -PathType Leaf) {
        throw "A target deck is open or locked: $ownerFile"
    }
}

$outputParent = Split-Path -Parent $outputFull
$renderParent = Split-Path -Parent $renderFull
[System.IO.Directory]::CreateDirectory($outputParent) | Out-Null
[System.IO.Directory]::CreateDirectory($renderParent) | Out-Null
$transactionId = [guid]::NewGuid().ToString('N')
$stagingOutput = Join-Path $outputParent ('.' + [System.IO.Path]::GetFileNameWithoutExtension($outputFull) + ".${transactionId}.staging.pptx")
$stagingRender = Join-Path $renderParent ('.' + [System.IO.Path]::GetFileName($renderFull) + ".${transactionId}.staging")
[System.IO.Directory]::CreateDirectory($stagingRender) | Out-Null

$startedAt = [DateTime]::UtcNow.ToString('o')
$scriptHash = Get-Sha256 $PSCommandPath
$powerShellExecutablePath = [System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
$powerShellExecutableHash = Get-Sha256 $powerShellExecutablePath
$powerShellSignature = Get-AuthenticodeSignature -LiteralPath $powerShellExecutablePath
$powerShellSignatureStatus = [string]$powerShellSignature.Status
$powerShellSignerSubject = if ($null -ne $powerShellSignature.SignerCertificate) { [string]$powerShellSignature.SignerCertificate.Subject } else { $null }
$powerShellSignerThumbprint = if ($null -ne $powerShellSignature.SignerCertificate) { [string]$powerShellSignature.SignerCertificate.Thumbprint } else { $null }
$wasRunning = @(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue).Count -gt 0
$application = $null
$presentation = $null
$reopened = $null
$controlPresentation = $null
$powerPointVersion = $null
$powerPointProcessId = $null
$powerPointExecutablePath = $null
$powerPointExecutableHash = $null
$powerPointSignatureStatus = $null
$powerPointSignerSubject = $null
$powerPointSignerThumbprint = $null
$powerPointExecutableVersion = $null
$mathShapes = @()
$shapeInventory = @()
$templateInventory = @()
$slideBackgroundColors = @{}
$renders = @()
$counterfactualRenders = @()
$verifiedInputFormulaStyles = @()
$violations = @()
$stages = [ordered]@{
    opened = $false
    formula_styles_verified_at_open = $false
    saved_as_staging_pptx = $false
    first_close = $false
    reopened_read_only = $false
    math_zones_read = $false
    visibility_scanned = $false
    masquerade_scanned = $false
    render_exported = $false
    counterfactual_rendered = $false
    second_close = $false
    output_committed = $false
    render_committed = $false
}
$failure = $null
$renderWasCommitted = $false
$outputWasCommitted = $false

try {
    $application = New-Object -ComObject PowerPoint.Application
    $application.Visible = -1
    $powerPointVersion = [string]$application.Version
    try {
        $applicationHwnd = [IntPtr][int64]$application.HWND
        [uint32]$windowProcessId = 0
        [void][AutofigureNativeWindow]::GetWindowThreadProcessId($applicationHwnd, [ref]$windowProcessId)
        if ($windowProcessId -lt 1) { throw 'PowerPoint HWND did not resolve to a process.' }
        $powerPointProcess = Get-Process -Id ([int]$windowProcessId) -ErrorAction Stop
        if ($powerPointProcess.ProcessName -notmatch '^(?i:POWERPNT)$') {
            throw "PowerPoint HWND resolved to unexpected process $($powerPointProcess.ProcessName)."
        }
        $powerPointProcessId = [int]$powerPointProcess.Id
        $powerPointExecutablePath = [string]$powerPointProcess.MainModule.FileName
        $powerPointExecutableHash = Get-Sha256 $powerPointExecutablePath
        $powerPointSignature = Get-AuthenticodeSignature -LiteralPath $powerPointExecutablePath
        $powerPointSignatureStatus = [string]$powerPointSignature.Status
        $powerPointSignerSubject = if ($null -ne $powerPointSignature.SignerCertificate) { [string]$powerPointSignature.SignerCertificate.Subject } else { $null }
        $powerPointSignerThumbprint = if ($null -ne $powerPointSignature.SignerCertificate) { [string]$powerPointSignature.SignerCertificate.Thumbprint } else { $null }
        $powerPointExecutableVersion = [string][System.Diagnostics.FileVersionInfo]::GetVersionInfo($powerPointExecutablePath).FileVersion
    }
    catch { $powerPointProcessId = $null }
    if ($null -eq $powerPointProcessId -or [string]::IsNullOrWhiteSpace($powerPointExecutablePath) -or
        $powerPointSignatureStatus -ne 'Valid' -or $powerPointSignerSubject -notmatch '(?i)Microsoft') {
        throw 'PowerPoint process identity or Microsoft Authenticode signature could not be verified.'
    }
    foreach ($openDeck in @($application.Presentations)) {
        if ([StringComparer]::OrdinalIgnoreCase.Equals([string]$openDeck.FullName, $inputFull) -or
            [StringComparer]::OrdinalIgnoreCase.Equals([string]$openDeck.FullName, $outputFull) -or
            [StringComparer]::OrdinalIgnoreCase.Equals([string]$openDeck.FullName, $stagingOutput)) {
            throw "A target deck is already open in PowerPoint: $($openDeck.FullName)"
        }
    }

    $presentation = $application.Presentations.Open($inputFull, $false, $false, $true)
    $stages.opened = $true
    foreach ($style in $styleOperations) {
        $styleSlide = $presentation.Slides.Item([int]$style.slide_index)
        $styleShape = $styleSlide.Shapes.Item([string]$style.shape_name)
        $styleTextRange = $styleShape.TextFrame2.TextRange
        $styleZones = $styleTextRange.MathZones(-1, -1)
        if ([int]$styleZones.Count -lt 1) {
            throw "Target font style shape has no MathZone: $($style.shape_name)"
        }
        $observedSize = [double]$styleZones.Font.Size
        $observedColor = [int64]$styleZones.Font.Fill.ForeColor.RGB
        $observedTransparency = [double]$styleZones.Font.Fill.Transparency
        if ($observedSize -le 0 -or
            [Math]::Abs($observedSize - [double]$style.target_font_size_pt) -gt 0.15 -or
            $observedColor -ne [int64]$style.target_font_color_rgb -or
            $observedTransparency -lt 0 -or $observedTransparency -gt 0.05) {
            throw "Injected MathZone target font style was not materialized for $($style.shape_name)."
        }
        $verifiedInputFormulaStyles += [pscustomobject][ordered]@{
            slide_index = [int]$style.slide_index
            shape_name = [string]$style.shape_name
            target_font_size_pt = [double]$style.target_font_size_pt
            target_font_color = [string]$style.target_font_color
            target_font_color_rgb = [int64]$style.target_font_color_rgb
            observed_font_size_pt = $observedSize
            observed_font_color_rgb = $observedColor
            observed_font_transparency = $observedTransparency
        }
    }
    $stages.formula_styles_verified_at_open = $true
    $presentation.SaveAs($stagingOutput, 24)
    $stages.saved_as_staging_pptx = $true
    $presentation.Close()
    $presentation = $null
    $stages.first_close = $true

    $reopened = $application.Presentations.Open($stagingOutput, $true, $false, $true)
    $stages.reopened_read_only = $true
    $slideWidth = [double]$reopened.PageSetup.SlideWidth
    $slideHeight = [double]$reopened.PageSetup.SlideHeight
    for ($slideIndex = 1; $slideIndex -le $reopened.Slides.Count; $slideIndex++) {
        $slide = $reopened.Slides.Item($slideIndex)
        $backgroundRgb = $null
        try {
            if ([int]$slide.FollowMasterBackground -eq 0) {
                $activeBackgroundScope = 'slide'
            }
            elseif ([int]$slide.CustomLayout.FollowMasterBackground -eq 0) {
                $activeBackgroundScope = 'custom_layout'
            }
            else {
                $activeBackgroundScope = 'slide_master'
            }
        }
        catch {
            $activeBackgroundScope = $null
            $violations += [pscustomobject]@{ code = 'BACKGROUND_INHERITANCE_UNREADABLE'; slide_index = $slideIndex; error = $_.Exception.Message }
        }
        foreach ($backgroundScope in @('slide', 'custom_layout', 'slide_master')) {
            try {
                $backgroundObject = switch ($backgroundScope) {
                    'slide' { $slide.Background }
                    'custom_layout' { $slide.CustomLayout.Background }
                    'slide_master' { $slide.Master.Background }
                }
                $backgroundFillType = [int]$backgroundObject.Fill.Type
                $backgroundColor = [int64]$backgroundObject.Fill.ForeColor.RGB
                $templateInventory += [pscustomobject][ordered]@{
                    slide_index = $slideIndex
                    scope = $backgroundScope
                    kind = 'background'
                    fill_type = $backgroundFillType
                    color_rgb = $backgroundColor
                    picture_or_texture = ($backgroundFillType -in @(4, 6))
                    active = ($backgroundScope -eq $activeBackgroundScope)
                    error = $null
                }
                if ($backgroundScope -eq $activeBackgroundScope -and $backgroundFillType -ne 1) {
                    $violations += [pscustomobject]@{ code = 'ACTIVE_BACKGROUND_NOT_SOLID'; slide_index = $slideIndex; scope = $backgroundScope; fill_type = $backgroundFillType }
                }
                if ($backgroundScope -eq $activeBackgroundScope -and $backgroundFillType -eq 1 -and
                    $backgroundColor -ge 0 -and $backgroundColor -le 16777215) {
                    $backgroundRgb = $backgroundColor
                }
            }
            catch {
                $templateInventory += [pscustomobject][ordered]@{
                    slide_index = $slideIndex
                    scope = $backgroundScope
                    kind = 'background'
                    fill_type = $null
                    color_rgb = $null
                    picture_or_texture = $null
                    active = ($backgroundScope -eq $activeBackgroundScope)
                    error = $_.Exception.Message
                }
            }
        }
        if ($null -eq $backgroundRgb) {
            $violations += [pscustomobject]@{ code = 'EFFECTIVE_BACKGROUND_COLOR_UNREADABLE'; slide_index = $slideIndex }
        }
        else {
            $slideBackgroundColors[$slideIndex] = $backgroundRgb
        }

        $templateScopes = @('custom_layout')
        try { if ([int]$slide.DisplayMasterShapes -ne 0) { $templateScopes += 'slide_master' } }
        catch { $violations += [pscustomobject]@{ code = 'DISPLAY_MASTER_SHAPES_UNREADABLE'; slide_index = $slideIndex } }
        foreach ($templateScope in $templateScopes) {
            try {
                if ($templateScope -eq 'custom_layout') {
                    $templateShapes = $slide.CustomLayout.Shapes
                }
                else {
                    $templateShapes = $slide.Master.Shapes
                }
                $templateShapeCount = [int]$templateShapes.Count
                for ($templateIndex = 1; $templateIndex -le $templateShapeCount; $templateIndex++) {
                    $templateShape = $templateShapes.Item($templateIndex)
                    $templateText = ''
                    $templateHasText = $false
                    try {
                        if ([int]$templateShape.HasTextFrame -ne 0 -and [int]$templateShape.TextFrame2.HasText -ne 0) {
                            $templateHasText = $true
                            $templateText = [string]$templateShape.TextFrame2.TextRange.Text
                        }
                    }
                    catch { $templateText = ''; $templateHasText = $false }
                    $templateType = [int]$templateShape.Type
                    $templateFillVisible = $false
                    $templateFillType = $null
                    $templateFillTransparency = $null
                    try {
                        $templateFillVisible = ([int]$templateShape.Fill.Visible -ne 0)
                        $templateFillType = [int]$templateShape.Fill.Type
                        $templateFillTransparency = [double]$templateShape.Fill.Transparency
                    }
                    catch { }
                    $templateRow = [pscustomobject][ordered]@{
                        slide_index = $slideIndex
                        scope = $templateScope
                        kind = 'shape'
                        shape_index = $templateIndex
                        shape_name = [string]$templateShape.Name
                        shape_type = $templateType
                        visible = ([int]$templateShape.Visible -ne 0)
                        left = [double]$templateShape.Left
                        top = [double]$templateShape.Top
                        width = [double]$templateShape.Width
                        height = [double]$templateShape.Height
                        has_text = $templateHasText
                        text = $templateText
                        formula_text_candidate = (Test-FormulaText $templateText)
                        is_picture = ($templateType -in @(11, 13))
                        is_ole = ($templateType -in @(7, 10))
                        fill_visible = $templateFillVisible
                        fill_type = $templateFillType
                        fill_transparency = $templateFillTransparency
                    }
                    $templateInventory += $templateRow
                    if ($templateRow.is_ole) {
                        $violations += [pscustomobject]@{ code = 'TEMPLATE_OLE_MASQUERADE_RISK'; slide_index = $slideIndex; scope = $templateScope; shape_name = $templateRow.shape_name }
                    }
                    if ($templateRow.formula_text_candidate) {
                        $violations += [pscustomobject]@{ code = 'TEMPLATE_TEXT_FORMULA_MASQUERADE_RISK'; slide_index = $slideIndex; scope = $templateScope; shape_name = $templateRow.shape_name }
                    }
                }
            }
            catch {
                $violations += [pscustomobject]@{ code = 'TEMPLATE_SHAPE_SCAN_FAILED'; slide_index = $slideIndex; scope = $templateScope; error = $_.Exception.Message }
            }
        }
        for ($shapeIndex = 1; $shapeIndex -le $slide.Shapes.Count; $shapeIndex++) {
            $shape = $slide.Shapes.Item($shapeIndex)
            $alternativeText = [string]$shape.AlternativeText
            $title = [string]$shape.Title
            $text = ''
            $hasText = $false
            try {
                if ([int]$shape.HasTextFrame -ne 0 -and [int]$shape.TextFrame2.HasText -ne 0) {
                    $hasText = $true
                    $text = [string]$shape.TextFrame2.TextRange.Text
                }
            }
            catch { $hasText = $false; $text = '' }
            $fillVisible = $false
            $fillTransparency = $null
            $fillType = $null
            $fillColorRgb = $null
            try {
                $fillVisible = ([int]$shape.Fill.Visible -ne 0)
                $fillTransparency = [double]$shape.Fill.Transparency
                $fillType = [int]$shape.Fill.Type
                $fillColorRgb = [int64]$shape.Fill.ForeColor.RGB
            }
            catch { $fillVisible = $false; $fillTransparency = $null; $fillType = $null; $fillColorRgb = $null }
            $textFillTransparency = $null
            if ($hasText) {
                try { $textFillTransparency = [double]$shape.TextFrame2.TextRange.Font.Fill.Transparency }
                catch { $textFillTransparency = $null }
            }
            $row = [pscustomobject][ordered]@{
                slide_index = $slideIndex
                shape_index = $shapeIndex
                shape_id = [int]$shape.Id
                shape_name = [string]$shape.Name
                shape_type = [int]$shape.Type
                z_order_position = [int]$shape.ZOrderPosition
                visible = ([int]$shape.Visible -ne 0)
                left = [double]$shape.Left
                top = [double]$shape.Top
                width = [double]$shape.Width
                height = [double]$shape.Height
                slide_width = $slideWidth
                slide_height = $slideHeight
                alternative_text = $alternativeText
                title = $title
                has_text = $hasText
                text = $text
                text_sha256 = if ($hasText) {
                    $bytes = [Text.Encoding]::UTF8.GetBytes($text)
                    $sha = [Security.Cryptography.SHA256]::Create()
                    try { ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() }
                    finally { $sha.Dispose() }
                } else { $null }
                fill_visible = $fillVisible
                fill_transparency = $fillTransparency
                fill_type = $fillType
                fill_color_rgb = $fillColorRgb
                text_fill_transparency = $textFillTransparency
                formula_text_candidate = (Test-FormulaText $text)
                is_picture = ([int]$shape.Type -in @(11, 13))
                is_ole = ([int]$shape.Type -in @(7, 10))
                is_native_math = $alternativeText.StartsWith('AI_AUTOFIGURE_NATIVE_MATH_V1:')
            }
            $shapeInventory += $row
            if (-not $row.is_native_math) { continue }
            $zoneCount = 0
            $zoneLength = 0
            $zoneInventory = @()
            $zoneError = $null
            try {
                $mathZones = $shape.TextFrame2.TextRange.MathZones(-1, -1)
                $zoneCount = [int]$mathZones.Count
                $zoneLength = [int]$mathZones.Length
                for ($nativeZoneIndex = 1; $nativeZoneIndex -le $zoneCount; $nativeZoneIndex++) {
                    $nativeZone = $shape.TextFrame2.TextRange.MathZones([int]$nativeZoneIndex, 1)
                    $nativeZoneText = [string]$nativeZone.Text
                    $zoneInventory += [pscustomobject][ordered]@{
                        zone_index = [int]$nativeZoneIndex
                        start = [int]$nativeZone.Start
                        length = [int]$nativeZone.Length
                        text_sha256 = Get-StringSha256 $nativeZoneText
                    }
                }
            }
            catch {
                $zoneError = $_.Exception.Message
            }
            $styleKey = ([string]$slideIndex) + "`n" + [string]$row.shape_name
            $expectedStyle = if ($expectedFormulaStyles.ContainsKey($styleKey)) {
                $expectedFormulaStyles[$styleKey]
            }
            else { $null }
            $mathRow = [pscustomobject][ordered]@{
                slide_index = $slideIndex
                shape_name = $row.shape_name
                shape_id = $row.shape_id
                shape_type = $row.shape_type
                math_zone_count = $zoneCount
                math_zone_length = $zoneLength
                math_zones = $zoneInventory
                math_zone_error = $zoneError
                z_order_position = $row.z_order_position
                visible = $row.visible
                left = $row.left
                top = $row.top
                width = $row.width
                height = $row.height
                slide_width = $slideWidth
                slide_height = $slideHeight
                text_fill_transparency = $textFillTransparency
                target_font_size_pt = if ($null -ne $expectedStyle) { [double]$expectedStyle.target_font_size_pt } else { $null }
                target_font_color = if ($null -ne $expectedStyle) { [string]$expectedStyle.target_font_color } else { $null }
                target_font_color_rgb = if ($null -ne $expectedStyle) { [int64]$expectedStyle.target_font_color_rgb } else { $null }
                background_rgb = $null
                background_source = $null
                minimum_font_size = $null
                minimum_contrast_ratio = $null
                minimum_contrast_ratio_required = $minimumContrastRatioRequired
                maximum_character_transparency = $null
                color_rgb_values = @()
                checked_character_count = 0
                ink_evidence_error = $null
            }
            $mathShapes += $mathRow
            if ($zoneCount -lt 1 -or $zoneLength -lt 1 -or $null -ne $zoneError) {
                $violations += [pscustomobject]@{ code = 'MATHZONES_INVALID'; slide_index = $slideIndex; shape_name = $row.shape_name }
            }
            if (-not $row.visible -or $row.width -le 0 -or $row.height -le 0 -or
                $row.left -lt 0 -or $row.top -lt 0 -or
                $row.left + $row.width -gt $slideWidth + 0.01 -or
                $row.top + $row.height -gt $slideHeight + 0.01 -or
                $row.z_order_position -lt 1) {
                $violations += [pscustomobject]@{ code = 'NATIVE_MATH_NOT_VISIBLE_ON_CANVAS'; slide_index = $slideIndex; shape_name = $row.shape_name }
            }
            if ($null -eq $textFillTransparency -or $textFillTransparency -gt 0.05) {
                $violations += [pscustomobject]@{ code = 'NATIVE_MATH_TEXT_TRANSPARENCY_INVALID'; slide_index = $slideIndex; shape_name = $row.shape_name; transparency = $textFillTransparency }
            }
        }
    }
    foreach ($mathRow in $mathShapes) {
        $effectiveBackgroundRgb = $slideBackgroundColors[[int]$mathRow.slide_index]
        $backgroundSource = 'slide_background'
        $underlayInvalid = $false
        $nativeShapeRow = $shapeInventory | Where-Object {
            $_.slide_index -eq $mathRow.slide_index -and $_.shape_name -eq $mathRow.shape_name -and $_.is_native_math
        } | Select-Object -First 1
        if ($null -ne $nativeShapeRow -and $nativeShapeRow.fill_visible) {
            if ($nativeShapeRow.fill_type -eq 1 -and $null -ne $nativeShapeRow.fill_transparency -and
                $nativeShapeRow.fill_transparency -le 0.05 -and $null -ne $nativeShapeRow.fill_color_rgb -and
                $nativeShapeRow.fill_color_rgb -ge 0 -and $nativeShapeRow.fill_color_rgb -le 16777215) {
                $effectiveBackgroundRgb = [int64]$nativeShapeRow.fill_color_rgb
                $backgroundSource = 'native_shape_fill'
            }
            else {
                $violations += [pscustomobject]@{ code = 'NATIVE_MATH_FILL_UNVERIFIABLE'; slide_index = $mathRow.slide_index; shape_name = $mathRow.shape_name; fill_type = $nativeShapeRow.fill_type; fill_transparency = $nativeShapeRow.fill_transparency }
                $underlayInvalid = $true
            }
        }
        $lowerShapes = @($shapeInventory |
            Where-Object {
                $_.slide_index -eq $mathRow.slide_index -and
                $_.z_order_position -lt $mathRow.z_order_position -and
                -not $_.is_native_math -and $_.visible
            } |
            Sort-Object z_order_position -Descending)
        foreach ($lowerShape in $lowerShapes) {
            if ($backgroundSource -eq 'native_shape_fill' -or $underlayInvalid) { break }
            $underlayOverlap = Get-OverlapRatio $mathRow $lowerShape
            if ($underlayOverlap -lt 0.005) { continue }
            if (-not $lowerShape.fill_visible) {
                if ($lowerShape.is_picture -or $lowerShape.is_ole) {
                    $violations += [pscustomobject]@{ code = 'NATIVE_MATH_UNDERLAY_UNVERIFIABLE'; slide_index = $mathRow.slide_index; shape_name = $mathRow.shape_name; underlay_shape = $lowerShape.shape_name; overlap_ratio = $underlayOverlap }
                    $underlayInvalid = $true
                    break
                }
                continue
            }
            # A semantic container may have a visible outline but a fully
            # transparent interior. It contributes no background ink, so it
            # must not hide the real solid underlay or slide background.
            if ($null -ne $lowerShape.fill_transparency -and
                $lowerShape.fill_transparency -ge 0.95 -and
                -not $lowerShape.is_picture -and -not $lowerShape.is_ole) {
                continue
            }
            $isReliableSolidFill = $lowerShape.fill_type -eq 1 -and
                $null -ne $lowerShape.fill_transparency -and $lowerShape.fill_transparency -le 0.05 -and
                $null -ne $lowerShape.fill_color_rgb -and $lowerShape.fill_color_rgb -ge 0 -and
                $lowerShape.fill_color_rgb -le 16777215
            # Arrowheads and rounded corners can touch a formula box by a few pixels
            # even though they are foreground decoration, not its effective background.
            # Ignore only a narrow, opaque-solid boundary contact and continue looking
            # for the first shape that actually covers the complete formula box.  Raster,
            # OLE, transparent/gradient, and material partial underlays remain fatal.
            if ($isReliableSolidFill -and $underlayOverlap -lt 0.02) { continue }
            if ($underlayOverlap -lt 0.995 -or -not $isReliableSolidFill) {
                $violations += [pscustomobject]@{
                    code = 'NATIVE_MATH_UNDERLAY_UNVERIFIABLE'
                    slide_index = $mathRow.slide_index
                    shape_name = $mathRow.shape_name
                    underlay_shape = $lowerShape.shape_name
                    overlap_ratio = $underlayOverlap
                    fill_type = $lowerShape.fill_type
                    fill_transparency = $lowerShape.fill_transparency
                }
                $underlayInvalid = $true
                break
            }
            $effectiveBackgroundRgb = [int64]$lowerShape.fill_color_rgb
            $backgroundSource = 'solid_shape:' + [string]$lowerShape.shape_name
            break
        }
        $inkEvidence = if ($underlayInvalid -or $null -eq $effectiveBackgroundRgb) {
            [pscustomobject][ordered]@{
                minimum_font_size = $null
                minimum_contrast_ratio = $null
                maximum_character_transparency = $null
                color_rgb_values = @()
                checked_character_count = 0
                error = 'A reliable effective background could not be established.'
            }
        }
        else {
            $mathSlide = $reopened.Slides.Item([int]$mathRow.slide_index)
            $mathShape = $mathSlide.Shapes.Item([string]$mathRow.shape_name)
            Get-TextInkEvidence $mathShape.TextFrame2.TextRange ([int64]$effectiveBackgroundRgb)
        }
        $mathRow.background_rgb = $effectiveBackgroundRgb
        $mathRow.background_source = $backgroundSource
        $mathRow.minimum_font_size = $inkEvidence.minimum_font_size
        $mathRow.minimum_contrast_ratio = $inkEvidence.minimum_contrast_ratio
        $mathRow.minimum_contrast_ratio_required = $minimumContrastRatioRequired
        $mathRow.maximum_character_transparency = $inkEvidence.maximum_character_transparency
        $mathRow.color_rgb_values = $inkEvidence.color_rgb_values
        $mathRow.checked_character_count = $inkEvidence.checked_character_count
        $mathRow.ink_evidence_error = $inkEvidence.error
        if ($null -ne $inkEvidence.error -or $inkEvidence.minimum_font_size -lt 6.0 -or
            $inkEvidence.minimum_contrast_ratio -lt $minimumContrastRatioRequired -or
            $inkEvidence.maximum_character_transparency -gt 0.05 -or
            $inkEvidence.checked_character_count -lt 1) {
            $violations += [pscustomobject]@{
                code = 'NATIVE_MATH_TEXT_INK_INVALID'
                slide_index = $mathRow.slide_index
                shape_name = $mathRow.shape_name
                minimum_font_size = $inkEvidence.minimum_font_size
                minimum_contrast_ratio = $inkEvidence.minimum_contrast_ratio
                maximum_character_transparency = $inkEvidence.maximum_character_transparency
                error = $inkEvidence.error
            }
        }
        if ($null -ne $mathRow.target_font_size_pt -and $null -eq $inkEvidence.error) {
            $sizeDelta = [Math]::Abs(
                [double]$inkEvidence.minimum_font_size - [double]$mathRow.target_font_size_pt
            )
            $observedColors = @($inkEvidence.color_rgb_values)
            $colorMatches = $observedColors.Count -eq 1 -and
                [int64]$observedColors[0] -eq [int64]$mathRow.target_font_color_rgb
            if ($sizeDelta -gt 0.15 -or -not $colorMatches) {
                $violations += [pscustomobject]@{
                    code = 'NATIVE_MATH_STYLE_MISMATCH'
                    slide_index = $mathRow.slide_index
                    shape_name = $mathRow.shape_name
                    target_font_size_pt = $mathRow.target_font_size_pt
                    observed_minimum_font_size = $inkEvidence.minimum_font_size
                    target_font_color_rgb = $mathRow.target_font_color_rgb
                    observed_color_rgb_values = $observedColors
                }
            }
        }
    }
    $stages.math_zones_read = $true
    $stages.visibility_scanned = $true
    if ($mathShapes.Count -eq 0) {
        $violations += [pscustomobject]@{ code = 'NO_NATIVE_MATH_SHAPES' }
    }
    for ($mathIndex = 0; $mathIndex -lt $mathShapes.Count; $mathIndex++) {
        for ($otherMathIndex = $mathIndex + 1; $otherMathIndex -lt $mathShapes.Count; $otherMathIndex++) {
            $firstMath = $mathShapes[$mathIndex]
            $secondMath = $mathShapes[$otherMathIndex]
            if ($firstMath.slide_index -ne $secondMath.slide_index) { continue }
            $nativeOverlap = Get-OverlapRatio $firstMath $secondMath
            if ($nativeOverlap -ge 0.05) {
                $violations += [pscustomobject]@{ code = 'NATIVE_MATH_SHAPE_OVERLAP'; slide_index = $firstMath.slide_index; shape_name = $firstMath.shape_name; other_shape = $secondMath.shape_name; overlap_ratio = $nativeOverlap }
            }
        }
    }
    foreach ($templateRow in @($templateInventory | Where-Object { $_.kind -eq 'shape' -and $_.visible })) {
        foreach ($mathRow in @($mathShapes | Where-Object { $_.slide_index -eq $templateRow.slide_index })) {
            $templateOverlap = Get-OverlapRatio $mathRow $templateRow
            $templateCanPaint = $templateRow.is_picture -or
                ($templateRow.fill_visible -and $null -ne $templateRow.fill_transparency -and $templateRow.fill_transparency -lt 0.95)
            if ($templateCanPaint -and $templateOverlap -ge 0.005) {
                $violations += [pscustomobject]@{
                    code = 'TEMPLATE_SHAPE_FORMULA_OVERLAP_RISK'
                    slide_index = $mathRow.slide_index
                    scope = $templateRow.scope
                    shape_name = $templateRow.shape_name
                    native_shape = $mathRow.shape_name
                    overlap_ratio = $templateOverlap
                }
            }
        }
    }

    foreach ($row in $shapeInventory) {
        if ($row.is_native_math) { continue }
        if ($row.is_ole) {
            $violations += [pscustomobject]@{ code = 'OLE_OBJECT_MASQUERADE_RISK'; slide_index = $row.slide_index; shape_name = $row.shape_name }
        }
        if ($row.formula_text_candidate) {
            $violations += [pscustomobject]@{ code = 'PLAIN_TEXT_FORMULA_MASQUERADE_RISK'; slide_index = $row.slide_index; shape_name = $row.shape_name; text_sha256 = $row.text_sha256 }
        }
        foreach ($mathRow in @($mathShapes | Where-Object { $_.slide_index -eq $row.slide_index })) {
            $overlap = Get-OverlapRatio $mathRow $row
            $nameSuggestsFormula = [regex]::IsMatch(($row.shape_name + ' ' + $row.alternative_text + ' ' + $row.title), '(?i)(formula|equation|latex|math|omml)')
            if ($row.is_picture -and ($overlap -ge 0.005 -or $nameSuggestsFormula)) {
                $violations += [pscustomobject]@{ code = 'PICTURE_FORMULA_MASQUERADE_RISK'; slide_index = $row.slide_index; shape_name = $row.shape_name; native_shape = $mathRow.shape_name; overlap_ratio = $overlap }
            }
            $coverCandidate = $row.is_picture -or $row.is_ole -or
                ($row.fill_visible -and $null -ne $row.fill_transparency -and $row.fill_transparency -lt 0.95) -or
                $row.has_text -or $row.formula_text_candidate -or ($row.shape_type -in @(1, 3, 6, 14, 17))
            if ($row.z_order_position -gt $mathRow.z_order_position -and $coverCandidate -and $overlap -ge 0.005) {
                $violations += [pscustomobject]@{ code = 'NATIVE_MATH_ZORDER_COVER_RISK'; slide_index = $row.slide_index; shape_name = $mathRow.shape_name; cover_shape = $row.shape_name; overlap_ratio = $overlap }
            }
        }
    }
    $stages.masquerade_scanned = $true
    if ($violations.Count -gt 0) {
        throw 'PowerPoint visibility or formula-masquerade checks failed.'
    }

    $renderWidth = 1600
    $renderHeight = [Math]::Max(1, [int][Math]::Round($renderWidth * $slideHeight / $slideWidth))
    for ($slideIndex = 1; $slideIndex -le $reopened.Slides.Count; $slideIndex++) {
        $slide = $reopened.Slides.Item($slideIndex)
        $warmupPath = Join-Path $stagingRender (".warmup-${slideIndex}.png")
        $stagingRenderPath = Join-Path $stagingRender ("slide-${slideIndex}.png")
        $verificationRenderPath = Join-Path $stagingRender ("slide-${slideIndex}.verify.png")
        $slide.Export($warmupPath, 'PNG', $renderWidth, $renderHeight)
        $slide.Export($stagingRenderPath, 'PNG', $renderWidth, $renderHeight)
        $slide.Export($verificationRenderPath, 'PNG', $renderWidth, $renderHeight)
        if (Test-Path -LiteralPath $warmupPath -PathType Leaf) { Remove-Item -LiteralPath $warmupPath -Force }
        if (-not (Test-Path -LiteralPath $stagingRenderPath -PathType Leaf) -or
            (Get-Item -LiteralPath $stagingRenderPath).Length -le 0 -or
            -not (Test-Path -LiteralPath $verificationRenderPath -PathType Leaf) -or
            (Get-Item -LiteralPath $verificationRenderPath).Length -le 0) {
            throw "PowerPoint did not create two non-empty fresh renders for slide $slideIndex."
        }
        $renders += [pscustomobject][ordered]@{
            slide_index = $slideIndex
            path = (Join-Path $renderFull ("slide-${slideIndex}.png"))
            sha256 = Get-Sha256 $stagingRenderPath
            byte_length = [int64](Get-Item -LiteralPath $stagingRenderPath).Length
            verification_path = (Join-Path $renderFull ("slide-${slideIndex}.verify.png"))
            verification_sha256 = Get-Sha256 $verificationRenderPath
            verification_byte_length = [int64](Get-Item -LiteralPath $verificationRenderPath).Length
            width = $renderWidth
            height = $renderHeight
            exported_at_utc = [DateTime]::UtcNow.ToString('o')
        }
    }
    $stages.render_exported = $true
    $reopened.Close()
    $reopened = $null
    $stages.second_close = $true
    if ($AuditProfile -eq 'strict') {
    foreach ($mathRow in $mathShapes) {
        $matchingOperations = @($injectionReport.operations | Where-Object {
            [int]$_.slide_index -eq [int]$mathRow.slide_index -and
            [string]$_.placeholder_name -eq [string]$mathRow.shape_name
        })
        if ($matchingOperations.Count -ne 1) {
            throw "Injection report does not identify exactly one operation for $($mathRow.shape_name)."
        }
        $formulaIds = @($matchingOperations[0].formula_ids)
        if ($formulaIds.Count -ne [int]$mathRow.math_zone_count) {
            throw "Injection report formula count does not match MathZones for $($mathRow.shape_name)."
        }
        for ($zoneIndex = 1; $zoneIndex -le $formulaIds.Count; $zoneIndex++) {
            $controlPresentation = $null
            $controlSlide = $null
            $controlShape = $null
            $controlZone = $null
            $originalZoneTransparency = $null
            $zoneStart = $null
            $zoneLength = $null
            $selectedZoneCount = $null
            $zoneTextHash = $null
            $controlWarmupPath = Join-Path $stagingRender (".warmup-control-slide-$($mathRow.slide_index)-shape-$($mathRow.shape_id)-zone-${zoneIndex}.png")
            $controlPath = Join-Path $stagingRender ("control-slide-$($mathRow.slide_index)-shape-$($mathRow.shape_id)-zone-${zoneIndex}.png")
            try {
                $controlPresentation = $application.Presentations.Open($stagingOutput, $true, $false, $true)
                $controlSlide = $controlPresentation.Slides.Item([int]$mathRow.slide_index)
                $controlShape = $controlSlide.Shapes.Item([string]$mathRow.shape_name)
                if ([int]$controlShape.Id -ne [int]$mathRow.shape_id) {
                    throw "Counterfactual shape identity changed for $($mathRow.shape_name)."
                }
                $controlZone = $controlShape.TextFrame2.TextRange.MathZones([int]$zoneIndex, 1)
                $selectedZoneCount = [int]$controlZone.Count
                $zoneStart = [int]$controlZone.Start
                $zoneLength = [int]$controlZone.Length
                if ($selectedZoneCount -ne 1 -or $zoneStart -lt 1 -or $zoneLength -lt 1) {
                    throw "MathZone $zoneIndex is empty for $($mathRow.shape_name)."
                }
                $zoneTextHash = Get-StringSha256 ([string]$controlZone.Text)
                $originalZoneTransparency = [double]$controlZone.Font.Fill.Transparency
                if ($originalZoneTransparency -lt 0 -or $originalZoneTransparency -gt 0.05) {
                    throw "MathZone $zoneIndex has an unresolved or non-opaque baseline transparency."
                }
                $controlZone.Font.Fill.Transparency = 1.0
                $controlSlide.Export($controlWarmupPath, 'PNG', $renderWidth, $renderHeight)
                $controlSlide.Export($controlPath, 'PNG', $renderWidth, $renderHeight)
            }
            finally {
                if ($null -ne $controlPresentation) {
                    try { $controlPresentation.Close() } catch { }
                }
                if ($null -ne $controlZone) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($controlZone) }
                if ($null -ne $controlShape) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($controlShape) }
                if ($null -ne $controlSlide) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($controlSlide) }
                if ($null -ne $controlPresentation) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($controlPresentation) }
                $controlZone = $null
                $controlShape = $null
                $controlSlide = $null
                $controlPresentation = $null
                if (Test-Path -LiteralPath $controlWarmupPath -PathType Leaf) { Remove-Item -LiteralPath $controlWarmupPath -Force }
            }
            if (-not (Test-Path -LiteralPath $controlPath -PathType Leaf) -or
                (Get-Item -LiteralPath $controlPath).Length -le 0) {
                throw "PowerPoint did not create a counterfactual render for $($mathRow.shape_name) MathZone $zoneIndex."
            }
            $counterfactualRenders += [pscustomobject][ordered]@{
                slide_index = [int]$mathRow.slide_index
                shape_name = [string]$mathRow.shape_name
                shape_id = [int]$mathRow.shape_id
                formula_id = [string]$formulaIds[$zoneIndex - 1]
                math_run_index = [int]$zoneIndex
                zone_index = [int]$zoneIndex
                selected_zone_count = [int]$selectedZoneCount
                zone_start = [int]$zoneStart
                zone_length = [int]$zoneLength
                zone_text_sha256 = [string]$zoneTextHash
                path = (Join-Path $renderFull ("control-slide-$($mathRow.slide_index)-shape-$($mathRow.shape_id)-zone-${zoneIndex}.png"))
                sha256 = Get-Sha256 $controlPath
                byte_length = [int64](Get-Item -LiteralPath $controlPath).Length
                width = $renderWidth
                height = $renderHeight
                left = [double]$mathRow.left
                top = [double]$mathRow.top
                shape_width = [double]$mathRow.width
                shape_height = [double]$mathRow.height
                slide_width = [double]$mathRow.slide_width
                slide_height = [double]$mathRow.slide_height
                mutation = 'mathzone-font-fill-transparency-1.0'
                baseline_transparency = $originalZoneTransparency
                exported_at_utc = [DateTime]::UtcNow.ToString('o')
            }
        }
    }
    }
    $stages.counterfactual_rendered = $true

    [System.IO.Directory]::Move($stagingRender, $renderFull)
    $renderWasCommitted = $true
    $stages.render_committed = $true
    [System.IO.File]::Move($stagingOutput, $outputFull)
    $outputWasCommitted = $true
    $stages.output_committed = $true
}
catch {
    $failure = $_.Exception.Message
}
finally {
    if ($null -ne $controlPresentation) { try { $controlPresentation.Close() } catch { } }
    if ($null -ne $reopened) { try { $reopened.Close() } catch { } }
    if ($null -ne $presentation) { try { $presentation.Close() } catch { } }
    if ($null -ne $application -and -not $wasRunning) {
        try { if ($application.Presentations.Count -eq 0) { $application.Quit() } } catch { }
    }
    if ($null -ne $controlPresentation) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($controlPresentation) }
    if ($null -ne $reopened) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($reopened) }
    if ($null -ne $presentation) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($presentation) }
    if ($null -ne $application) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($application) }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    if (Test-Path -LiteralPath $stagingOutput -PathType Leaf) { Remove-Item -LiteralPath $stagingOutput -Force }
    if (Test-Path -LiteralPath $stagingRender -PathType Container) { Remove-Item -LiteralPath $stagingRender -Recurse -Force }
}

$allStagesComplete = @($stages.Values | Where-Object { -not $_ }).Count -eq 0
$status = if ($null -eq $failure -and $violations.Count -eq 0 -and $allStagesComplete) { 'OBSERVED_PASS' } else { 'OBSERVED_FAIL' }
$outputHash = if ($stages.output_committed -and (Test-Path -LiteralPath $outputFull -PathType Leaf)) { Get-Sha256 $outputFull } else { $null }
$payload = [ordered]@{
    document_type = 'POWERPOINT_NATIVE_MATH_ROUNDTRIP_RECEIPT'
    schema_version = '2.0'
    status = $status
    challenge = $Challenge
    powershell_process_id = [int]$PID
    parent_process_id = $ParentProcessId
    audit_profile = $AuditProfile
    minimum_contrast_ratio_required = $minimumContrastRatioRequired
    powerpoint_process_id = $powerPointProcessId
    powerpoint_executable_path = $powerPointExecutablePath
    powerpoint_executable_sha256 = $powerPointExecutableHash
    powerpoint_signature_status = $powerPointSignatureStatus
    powerpoint_signer_subject = $powerPointSignerSubject
    powerpoint_signer_thumbprint = $powerPointSignerThumbprint
    powerpoint_executable_version = $powerPointExecutableVersion
    started_at_utc = $startedAt
    completed_at_utc = [DateTime]::UtcNow.ToString('o')
    input_pptx = $inputFull
    input_sha256 = $inputHash
    output_pptx = $outputFull
    output_sha256 = $outputHash
    expected_plan_path = $planFull
    expected_plan_sha256 = $planHash
    injection_report_path = $injectionReportFull
    injection_report_sha256 = $injectionReportHash
    roundtrip_script_path = $PSCommandPath
    roundtrip_script_sha256 = $scriptHash
    powershell_executable_path = $powerShellExecutablePath
    powershell_executable_sha256 = $powerShellExecutableHash
    powershell_signature_status = $powerShellSignatureStatus
    powershell_signer_subject = $powerShellSignerSubject
    powershell_signer_thumbprint = $powerShellSignerThumbprint
    render_directory = $renderFull
    powerpoint_version = $powerPointVersion
    stages = $stages
    verified_input_formula_styles = $verifiedInputFormulaStyles
    math_shapes = $mathShapes
    shape_inventory = $shapeInventory
    template_inventory = $templateInventory
    renders = $renders
    counterfactual_renders = $counterfactualRenders
    violations = $violations
    failure = $failure
}
Write-AtomicUtf8Json $receiptFull $payload
$payload | ConvertTo-Json -Depth 30
if ($status -ne 'OBSERVED_PASS') { exit 3 }
