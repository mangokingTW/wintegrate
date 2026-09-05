# Quiets a GitHub-hosted Windows runner for driving a real desktop application,
# and reports what it found.
#
# One definition, called from the composite action by its own path and from this
# repository's workflows by a relative one. It used to be four inline copies, and
# they had already drifted: one ran the WSL install on x64, two had lost the OOBE
# policy, and the Edge policy was written to different keys in different copies.
# The action's header records that the same thing happened once before. A file
# cannot be copied by hand, so it cannot drift.
#
# Everything here is either suppression or measurement, and all of it is written
# for a machine nobody owns: it sets HKLM policy, changes the system-wide hard
# error mode, and kills a browser. On a self-hosted runner every one of those is
# somebody's computer, so the whole file is a no-op there, and says so.

$ErrorActionPreference = 'Continue'

$hosted = ($env:GITHUB_ACTIONS -eq 'true') -and ($env:RUNNER_ENVIRONMENT -eq 'github-hosted')
Write-Host "GITHUB_ACTIONS=$($env:GITHUB_ACTIONS) RUNNER_ENVIRONMENT=$($env:RUNNER_ENVIRONMENT) -> hosted=$hosted"
if (-not $hosted) {
    Write-Host 'Not a GitHub-hosted runner; leaving the machine alone.'
    $global:LASTEXITCODE = 0
    exit 0
}

# ErrorMode=2 suppresses hard-error message boxes for the whole system. A runner
# has nobody to click them, so one sits modal on the desktop and takes the
# foreground -- measured on windows-11-arm, which repeatedly raised "Windows
# created a temporary paging file on your computer because of a problem that
# occurred with your paging file configuration" and then produced no window at
# all for notepad.exe. The whole class, rather than that one title: any hard
# error is a modal nobody will answer.
$emKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Windows'
$emWas = (Get-ItemProperty -Path $emKey -Name ErrorMode -ErrorAction SilentlyContinue).ErrorMode
Set-ItemProperty -Path $emKey -Name ErrorMode -Value 2 -ErrorAction SilentlyContinue
Write-Host "ErrorMode: $emWas -> $((Get-ItemProperty -Path $emKey -Name ErrorMode -ErrorAction SilentlyContinue).ErrorMode)"

# Measured, not fixed. The paging-file warning means the machine is already
# short of something, and none of it can be repaired from inside the job:
# automatic pagefile management needs a reboot to take effect, and if the disk
# is what ran out then managing it differently does not make room. What this can
# do is make the next failure attributable instead of arriving as "no window
# appeared".
Get-PSDrive C | Select-Object @{n='Free(GB)';e={[math]::Round($_.Free/1GB,1)}},
                              @{n='Used(GB)';e={[math]::Round($_.Used/1GB,1)}} |
    Format-Table -AutoSize | Out-String | Write-Host
$cs = Get-CimInstance Win32_ComputerSystem
Write-Host "AutomaticManagedPagefile: $($cs.AutomaticManagedPagefile)"
Get-CimInstance Win32_PageFileUsage |
    Select-Object Name, CurrentUsage, AllocatedBaseSize, PeakUsage |
    Format-Table -AutoSize | Out-String | Write-Host

# One may already be up: ErrorMode only governs the next hard error, and this
# one is raised at logon, before any step runs. By window handle, not
# Get-Process | Where MainWindowTitle -- a modal #32770 is not necessarily its
# process's main window, and if it belongs to explorer, Stop-Process would take
# the desktop with it.
$sig = @(
    '[DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr p);'
    'public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr p);'
    '[DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);'
    '[DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, System.Text.StringBuilder s, int n);'
    '[DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);'
    '[DllImport("user32.dll")] public static extern IntPtr SendMessageTimeout(IntPtr h, uint m, IntPtr w, IntPtr l, uint f, uint t, out IntPtr r);'
) -join [Environment]::NewLine
Add-Type -Namespace Win -Name Dlg -MemberDefinition $sig

$found = @()
$cb = [Win.Dlg+EnumWindowsProc]{
    param($hWnd, $lParam)
    if ([Win.Dlg]::IsWindowVisible($hWnd)) {
        $cls = New-Object System.Text.StringBuilder 256
        [void][Win.Dlg]::GetClassName($hWnd, $cls, 256)
        $txt = New-Object System.Text.StringBuilder 512
        [void][Win.Dlg]::GetWindowText($hWnd, $txt, 512)
        # Class and title together. This runs on a live desktop, and closing a
        # dialog the tests put up would be worse than the noise.
        if ($cls.ToString() -eq '#32770' -and $txt.ToString() -match 'System Properties') {
            $script:found += $txt.ToString()
            # WM_CLOSE with a timeout, because a modal dialog runs its own message
            # loop and a plain PostMessage can go unanswered.
            $r = [IntPtr]::Zero
            [void][Win.Dlg]::SendMessageTimeout($hWnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero, 2, 3000, [ref]$r)
        }
    }
    return $true
}
[void][Win.Dlg]::EnumWindows($cb, [IntPtr]::Zero)
if ($found.Count -gt 0) {
    Write-Host "::warning::Dismissed runner dialog(s): $($found -join ', '). Windows only says this when the machine is already short of something, so treat a failure in this job as environmental until shown otherwise."
} else {
    Write-Host 'No runner dialog on the desktop.'
}

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

# The Store applies a downloaded app update the moment the app closes. On the
# arm64 image Notepad is the Store package and ships behind the current version,
# so the first test that closes Notepad is when the package gets swapped, and a
# launch during the swap produces no window at all -- measured 2026-09-05 on
# windows-11-arm as a 30 s discovery timeout right after a test that had shown
# Notepad's "a new version is available" banner. The x64 image carries the
# classic notepad.exe, which the Store never touches. AutoDownload=2 is the
# policy "turn off automatic download and install of updates".
$store = 'HKLM:\SOFTWARE\Policies\Microsoft\WindowsStore'
New-Item -Path $store -Force -ErrorAction SilentlyContinue | Out-Null
Set-ItemProperty -Path $store -Name 'AutoDownload' -Value 2 -Type DWord -Force -ErrorAction SilentlyContinue
Write-Host "WindowsStore AutoDownload: $((Get-ItemProperty -Path $store -Name AutoDownload -ErrorAction SilentlyContinue).AutoDownload)"

# Measured, so a later failure can be attributed: the version now, and whether a
# second version is already staged. A policy set after the download has started
# may or may not stop the swap; this line is how that gets found out.
foreach ($name in 'Microsoft.WindowsNotepad', 'Microsoft.WindowsCalculator') {
    $pkgs = @(Get-AppxPackage -AllUsers -Name $name -ErrorAction SilentlyContinue)
    if ($pkgs.Count -eq 0) { Write-Host "${name}: not a packaged app on this image"; continue }
    $desc = ($pkgs | ForEach-Object { "$($_.Version) [$($_.Status)]" }) -join ', '
    Write-Host "${name}: $desc"
    if ($pkgs.Count -gt 1) {
        Write-Host "::warning::${name} has $($pkgs.Count) package versions present; a Store update is staged and applies when the app closes. A launch during that swap shows no window."
    }
}

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
