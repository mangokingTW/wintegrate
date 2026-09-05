# Quiets a hosted Windows runner for driving a real desktop application.
#
# One definition, called from the composite action by its own path and from this
# repository's workflows by a relative one. It used to be four inline copies, and
# they had already drifted: one ran the WSL install on x64, two had lost the OOBE
# policy, and the Edge policy was written to different keys in different copies.
# The action's header records that the same thing happened once before. A file
# cannot be copied by hand, so it cannot drift.
#
# Everything here is best-effort and idempotent. Nothing repairs the machine.

$ErrorActionPreference = 'Continue'

# WSL: arm64 images ship WSL uninstalled, and the missing component puts up a
# probe popup every 30 seconds that takes the foreground. x64 images do not have
# the problem, and `wsl --update` there is a no-op that exits 0 -- which is how
# one copy of this ran on x64 for months without anyone noticing.
if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
    $p = Start-Process -FilePath wsl.exe -ArgumentList '--update', '--confirm' -NoNewWindow -PassThru
    if (-not $p.WaitForExit(60000)) { $p.Kill(); Write-Host 'wsl --update timed out' }

    # `wsl --status` writes UTF-16; through a pipe the interleaved NULs make the
    # string match nothing, so the encoding is set for the call.
    function Get-WslStatus {
        $prev = [Console]::OutputEncoding
        try {
            [Console]::OutputEncoding = [System.Text.Encoding]::Unicode
            return ((& wsl.exe --status 2>&1 | Out-String) -replace "\0", '')
        } finally { [Console]::OutputEncoding = $prev }
    }
    $status = Get-WslStatus
    Write-Host "WSL status: $status"

    # `wsl --update` alone is a no-op when WSL is absent: there is nothing to
    # update until it exists.
    if ($status -match 'not installed') {
        try {
            $rel = Invoke-RestMethod 'https://api.github.com/repos/microsoft/WSL/releases/latest'
            $asset = $rel.assets | Where-Object { $_.name -match 'arm64\.msi$' } | Select-Object -First 1
            if ($asset) {
                Write-Host "Downloading $($asset.name)..."
                Invoke-WebRequest -Uri $asset.browser_download_url -OutFile wsl-arm64.msi
                Start-Process msiexec -ArgumentList '/i', 'wsl-arm64.msi', '/qn', '/norestart' -Wait
                Write-Host "WSL status after install: $(Get-WslStatus)"
            }
        } catch {
            Write-Host "WSL MSI fallback skipped: $_"
        }
    }
} else {
    Write-Host "Not arm64 ($env:PROCESSOR_ARCHITECTURE); skipping the WSL step"
}

# Edge's first-run page, in both hives and both keys.
foreach ($hive in 'HKLM', 'HKCU') {
    $key = "${hive}:\SOFTWARE\Policies\Microsoft\Edge"
    New-Item -Path $key -Force -ErrorAction SilentlyContinue | Out-Null
    Set-ItemProperty -Path $key -Name 'HideFirstRunExperience' -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue
    Set-ItemProperty -Path $key -Name 'PreventFirstRunPage' -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue
}

# The OOBE privacy experience is a different dialog from Edge's.
$oobe = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\OOBE'
New-Item -Path $oobe -Force -ErrorAction SilentlyContinue | Out-Null
Set-ItemProperty -Path $oobe -Name 'DisablePrivacyExperience' -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue

# Background popups that steal the foreground. None of these hosts a console.
Get-Process -Name 'wsl', 'wslhost', 'msedge', 'msedgewebview2' -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue

# WindowsTerminal is hidden, not killed. On Windows 11 it is also the default
# console host, so Stop-Process ends every console it hosts -- this step's
# included, and on a hosted runner the agent reporting the job with it. What
# steals the foreground is its window; hiding that leaves the process alone.
$u32 = Add-Type -Namespace Quiet -Name User32 -PassThru -MemberDefinition '[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);'
Get-Process -Name 'WindowsTerminal' -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 } |
    ForEach-Object { [void]$u32::ShowWindow($_.MainWindowHandle, 0) }

# wsl.exe exits non-zero when WSL is absent, and this whole file is best-effort.
$global:LASTEXITCODE = 0
exit 0
