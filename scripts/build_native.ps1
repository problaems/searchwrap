# Build the native C CLI client (searchc.exe)
# Requires: mingw-w64 gcc on PATH (e.g. `winget install MartinStorsjo.LLVM-MinGW` or scoop install gcc)
# Output: searchc.exe in the repo root (also useful as %USERPROFILE%\bin\searchc.exe)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent
Set-Location (Join-Path $root "native")

gcc -O2 -o searchc.exe searchc_client.c -lws2_32 -lshell32 -municode -mconsole
if ($LASTEXITCODE -ne 0) { throw "gcc failed with $LASTEXITCODE" }
Write-Output "BUILD_OK: $(Join-Path (Get-Location) 'searchc.exe')"

# smoke test against a running daemon (spawns one if needed)
& .\searchc.exe "everything" | Select-Object -First 3
