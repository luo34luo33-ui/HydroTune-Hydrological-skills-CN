param([Parameter(ValueFromRemainingArguments = $true)][string[]]$InstallerArgs)
. (Join-Path $PSScriptRoot "install-common.ps1")

$exitCode = Invoke-HydroTuneInstaller `
    -ToolName "OpenCode" `
    -DefaultTargetDir (Join-Path $HOME ".config/opencode/skills") `
    -ProjectSubdir ".opencode/skills" `
    -InstallerArgs $InstallerArgs
exit $exitCode

