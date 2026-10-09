param([Parameter(ValueFromRemainingArguments = $true)][string[]]$InstallerArgs)
. (Join-Path $PSScriptRoot "install-common.ps1")

$exitCode = Invoke-HydroTuneInstaller `
    -ToolName "Qwen Code" `
    -DefaultTargetDir (Join-Path $HOME ".qwen/skills") `
    -ProjectSubdir ".qwen/skills" `
    -InstallerArgs $InstallerArgs
exit $exitCode
