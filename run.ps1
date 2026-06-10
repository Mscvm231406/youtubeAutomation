# Helper for Task Scheduler / manual runs. Activates the venv and runs the pipeline.
# Pass extra args through, e.g.:  .\run.ps1 --limit 1 --privacy private
param([Parameter(ValueFromRemainingArguments = $true)] $Args)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
}

python main.py @Args
