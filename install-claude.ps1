param([Parameter(ValueFromRemainingArguments = $true)][string[]]$InstallerArgs)
. (Join-Path $PSScriptRoot "install-common.ps1")

$exitCode = Invoke-HydroTuneInstaller `
    -ToolName "Claude Code" `
    -DefaultTargetDir (Join-Path $HOME ".claude/skills") `
    -ProjectSubdir ".claude/skills" `
    -InstallerArgs $InstallerArgs
exit $exitCode

