param(
  [string]$Repository = "Nonary/vibeshine_truehdr_runtime",
  [string]$Tag = "v1.0.0",
  [string]$AssetName = "",
  [string]$OutDir = "",
  [string]$GitHubToken = ""
)

$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[truehdr-runtime] $Message"
}

function Get-Sha256 {
  param([string]$Path)
  $stream = [System.IO.File]::OpenRead($Path)
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    return [System.BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
  } finally {
    $sha.Dispose()
    $stream.Dispose()
  }
}

function New-GitHubHeaders {
  param([string]$Token = "")

  $headers = @{
    "Accept" = "application/vnd.github+json"
    "User-Agent" = "vibeshine-truehdr-runtime-downloader"
    "X-GitHub-Api-Version" = "2022-11-28"
  }

  if ($Token) {
    $headers["Authorization"] = "Bearer $Token"
  }

  return $headers
}

if ([string]::IsNullOrWhiteSpace($Repository)) {
  throw "TrueHDR runtime repository is required."
}
if ([string]::IsNullOrWhiteSpace($Tag)) {
  throw "TrueHDR runtime release tag is required."
}

$scriptRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $OutDir) {
  $OutDir = if ($env:SUNSHINE_TRUEHDR_RUNTIME_DIR) {
    $env:SUNSHINE_TRUEHDR_RUNTIME_DIR
  } else {
    Join-Path $scriptRoot ".vibeshine-deps\truehdr-runtime"
  }
}
$OutDir = [System.IO.Path]::GetFullPath($OutDir)

if (-not $GitHubToken) {
  $GitHubToken = if ($env:TRUEHDR_RUNTIME_TOKEN) {
    $env:TRUEHDR_RUNTIME_TOKEN
  } elseif ($env:GH_TOKEN) {
    $env:GH_TOKEN
  } elseif ($env:GITHUB_TOKEN) {
    $env:GITHUB_TOKEN
  } else {
    ""
  }
}

$version = $Tag -replace '^v', ''
if ([string]::IsNullOrWhiteSpace($AssetName)) {
  $AssetName = "vibeshine-truehdr-runtime-$version-windows-x64.zip"
}

# The release tag is mutable and these DLLs end up inside the signed MSI: the archive and each
# DLL must match the pins committed next to this script. An unpinned release is refused unless
# TRUEHDR_RUNTIME_ALLOW_UNPINNED=1 (local experiments only; never set in CI).
$pinsPath = Join-Path $PSScriptRoot 'truehdr_runtime_pins.json'
$pins = (Get-Content -LiteralPath $pinsPath -Raw | ConvertFrom-Json)
$repoPins = $pins.PSObject.Properties[$Repository]
$pin = if ($repoPins) { $repoPins.Value.PSObject.Properties[$Tag] } else { $null }
$pin = if ($pin) { $pin.Value } else { $null }
if (-not $pin) {
  if ($env:TRUEHDR_RUNTIME_ALLOW_UNPINNED -eq '1') {
    Write-Step "WARNING: no pin for $Repository@$Tag; continuing because TRUEHDR_RUNTIME_ALLOW_UNPINNED=1"
  } else {
    throw "No SHA-256 pin for TrueHDR runtime $Repository@$Tag in $pinsPath; add one before packaging it."
  }
} elseif ($pin.asset -and $pin.asset -ne $AssetName) {
  throw "TrueHDR runtime asset '$AssetName' is not the pinned asset '$($pin.asset)' for $Repository@$Tag."
}

function Assert-PinnedHash {
  param([string]$Path, [string]$Expected, [string]$What)
  if (-not $Expected) { return }
  $actual = Get-Sha256 -Path $Path
  if ($actual -ne $Expected.ToLowerInvariant()) {
    throw "TrueHDR runtime $What hash mismatch: expected $Expected, got $actual ($Path)."
  }
}

$requiredDlls = @("vibeshine_truehdr.dll", "nvngx_truehdr.dll")
$missingOrEmpty = @()
foreach ($dll in $requiredDlls) {
  $path = Join-Path $OutDir $dll
  $item = Get-Item -LiteralPath $path -ErrorAction SilentlyContinue
  if (-not $item -or $item.Length -le 0) {
    $missingOrEmpty += $dll
  } elseif ($pin -and $pin.files.PSObject.Properties[$dll] -and (Get-Sha256 -Path $path) -ne $pin.files.PSObject.Properties[$dll].Value) {
    Write-Step "Staged $dll does not match its pin; re-downloading"
    $missingOrEmpty += $dll
  }
}

if ($missingOrEmpty.Count -eq 0) {
  Write-Step "Pinned runtime already staged in $OutDir"
  Get-ChildItem -LiteralPath $OutDir -File | Where-Object { $_.Name -in $requiredDlls } | ForEach-Object {
    Write-Step ("{0}  {1} bytes  sha256={2}" -f $_.Name, $_.Length, (Get-Sha256 -Path $_.FullName))
  }
  exit 0
}

Write-Step "Missing required runtime file(s): $($missingOrEmpty -join ', ')"

$headers = New-GitHubHeaders -Token $GitHubToken
$releaseUri = "https://api.github.com/repos/$Repository/releases/tags/$([System.Uri]::EscapeDataString($Tag))"
Write-Step "Resolving $Repository release $Tag"
try {
  $release = Invoke-RestMethod -Uri $releaseUri -Headers $headers
} catch {
  if (-not $GitHubToken) {
    throw
  }

  Write-Step "Authenticated release lookup failed; retrying public lookup without a token"
  $headers = New-GitHubHeaders
  $release = Invoke-RestMethod -Uri $releaseUri -Headers $headers
}

$asset = @($release.assets | Where-Object { $_.name -eq $AssetName } | Select-Object -First 1)
if ($asset.Count -eq 0) {
  throw "Release '$Repository@$Tag' does not contain asset '$AssetName'."
}

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "vibeshine-truehdr-$([System.Guid]::NewGuid().ToString('N'))"
$downloadDir = Join-Path $tempRoot "download"
$extractDir = Join-Path $tempRoot "extract"
New-Item -ItemType Directory -Path $downloadDir, $extractDir | Out-Null

try {
  $archivePath = Join-Path $downloadDir $AssetName
  $downloadHeaders = New-GitHubHeaders -Token $GitHubToken
  $downloadHeaders["Accept"] = "application/octet-stream"

  Write-Step "Downloading $AssetName"
  try {
    Invoke-WebRequest -Uri $asset.url -Headers $downloadHeaders -OutFile $archivePath
  } catch {
    if (-not $GitHubToken) {
      throw
    }

    Write-Step "Authenticated asset download failed; retrying public download without a token"
    $downloadHeaders = New-GitHubHeaders
    $downloadHeaders["Accept"] = "application/octet-stream"
    Invoke-WebRequest -Uri $asset.url -Headers $downloadHeaders -OutFile $archivePath
  }

  if ($pin) { Assert-PinnedHash -Path $archivePath -Expected $pin.archive_sha256 -What "archive $AssetName" }
  Expand-Archive -LiteralPath $archivePath -DestinationPath $extractDir -Force

  $requiredDlls = @("vibeshine_truehdr.dll", "nvngx_truehdr.dll")
  $resolved = @{}
  foreach ($dll in $requiredDlls) {
    $matches = @(Get-ChildItem -LiteralPath $extractDir -Recurse -File -Filter $dll)
    if ($matches.Count -ne 1) {
      Get-ChildItem -LiteralPath $extractDir -Recurse -File | ForEach-Object { Write-Host $_.FullName }
      throw "Expected exactly one '$dll' in TrueHDR runtime archive, found $($matches.Count)."
    }
    if ($matches[0].Length -le 0) {
      throw "TrueHDR runtime file is empty: $($matches[0].FullName)"
    }
    if ($pin -and $pin.files.PSObject.Properties[$dll]) {
      Assert-PinnedHash -Path $matches[0].FullName -Expected $pin.files.PSObject.Properties[$dll].Value -What $dll
    }
    $resolved[$dll] = $matches[0].FullName
  }

  New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

  foreach ($dll in $requiredDlls) {
    Copy-Item -LiteralPath $resolved[$dll] -Destination (Join-Path $OutDir $dll) -Force
  }

  Write-Step "Installed pinned TrueHDR runtime to $OutDir"
  Get-ChildItem -LiteralPath $OutDir -File | ForEach-Object {
    Write-Step ("{0}  {1} bytes  sha256={2}" -f $_.Name, $_.Length, (Get-Sha256 -Path $_.FullName))
  }
} finally {
  if (Test-Path -LiteralPath $tempRoot) {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
}
