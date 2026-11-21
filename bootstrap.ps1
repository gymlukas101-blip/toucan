$ErrorActionPreference = 'Stop'

Push-Location -LiteralPath $PSScriptRoot
try {
    git config --local core.hooksPath .githooks
    if ($LASTEXITCODE -ne 0) { throw 'Could not configure the repository hooks path.' }

    py -3 tools/check_secrets.py
    if ($LASTEXITCODE -ne 0) { throw 'The tracked-file secret check failed.' }

    py -3 tools/validate_memory.py
    if ($LASTEXITCODE -ne 0) { throw 'Memory validation failed.' }
}
finally {
    Pop-Location
}
