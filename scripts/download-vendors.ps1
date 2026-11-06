#!/usr/bin/env pwsh
# web/static/vendor/ is gitignored for repo size reasons.
# Run this script after cloning or when updating versions.
[CmdletBinding()]
param(
	[Alias('F')]
	[switch]$Force,

	[Alias('h')]
	[switch]$Help
)

$ErrorActionPreference = 'Stop'

function Show-Usage {
	@'
Usage: download-vendors.ps1 [OPTIONS]

Download vendored JS/CSS/font files into web/static/vendor/.
The directory is gitignored for repo size reasons; run this script
after cloning or when bumping an upstream version.

Options:
  -F, --force    Re-download every file even if it already exists
				 (use after bumping a version in this script).
  -h, --help     Show this help and exit.

Examples:
  .\scripts\download-vendors.ps1
  .\scripts\download-vendors.ps1 -F
'@
}

if ($Help) {
	Show-Usage
	exit 0
}

# Force TLS 1.2 for Windows PowerShell 5.1 on older Windows builds.
try {
	[Net.ServicePointManager]::SecurityProtocol = `
		[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {
	# Newer runtimes may not support this; ignore.
}

function Format-Size {
	param([long]$Bytes)
	if ($Bytes -ge 1GB) { return ('{0:N1} GB' -f ($Bytes / 1GB)) }
	if ($Bytes -ge 1MB) { return ('{0:N1} MB' -f ($Bytes / 1MB)) }
	if ($Bytes -ge 1KB) { return ('{0:N0} KB' -f ($Bytes / 1KB)) }
	return "$Bytes B"
}

$VendorDir = [System.IO.Path]::GetFullPath(
	(Join-Path $PSScriptRoot '..\web\static\vendor')
)
$FontsDir = Join-Path $VendorDir 'fonts'
New-Item -ItemType Directory -Force -Path $FontsDir | Out-Null

if ($Force) {
	Write-Host 'Force mode: re-downloading all files'
}

function Fetch {
	param(
		[Parameter(Mandatory)][string]$File,
		[Parameter(Mandatory)][string]$Url
	)
	$full = Join-Path $VendorDir $File

	if ((Test-Path -LiteralPath $full) -and -not $Force) {
		Write-Host "  [skip] $File"
		return
	}

	try {
		Invoke-WebRequest -Uri $Url -OutFile $full `
			-UseBasicParsing -TimeoutSec 60 -ErrorAction Stop
	} catch {
		if (Test-Path -LiteralPath $full) {
			Remove-Item -LiteralPath $full -Force -ErrorAction SilentlyContinue
		}
		throw "ERROR: failed to download $Url ($($_.Exception.Message))"
	}

	Write-Host "  [get]  $File"
}

Push-Location $VendorDir
try {
	Write-Host 'Fetching vendor libraries...'
	Fetch 'htmx.min.js'        'https://unpkg.com/htmx.org@1.9.10/dist/htmx.min.js'
	Fetch 'marked.min.js'      'https://cdn.jsdelivr.net/npm/marked@12/marked.min.js'
	Fetch 'alpine.min.js'      'https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js'
	Fetch 'katex.min.js'       'https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js'
	Fetch 'auto-render.min.js' 'https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js'
	Fetch 'katex.min.css'      'https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css'

	Write-Host 'Fetching katex fonts (per-file jsdelivr, list from CSS)...'
	$KatexVersion = '0.16.9'
	$KatexFonts = @(
		'KaTeX_AMS-Regular'
		'KaTeX_Caligraphic-Bold'
		'KaTeX_Caligraphic-Regular'
		'KaTeX_Fraktur-Bold'
		'KaTeX_Fraktur-Regular'
		'KaTeX_Main-Bold'
		'KaTeX_Main-BoldItalic'
		'KaTeX_Main-Italic'
		'KaTeX_Main-Regular'
		'KaTeX_Math-BoldItalic'
		'KaTeX_Math-Italic'
		'KaTeX_SansSerif-Bold'
		'KaTeX_SansSerif-Italic'
		'KaTeX_SansSerif-Regular'
		'KaTeX_Script-Regular'
		'KaTeX_Size1-Regular'
		'KaTeX_Size2-Regular'
		'KaTeX_Size3-Regular'
		'KaTeX_Size4-Regular'
		'KaTeX_Typewriter-Regular'
	)

	foreach ($font in $KatexFonts) {
		Fetch "fonts/$font.woff2" `
			"https://cdn.jsdelivr.net/npm/katex@$KatexVersion/dist/fonts/$font.woff2"
	}

	$allFiles = @(Get-ChildItem -LiteralPath $VendorDir -Recurse -File -ErrorAction SilentlyContinue)
	$jsCss    = @($allFiles | Where-Object { $_.Extension -in '.js', '.css' }).Count
	$woff2    = @(Get-ChildItem -LiteralPath $FontsDir -Filter '*.woff2' -File -ErrorAction SilentlyContinue).Count
	$sizeStr  = Format-Size ([long](($allFiles | Measure-Object -Property Length -Sum).Sum))

	Write-Host "Done. Vendor files in $VendorDir"
	Write-Host "  js/css: $jsCss files"
	Write-Host "  fonts:  $woff2 woff2 files"
	Write-Host "  size:   $sizeStr"
	Get-ChildItem -LiteralPath $VendorDir -Force |
		Format-Table Mode, LastWriteTime, Length, Name -AutoSize

	exit 0
} catch {
	Write-Host "FAILED: $_" -ForegroundColor Red
	exit 1
} finally {
	Pop-Location
}
