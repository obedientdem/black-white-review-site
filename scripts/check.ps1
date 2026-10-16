$ErrorActionPreference='Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH='D:/codex/black-white-review-site/python'
$bwPython='C:/Users/18110/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $bwPython -m unittest discover -s tests -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& 'C:/Users/18110/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe' tests/frontend.test.cjs
exit $LASTEXITCODE
