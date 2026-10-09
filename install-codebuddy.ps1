param([Parameter(ValueFromRemainingArguments = $true)][string[]]$InstallerArgs)
. (Join-Path $PSScriptRoot "install-common.ps1")

$exitCode = Invoke-HydroTuneInstaller `
    -ToolName "CodeBuddy" `
    -DefaultTargetDir (Join-Path $HOME ".codebuddy/skills") `
    -ProjectSubdir ".codebuddy/skills" `
    -InstallerArgs $InstallerArgs
exit $exitCode
