<#
  uninstall-msys2.ps1
  Removes the MSYS2 / pacman setup that was installed for WeasyPrint (PDF export):
    - MSYS2 (winget uninstall, then folder cleanup)
    - WEASYPRINT_DLL_DIRECTORIES environment variable
    - msys64 entries in PATH (user + machine)
    - Start Menu shortcuts
    - optional: pip packages weasyprint / qrcode

  Usage (PowerShell):
    powershell -ExecutionPolicy Bypass -File .\scripts\uninstall-msys2.ps1
    powershell -ExecutionPolicy Bypass -File .\scripts\uninstall-msys2.ps1 -RemovePythonPackages
    powershell -ExecutionPolicy Bypass -File .\scripts\uninstall-msys2.ps1 -MsysRoot "D:\msys64" -Force

  Run as Administrator to also clean machine-level PATH / Start Menu entries.
#>
param(
    [string]$MsysRoot = "C:\msys64",
    [switch]$RemovePythonPackages,
    [switch]$Force
)

$ErrorActionPreference = "Continue"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Host "MSYS2 root      : $MsysRoot"
Write-Host "Administrator   : $isAdmin"
Write-Host "Remove pip pkgs : $RemovePythonPackages"
Write-Host ""
Write-Host "After this, the PDF download in Menu Designer will stop working"
Write-Host "(the rest of the app is not affected)."
if (-not $Force) {
    $ans = Read-Host "Continue? (y/N)"
    if ($ans -notmatch '^(y|yes)$') { Write-Host "Cancelled."; exit 0 }
}

# 1) Stop running MSYS2 processes (they lock files)
Write-Host "`n[1/6] Stopping MSYS2 processes..."
Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path -like "$MsysRoot*" } |
    ForEach-Object { Write-Host "  stopping $($_.ProcessName)"; Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }

# 2) Uninstall via winget (works if it was installed with winget)
Write-Host "`n[2/6] winget uninstall (if installed that way)..."
if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget uninstall --id MSYS2.MSYS2 --silent --disable-interactivity 2>&1 | Out-Host
} else {
    Write-Host "  winget not found, skipping."
}

# 3) Remove leftover folder
Write-Host "`n[3/6] Removing folder $MsysRoot ..."
if (Test-Path $MsysRoot) {
    Remove-Item -Path $MsysRoot -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $MsysRoot) {
        cmd /c "rmdir /s /q `"$MsysRoot`"" 2>$null   # fallback for long paths / locked attributes
    }
    if (Test-Path $MsysRoot) { Write-Warning "  Could not fully remove $MsysRoot. Close all terminals, reboot and delete it manually." }
    else { Write-Host "  removed." }
} else {
    Write-Host "  not found (already removed)."
}

# 4) Environment variable + PATH entries
Write-Host "`n[4/6] Cleaning environment variables..."
$targets = @(@{ Name = "User"; Hive = [Microsoft.Win32.Registry]::CurrentUser; Sub = "Environment" })
if ($isAdmin) {
    $targets += @{ Name = "Machine"; Hive = [Microsoft.Win32.Registry]::LocalMachine;
                   Sub = "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" }
} else {
    Write-Host "  (not admin: machine-level PATH is left untouched)"
}
$rootPattern = [regex]::Escape($MsysRoot.TrimEnd('\'))
foreach ($t in $targets) {
    try {
        $key = $t.Hive.OpenSubKey($t.Sub, $true)
        if ($key.GetValue("WEASYPRINT_DLL_DIRECTORIES", $null) -ne $null) {
            $key.DeleteValue("WEASYPRINT_DLL_DIRECTORIES")
            Write-Host "  [$($t.Name)] removed WEASYPRINT_DLL_DIRECTORIES"
        }
        # read PATH unexpanded so %SystemRoot% etc. stay intact
        $old = $key.GetValue("Path", $null, "DoNotExpandEnvironmentNames")
        if ($old) {
            $parts = $old -split ";" | Where-Object { $_ -and $_ -notmatch $rootPattern -and $_ -notmatch "(?i)\\msys64(\\|$)" }
            $new = $parts -join ";"
            if ($new -ne $old) {
                $key.SetValue("Path", $new, [Microsoft.Win32.RegistryValueKind]::ExpandString)
                Write-Host "  [$($t.Name)] removed msys64 entries from PATH"
            }
        }
        $key.Close()
    } catch {
        Write-Warning "  [$($t.Name)] $($_.Exception.Message)"
    }
}
# current session
Remove-Item Env:\WEASYPRINT_DLL_DIRECTORIES -ErrorAction SilentlyContinue
# broadcast the change to other apps (set + delete a dummy var triggers WM_SETTINGCHANGE)
[Environment]::SetEnvironmentVariable("MSYS2_CLEANUP_TMP", "1", "User")
[Environment]::SetEnvironmentVariable("MSYS2_CLEANUP_TMP", $null, "User")

# 5) Start Menu shortcuts
Write-Host "`n[5/6] Removing Start Menu shortcuts..."
$menus = @("$env:APPDATA\Microsoft\Windows\Start Menu\Programs")
if ($isAdmin) { $menus += "$env:ProgramData\Microsoft\Windows\Start Menu\Programs" }
foreach ($m in $menus) {
    Get-ChildItem -Path $m -Filter "MSYS2*" -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host "  removing $($_.FullName)"; Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
}

# 6) Optional: Python packages installed for PDF / QR
Write-Host "`n[6/6] Python packages..."
if ($RemovePythonPackages) {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) {
        Write-Host "  (activate the project's venv first if you used one)"
        python -m pip uninstall -y weasyprint qrcode
    } else {
        Write-Host "  python not found, skipping."
    }
} else {
    Write-Host "  skipped (use -RemovePythonPackages to also uninstall weasyprint and qrcode)."
}

Write-Host "`nDone. Open a NEW terminal so the updated PATH takes effect."
