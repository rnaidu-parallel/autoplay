param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ChatArguments
)

& (Join-Path $PSScriptRoot '..\harness\run-harness.ps1') chat-agent @ChatArguments
exit $LASTEXITCODE
