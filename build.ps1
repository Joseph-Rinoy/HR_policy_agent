# Build the Qubiqon Policy Assistant into a standalone, zippable folder.
#
#   .\build.ps1
#
# Produces dist\QubiqonPolicyAssistant\ (a self-contained app needing no
# Python install) and QubiqonPolicyAssistant.zip ready to hand to someone.

$ErrorActionPreference = "Stop"
$py = ".\venv\Scripts\python.exe"
$name = "QubiqonPolicyAssistant"

if (-not (Test-Path $py)) {
    throw "Could not find $py. Run this from the project root with the venv created."
}

# 1. Make sure PyInstaller is installed in the venv.
& $py -m pip install --quiet pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install pyinstaller failed." }

# 2. Pre-clean previous output. (OneDrive can briefly lock files; retry once.)
foreach ($d in @("build", (Join-Path "dist" $name))) {
    if (Test-Path $d) {
        try {
            Remove-Item -Recurse -Force $d -ErrorAction Stop
        } catch {
            Start-Sleep -Seconds 2
            Remove-Item -Recurse -Force $d -ErrorAction Stop
        }
    }
}

# 3. Build the one-folder, no-console GUI app. Fail loudly if PyInstaller errors
#    (otherwise we would zip a stale build).
& $py -m PyInstaller --noconfirm --windowed --name $name app.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed (exit $LASTEXITCODE)." }

$dist = Join-Path "dist" $name
if (-not (Test-Path (Join-Path $dist "$name.exe"))) {
    throw "Expected exe not found in $dist - build did not produce output."
}

# 3. Drop the runtime files next to the .exe so they can be seen/edited
#    without rebuilding (the app now looks beside the .exe for these).
$destPolicies = Join-Path $dist "policies"
if (Test-Path $destPolicies) { Remove-Item -Recurse -Force $destPolicies }
Copy-Item -Recurse "policies" $destPolicies

if (Test-Path ".env") {
    Copy-Item -Force ".env" (Join-Path $dist ".env")
} else {
    Write-Warning ".env not found - place one next to the .exe or the app won't connect."
}

if (Test-Path "Qubi.png") {
    Copy-Item -Force "Qubi.png" (Join-Path $dist "Qubi.png")
} else {
    Write-Warning "Qubi.png not found - the launcher will fall back to the sparkle icon."
}

# Optional animated assets (the app falls back gracefully if absent).
foreach ($gif in @("Qubi_launcher.gif", "typing.gif")) {
    if (Test-Path $gif) {
        Copy-Item -Force $gif (Join-Path $dist $gif)
    }
}

# 4. Zip the folder for delivery.
$zip = "$name.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $dist -DestinationPath $zip

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  App folder: $dist"
Write-Host "  Deliver:    $zip  (unzip and run $name.exe)"
