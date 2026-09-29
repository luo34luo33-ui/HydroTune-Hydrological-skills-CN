Set-StrictMode -Version Latest

function Invoke-HydroTuneInstaller {
    param(
        [Parameter(Mandatory = $true)][string]$ToolName,
        [Parameter(Mandatory = $true)][string]$DefaultTargetDir,
        [Parameter(Mandatory = $true)][string]$ProjectSubdir,
        [Parameter()][string[]]$InstallerArgs = @()
    )

    $pythonCommand = $env:HYDROTUNE_PYTHON
    if ([string]::IsNullOrWhiteSpace($pythonCommand)) {
        $pythonCommand = "python"
    }
    $engine = Join-Path $PSScriptRoot "scripts/install_skills.py"
    & $pythonCommand $engine `
        --repo-root $PSScriptRoot `
        --tool-name $ToolName `
        --default-target $DefaultTargetDir `
        --project-subdir $ProjectSubdir `
        @InstallerArgs | Out-Host
    return [int]$LASTEXITCODE
}

