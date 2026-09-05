# Prepares a hosted Windows runner for GUI automation, and reports what it
# found. Used by the setup action that consumers run, and by this repo's own
# CI -- which does not use that action, because it has to test the working
# tree rather than an installed wintegrate.
#
# Everything here is either suppression or measurement. Nothing repairs the
# machine: see the notes below for why that would be a no-op dressed as a fix.

# ErrorMode=2 suppresses hard-error message boxes for the whole system.
# A runner has nobody to click them, so one sits modal on the desktop and
# takes the foreground -- measured on windows-11-arm, which twice raised
# "Windows created a temporary paging file on your computer because of a
# problem that occurred with your paging file configuration" and then
# produced no window at all for notepad.exe.
#
# The whole class, rather than that one title: any hard error is a modal
# nobody will answer.
$key = 'HKLM:\SYSTEM\CurrentControlSet\Control\Windows'
$was = (Get-ItemProperty -Path $key -Name ErrorMode -ErrorAction SilentlyContinue).ErrorMode
Set-ItemProperty -Path $key -Name ErrorMode -Value 2
Write-Host "ErrorMode: $was -> $((Get-ItemProperty -Path $key -Name ErrorMode).ErrorMode)"

# Measured, not fixed. The paging-file warning means the machine is
# already short of something, and none of it can be repaired from inside
# the job: automatic pagefile management needs a reboot to take effect,
# and if the disk is what ran out then managing it differently does not
# make room. What this can do is make the next failure attributable
# instead of arriving as "no window appeared".
Get-PSDrive C | Select-Object @{n='Free(GB)';e={[math]::Round($_.Free/1GB,1)}},
                              @{n='Used(GB)';e={[math]::Round($_.Used/1GB,1)}} |
  Format-Table -AutoSize | Out-String | Write-Host
$cs = Get-CimInstance Win32_ComputerSystem
Write-Host "AutomaticManagedPagefile: $($cs.AutomaticManagedPagefile)"
Get-CimInstance Win32_PageFileUsage |
  Select-Object Name, CurrentUsage, AllocatedBaseSize, PeakUsage |
  Format-Table -AutoSize | Out-String | Write-Host

# One may already be up: ErrorMode only governs the next hard error, and
# this one is raised at logon, before any step runs.
#
# By window handle, not Get-Process | Where MainWindowTitle. A modal
# #32770 is not necessarily its process's main window -- if it belongs to
# explorer the title never matches, and if it did, Stop-Process would
# take the desktop with it.
# A here-string cannot be used here: PowerShell wants its terminator in
# column 0, and that ends the YAML block before the step does.
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
    # Class and title together. This runs on a live desktop, and closing
    # a dialog the tests put up would be worse than the noise.
    if ($cls.ToString() -eq '#32770' -and $txt.ToString() -match 'System Properties') {
      $script:found += $txt.ToString()
      # WM_CLOSE with a timeout, because a modal dialog runs its own
      # message loop and a plain PostMessage can go unanswered.
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
  Write-Host "No runner dialog on the desktop."
}
}
