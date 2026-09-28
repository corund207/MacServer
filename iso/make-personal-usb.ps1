# Build your personal MacServer USB image on Windows, with the Mac's Wi-Fi firmware
# inside so Wi-Fi works from the first screen. Run it from the MacServer folder:
#
#   powershell -ExecutionPolicy Bypass -File iso\make-personal-usb.ps1
#
# Needs WSL with Debian (once: wsl --install -d Debian, then restart Windows).
# The result is Downloads\macserver-personal-wifi.iso. It contains Apple firmware:
# keep it to yourself, never share or publish it.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$firmware = '/var/tmp/macserver-wifi-firmware.tar'
$built = '/var/tmp/macserver-local/repo/build/macserver-local-personal.iso'
$out = Join-Path $env:USERPROFILE 'Downloads\macserver-personal-wifi.iso'

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Invoke-Linux([string]$script) {
    # Judge Linux commands by exit code only: their progress messages go to stderr,
    # which Windows PowerShell would otherwise treat as errors.
    $ErrorActionPreference = 'Continue'
    wsl.exe -d Debian -u root -- bash -c $script
    if ($LASTEXITCODE -ne 0) { throw "failed in WSL: $script" }
}

wsl.exe -d Debian -- true 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'WSL Debian is not installed. Run: wsl --install -d Debian   then restart Windows and run this again.'
}
Push-Location $repo
try {
    Step 'Tools'
    Invoke-Linux 'command -v rsync >/dev/null || { apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q rsync; }'

    Step 'Wi-Fi firmware (downloaded once from Apple, about 10 minutes the first time)'
    Invoke-Linux "test -s $firmware || bash iso/fetch-wifi-firmware.sh $firmware"

    Step 'Building the image (about 10 minutes the first time, then about 1 minute)'
    Invoke-Linux "MACSERVER_WIFI_FIRMWARE=$firmware bash tests/local.sh image"

    Step "Copying it to $out"
    Copy-Item "\\wsl.localhost\Debian$($built -replace '/', '\')" $out -Force
    $expected = (wsl.exe -d Debian -u root -- sha256sum $built).Split(' ')[0]
    $actual = (Get-FileHash $out -Algorithm SHA256).Hash.ToLower()
    if ($expected -ne $actual) { throw "copy is damaged (checksum differs); run this again" }
    Write-Host "`nDone: $out" -ForegroundColor Green
    Write-Host "SHA-256 $actual"
    Write-Host 'Next: flash it to a USB stick with balenaEtcher (see docs/GUIDE.md, part 3).'
} finally {
    Pop-Location
}
