param([Parameter(ValueFromRemainingArguments = $true)][string[]]$InstallerArgs)
. (Join-Path $PSScriptRoot "install-common.ps1")

$exitCode = Invoke-HydroTuneInstaller `
    -ToolName "Codex" `
    -DefaultTargetDir (Join-Path $HOME ".agents/skills") `
    -ProjectSubdir ".agents/skills" `
    -InstallerArgs $InstallerArgs
exit $exitCode

