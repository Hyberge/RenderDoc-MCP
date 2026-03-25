<#
.SYNOPSIS
    Build renderdoc.pyd from source and install MCP Server.

.DESCRIPTION
    This script automates the following steps:
    1. Detect Python and Visual Studio environment
    2. Patch python.props to work with standard Python installations
    3. Build renderdoc.pyd using MSBuild
    4. Collect build outputs (renderdoc.pyd, renderdoc.dll)
    5. Install MCP Server via pip
    6. Generate MCP configuration file

.PREREQUISITES
    - Python >= 3.6 (full installation with include headers and libs)
    - Visual Studio 2015 or later (with C++ build tools)
#>

param(
    [string]$Configuration = "Release",
    [string]$Platform = "x64"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# Fix console encoding for MSBuild Chinese output
$OriginalOutputEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

# =============================================================================
# Helper Functions
# =============================================================================

function Write-Step {
    param([string]$Message)
    Write-Host "`n[STEP] $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "  [OK] $Message" -ForegroundColor Green
}

function Write-Error-Exit {
    param([string]$Message, [string]$Suggestion = "")
    Write-Host "`n[ERROR] $Message" -ForegroundColor Red
    if ($Suggestion) {
        Write-Host "`nSuggestion: $Suggestion" -ForegroundColor Yellow
    }
    exit 1
}

function Test-Command {
    param([string]$Command)
    $null = Get-Command $Command -ErrorAction SilentlyContinue
    return $?
}

function Invoke-MSBuildProject {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectPath,
        [Parameter(Mandatory = $true)][string]$DisplayName
    )

    if (-not (Test-Path $ProjectPath)) {
        Write-Error-Exit "$DisplayName not found: $ProjectPath"
    }

    $FileLogger = "/flp:LogFile=$BuildLogFile;Verbosity=normal;Encoding=UTF-8;Append"
    $ProjectBuildArgs = @(
        $ProjectPath,
        "/p:Configuration=$Configuration",
        "/p:Platform=$Platform",
        "/p:PlatformToolset=$PlatformToolset",
        "/p:SolutionDir=$($RenderDocSlnDir + '\')",
        "/m",
        "/v:minimal",
        $FileLogger
    )

    Write-Host "  Running: MSBuild $DisplayName"
    $ProjectBuildResult = & $MsBuildPath @ProjectBuildArgs 2>&1
    $ProjectBuildExitCode = $LASTEXITCODE

    if ($ProjectBuildExitCode -ne 0) {
        Write-Host "`n[Build Output]" -ForegroundColor Yellow
        Write-Host $ProjectBuildResult
        Write-Error-Exit `
            "$DisplayName build failed with exit code $ProjectBuildExitCode" `
            "Check the build log at: $BuildLogFile"
    }
}


# =============================================================================
# Step 1: Environment Detection
# =============================================================================

Write-Step "Detecting Python environment..."

$PythonExe = $null
$PythonPrefix = $null
$PythonVersion = $null
$PythonMajorMinor = $null

if (Test-Command "python") {
    $PythonExe = (Get-Command python).Source
}
elseif (Test-Command "py") {
    $pyResult = py --list 2>$null
    if ($LASTEXITCODE -eq 0 -and $pyResult) {
        $pyLine = $pyResult | Where-Object { $_ -match "-V3\." } | Select-Object -First 1
        if ($pyLine -match "^\s*(-V[\d.]+)\s+") {
            $pyVersion = $Matches[1]
            $PythonExe = py $pyVersion -c "import sys; print(sys.executable)" 2>$null
        }
    }
}

if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    Write-Error-Exit `
        "Python not found in PATH." `
        "Please install Python >= 3.6 from https://www.python.org`nMake sure to check 'Add Python to PATH' during installation."
}

$PythonInfo = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}|{sys.prefix}|{sys.version_info.major}{sys.version_info.minor}')" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error-Exit "Failed to get Python version information."
}

$PythonVersion, $PythonPrefix, $PythonMajorMinor = $PythonInfo.Split('|')
$PythonMajor = [int]($PythonVersion.Split('.')[0])
$PythonMinor = [int]($PythonVersion.Split('.')[1])

Write-Success "Python $PythonVersion found at: $PythonPrefix"
Write-Host "  Python executable: $PythonExe"
Write-Host "  PythonMajorMinor: $PythonMajorMinor"

if ($PythonMajor -lt 3 -or ($PythonMajor -eq 3 -and $PythonMinor -lt 6)) {
    Write-Error-Exit `
        "Python version $PythonVersion is too old. Required: >= 3.6" `
        "Please upgrade Python from https://www.python.org"
}

$PythonInclude = Join-Path $PythonPrefix "include\Python.h"
$PythonLib = Join-Path $PythonPrefix "libs\python$PythonMajorMinor.lib"
$PythonLibAlt = Join-Path $PythonPrefix "python$PythonMajorMinor.lib"

if (-not (Test-Path $PythonInclude)) {
    Write-Error-Exit `
        "Python development headers not found: $PythonInclude" `
        "Please reinstall Python with development headers and libraries included."
}

if (-not (Test-Path $PythonLib) -and -not (Test-Path $PythonLibAlt)) {
    Write-Error-Exit `
        "Python import library not found: $PythonLib" `
        "Please reinstall Python with development headers and libraries included."
}

Write-Success "Python development files verified"

# =============================================================================
# Step 1b: Detect Visual Studio / MSBuild
# =============================================================================

Write-Step "Detecting Visual Studio / MSBuild..."

$VswherePath = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$VsInstallPath = $null
$VsVersion = $null
$PlatformToolset = $null
$MsBuildPath = $null

if (Test-Path $VswherePath) {
    $VsInstallPath = & $VswherePath -latest -products * -requires Microsoft.Component.MSBuild -property installationPath 2>$null
    if ($VsInstallPath -and (Test-Path $VsInstallPath)) {
        $VsVersion = & $VswherePath -latest -products * -requires Microsoft.Component.MSBuild -property catalog_productLineVersion 2>$null
        $MsBuildPath = & $VswherePath -latest -products * -requires Microsoft.Component.MSBuild -find MSBuild\**\Bin\MSBuild.exe 2>$null | Select-Object -First 1
    }
}

if (-not $MsBuildPath) {
    $Vs2015Candidates = @(
        "${env:ProgramFiles(x86)}\MSBuild\14.0\Bin\MSBuild.exe",
        "${env:ProgramFiles}\MSBuild\14.0\Bin\MSBuild.exe"
    ) | Where-Object { Test-Path $_ }

    if ($Vs2015Candidates) {
        $MsBuildPath = $Vs2015Candidates | Select-Object -First 1
        $VsInstallPath = "Visual Studio 2015 / MSBuild 14.0"
        $VsVersion = "2015"
    }
}

if (-not $MsBuildPath -or -not (Test-Path $MsBuildPath)) {
    Write-Error-Exit `
        "MSBuild.exe not found." `
        "Please install Visual Studio 2015 or later with C++ build tools."
}

$PlatformToolset = switch ($VsVersion) {
    "2015" { "v140" }
    "2017" { "v141" }
    "2019" { "v142" }
    "2022" { "v143" }
    default {
        if ($MsBuildPath -match "\\14\.0\\") { "v140" }
        elseif ($MsBuildPath -match "\\2017\\") { "v141" }
        elseif ($MsBuildPath -match "\\2019\\") { "v142" }
        elseif ($MsBuildPath -match "\\2022\\") { "v143" }
        else { "v143" }
    }
}

Write-Success "Visual Studio / MSBuild found"
Write-Host "  VS Install Path: $VsInstallPath"
Write-Host "  VS Version: $VsVersion"
Write-Host "  MSBuild: $MsBuildPath"
Write-Host "  PlatformToolset: $PlatformToolset"

# =============================================================================
# Step 2: Patch python.props
# =============================================================================

Write-Step "Patching python.props for standard Python installation..."

$PythonPropsPath = Join-Path $ScriptDir "renderdoc\qrenderdoc\Code\pyrenderdoc\python.props"
if (-not (Test-Path $PythonPropsPath)) {
    Write-Error-Exit "python.props not found: $PythonPropsPath"
}

$PythonPropsContent = Get-Content $PythonPropsPath -Raw

if ($PythonPropsContent -match "Exists\('\$\(PythonOverride\)\\python\$\(PythonMajorMinorTest\)\.zip'\)") {
    Write-Host "  Applying patch to remove .zip file checks..."

    $PythonPropsContent = $PythonPropsContent -replace "\s*AND\s*Exists\('\$\(PythonOverride\)\\python\$\(PythonMajorMinorTest\)\.zip'\)", ""
    $PythonPropsContent = $PythonPropsContent -replace 'Check for \$\(PythonOverride\)\\include\\Python\.h, \$\(PythonOverride\)\\pythonMAJMIN\.zip, and either', 'Check for $(PythonOverride)\include\Python.h and either'

    [System.IO.File]::WriteAllText($PythonPropsPath, $PythonPropsContent, [System.Text.UTF8Encoding]::new($false))
    Write-Success "python.props patched successfully"
}
else {
    Write-Success "python.props already patched (no .zip checks found)"
}

# Patch pyrenderdoc_module.vcxproj: add /wd4996 for SWIG-generated code.
# Python >= 3.13 deprecated PyWeakref_GET_OBJECT which SWIG still emits,
# and the project enables /WX (warnings-as-errors), causing C2220.
if ($PythonMinor -ge 13) {
    Write-Step "Patching pyrenderdoc_module.vcxproj for Python $PythonVersion deprecated API warnings..."

    $VcxprojPath = Join-Path $ScriptDir "renderdoc\qrenderdoc\Code\pyrenderdoc\pyrenderdoc_module.vcxproj"
    if (-not (Test-Path $VcxprojPath)) {
        Write-Error-Exit "pyrenderdoc_module.vcxproj not found: $VcxprojPath"
    }

    $VcxprojContent = Get-Content $VcxprojPath -Raw

    if ($VcxprojContent -match '4127;4189;4456;4459;4701;4244;4706;4101;' -and $VcxprojContent -notmatch '4996') {
        Write-Host "  Adding /wd4996 to SWIG generated file's DisableSpecificWarnings..."
        $VcxprojContent = $VcxprojContent -replace `
            '4127;4189;4456;4459;4701;4244;4706;4101;%\(DisableSpecificWarnings\)', `
            '4127;4189;4456;4459;4701;4244;4706;4101;4996;%(DisableSpecificWarnings)'
        [System.IO.File]::WriteAllText($VcxprojPath, $VcxprojContent, [System.Text.UTF8Encoding]::new($false))
        Write-Success "pyrenderdoc_module.vcxproj patched (added /wd4996)"
    }
    else {
        Write-Success "pyrenderdoc_module.vcxproj already has /wd4996 or no matching pattern"
    }
}

# =============================================================================
# Step 3: Build renderdoc.pyd
# =============================================================================

Write-Step "Building renderdoc.pyd..."

$RenderDocSlnDir = Join-Path $ScriptDir "renderdoc"
$RenderDocSln = Join-Path $RenderDocSlnDir "renderdoc.sln"
if (-not (Test-Path $RenderDocSln)) {
    Write-Error-Exit "renderdoc.sln not found: $RenderDocSln"
}

# Build the .vcxproj directly instead of using .sln /t: target syntax,
# because the metaproj generated from .sln does not expose per-project targets.
# MSBuild will automatically build all ProjectReference dependencies (renderdoc.dll etc.)
$PyModuleVcxproj = Join-Path $RenderDocSlnDir "qrenderdoc\Code\pyrenderdoc\pyrenderdoc_module.vcxproj"
if (-not (Test-Path $PyModuleVcxproj)) {
    Write-Error-Exit "pyrenderdoc_module.vcxproj not found: $PyModuleVcxproj"
}

$BuildOutputDir = Join-Path $ScriptDir "build\$Platform"
$BuildLogFile = Join-Path $BuildOutputDir "build.log"

if (-not (Test-Path $BuildOutputDir)) {
    New-Item -ItemType Directory -Path $BuildOutputDir -Force | Out-Null
}

if (Test-Path $BuildLogFile) {
    Remove-Item $BuildLogFile -Force
}

Write-Host "  Configuration: $Configuration"
Write-Host "  Platform: $Platform"
Write-Host "  Build log: $BuildLogFile"

$env:RENDERDOC_PYTHON_PREFIX64 = $PythonPrefix

# When building pyrenderdoc_module.vcxproj directly, MSBuild loses the solution-only
# dependencies from renderdoc.sln (breakpad_common/crash_generation_*/exception_handler).
# Pre-build those Release-only static libraries first, then build the python module.
$PreBuildProjects = @()
if ($Configuration -eq "Release") {
    $PreBuildProjects = @(
        @{ Name = "breakpad_common"; Path = Join-Path $RenderDocSlnDir "renderdoc\3rdparty\breakpad\client\windows\common.vcxproj" },
        @{ Name = "crash_generation_client"; Path = Join-Path $RenderDocSlnDir "renderdoc\3rdparty\breakpad\client\windows\crash_generation\crash_generation_client.vcxproj" },
        @{ Name = "crash_generation_server"; Path = Join-Path $RenderDocSlnDir "renderdoc\3rdparty\breakpad\client\windows\crash_generation\crash_generation_server.vcxproj" },
        @{ Name = "exception_handler"; Path = Join-Path $RenderDocSlnDir "renderdoc\3rdparty\breakpad\client\windows\handler\exception_handler.vcxproj" }
    )
}

if ($PreBuildProjects.Count -gt 0) {
    Write-Host "  Pre-building Release breakpad dependencies from renderdoc.sln..."
    foreach ($Project in $PreBuildProjects) {
        Invoke-MSBuildProject -ProjectPath $Project.Path -DisplayName $Project.Name
    }
}

Invoke-MSBuildProject -ProjectPath $PyModuleVcxproj -DisplayName "pyrenderdoc_module"

Write-Success "Build completed successfully"


# =============================================================================
# Step 4: Collect build outputs and clean up
# =============================================================================

Write-Step "Collecting build outputs..."

# Build products live under renderdoc\<Platform>\<Configuration>
$BuildProductDir = Join-Path $ScriptDir "renderdoc\$Platform\$Configuration"
$PyModulesDir = Join-Path $BuildProductDir "pymodules"

if (-not (Test-Path $BuildProductDir)) {
    Write-Error-Exit `
        "Build product directory not found: $BuildProductDir" `
        "Check the build log at: $BuildLogFile"
}

# Collect all .pyd files from pymodules dir
$PydFiles = Get-ChildItem -Path $PyModulesDir -Filter "*.pyd" -ErrorAction SilentlyContinue
if (-not $PydFiles -or $PydFiles.Count -eq 0) {
    Write-Error-Exit `
        "No .pyd files found in: $PyModulesDir" `
        "Check the build log at: $BuildLogFile"
}

# Collect all .dll files from both the main dir and pymodules dir
$DllFiles = @()
$DllFiles += Get-ChildItem -Path $BuildProductDir -Filter "*.dll" -ErrorAction SilentlyContinue
$DllFiles += Get-ChildItem -Path $PyModulesDir -Filter "*.dll" -ErrorAction SilentlyContinue
# Deduplicate by filename (prefer main dir version, pymodules may have duplicates)
$DllMap = @{}
foreach ($dll in $DllFiles) {
    if (-not $DllMap.ContainsKey($dll.Name)) {
        $DllMap[$dll.Name] = $dll
    }
}
$UniqueDlls = $DllMap.Values

Write-Host "  Build product dir: $BuildProductDir"
Write-Host "  Found .pyd files:"
foreach ($f in $PydFiles) { Write-Host "    $($f.FullName)" }
Write-Host "  Found .dll files:"
foreach ($f in $UniqueDlls) { Write-Host "    $($f.FullName)" }

# Copy all to output dir
foreach ($f in $PydFiles) {
    Copy-Item $f.FullName -Destination $BuildOutputDir -Force
    Write-Success "Copied $($f.Name) to: $BuildOutputDir"
}
foreach ($f in $UniqueDlls) {
    Copy-Item $f.FullName -Destination $BuildOutputDir -Force
    Write-Success "Copied $($f.Name) to: $BuildOutputDir"
}




# =============================================================================
# Step 5: Install MCP Server
# =============================================================================

Write-Step "Installing MCP Server..."

$McpServerPath = Join-Path $ScriptDir "mcpserver"
if (-not (Test-Path $McpServerPath)) {
    Write-Error-Exit "MCP Server directory not found: $McpServerPath"
}

# Temporarily relax ErrorActionPreference — pip writes warnings (e.g. "Scripts
# not on PATH") to stderr, which PowerShell treats as terminating errors under
# $ErrorActionPreference = "Stop".
$SavedEAP = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"

# Uninstall any previous (possibly broken) editable install first.
# If pip uninstall fails (e.g. missing RECORD file from an interrupted install),
# manually remove the residual dist-info/egg-link from site-packages.
& $PythonExe -m pip uninstall -y renderdoc-mcp 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  pip uninstall failed, cleaning up residual files manually..."
    $SitePackages = & $PythonExe -c "import sysconfig; print(sysconfig.get_path('purelib'))" 2>$null
    if ($SitePackages -and (Test-Path $SitePackages)) {
        $Residuals = Get-ChildItem -Path $SitePackages -Filter "renderdoc_mcp*" -ErrorAction SilentlyContinue
        foreach ($r in $Residuals) {
            Write-Host "    Removing: $($r.FullName)"
            Remove-Item -Path $r.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
        # Also remove any .egg-link or .pth file
        $EggLinks = Get-ChildItem -Path $SitePackages -Filter "renderdoc-mcp*" -ErrorAction SilentlyContinue
        foreach ($r in $EggLinks) {
            Write-Host "    Removing: $($r.FullName)"
            Remove-Item -Path $r.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

$PipResult = & $PythonExe -m pip install -e $McpServerPath 2>&1
$PipExitCode = $LASTEXITCODE
$ErrorActionPreference = $SavedEAP

if ($PipExitCode -ne 0) {
    Write-Host ($PipResult | Out-String)
    Write-Error-Exit "Failed to install MCP Server"
}

Write-Success "MCP Server installed successfully"

# =============================================================================
# Step 6: Generate MCP Configuration
# =============================================================================

Write-Step "Generating MCP configuration..."

$McpConfigPath = Join-Path $ScriptDir "mcp.json"
$McpModuleDir = $BuildOutputDir

# Build JSON manually to ensure correct indentation (tabs) and key order
$EscapedPython = $PythonExe.Replace('\', '\\')
$EscapedModuleDir = $McpModuleDir.Replace('\', '\\')
$McpConfigJson = @"
{
	"mcpServers": {
		"renderdoc": {
			"command": "$EscapedPython",
			"args": [
				"-m",
				"renderdoc_mcp.server"
			],
			"env": {
				"RENDERDOC_MODULE_PATH": "$EscapedModuleDir"
			}
		}
	}
}
"@
[System.IO.File]::WriteAllText($McpConfigPath, $McpConfigJson, [System.Text.UTF8Encoding]::new($false))

Write-Success "MCP configuration saved to: $McpConfigPath"

# =============================================================================
# Summary
# =============================================================================

Write-Host ("`n" + ("=" * 60)) -ForegroundColor Cyan
Write-Host "Build and installation completed successfully!" -ForegroundColor Green
Write-Host (("=" * 60)) -ForegroundColor Cyan

Write-Host "`nBuild outputs in $BuildOutputDir :" -ForegroundColor Yellow
Get-ChildItem -Path $BuildOutputDir -Include '*.pyd','*.dll' -File -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "  $($_.Name)  ($([math]::Round($_.Length/1MB, 1)) MB)"
}
Write-Host "  MCP config: $McpConfigPath"

Write-Host "`nConfiguration for Claude Desktop / Cursor / VS Code:" -ForegroundColor Yellow
Write-Host "  Copy the contents of mcp.json to your MCP client configuration file."
Write-Host ""
Write-Host "  Claude Desktop: %APPDATA%\Claude\claude_desktop_config.json"
Write-Host "  Cursor:         ~/.cursor/mcp.json"
Write-Host "  VS Code:        Add to .vscode/mcp.json"

Write-Host "`nEnvironment variable set:" -ForegroundColor Yellow
Write-Host "  RENDERDOC_MODULE_PATH=$BuildOutputDir"

Write-Host "`nOpening mcp.json with default text editor..." -ForegroundColor Cyan
Start-Process $McpConfigPath

# Restore original console encoding
[Console]::OutputEncoding = $OriginalOutputEncoding
