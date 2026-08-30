# A WPF window holding a real DataGrid, as a test fixture.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tests/wpf_grid_app.ps1
#
# Why this and not the Win32 SysListView32 fixture: UIA reports a report-mode
# ListView as a *List* with only Selection and Scroll — no GridPattern, and its
# rows are list items rather than data items. GridPattern comes from WPF and
# WinUI. That was established by the provider diagnostic rather than assumed.
#
# PowerShell can build a WPF UI at runtime from the assemblies that ship with
# Windows, so this keeps the project's no-compiler rule while providing the real
# provider.
#
# The row count is deliberately far larger than the viewport: a WPF DataGrid
# virtualizes by default, so most rows have no UIA peer until something realizes
# them. That is what makes VirtualizedItemPattern and ItemContainerPattern
# testable here rather than merely implemented.

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase

$Title = 'wintegrate grid fixture'
$RowCount = 200

$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="$Title" Height="400" Width="640"
        WindowStartupLocation="Manual" Left="120" Top="120">
  <Grid>
    <DataGrid x:Name="Grid"
              AutomationProperties.AutomationId="wintegrate-grid"
              AutoGenerateColumns="False"
              IsReadOnly="True"
              SelectionUnit="Cell"
              SelectionMode="Single"
              EnableRowVirtualization="True"
              EnableColumnVirtualization="False">
      <DataGrid.Columns>
        <DataGridTextColumn Header="Name"   Binding="{Binding Name}"   Width="200"/>
        <DataGridTextColumn Header="Kind"   Binding="{Binding Kind}"   Width="160"/>
        <DataGridTextColumn Header="Status" Binding="{Binding Status}" Width="160"/>
      </DataGrid.Columns>
    </DataGrid>
  </Grid>
</Window>
"@

$reader = New-Object System.Xml.XmlNodeReader ([xml]$xaml)
$window = [Windows.Markup.XamlReader]::Load($reader)
$grid = $window.FindName('Grid')

# Deterministic content: the tests compute the expected value for any row from
# the same formula, so nothing depends on a hard-coded table staying in sync.
$kinds = @('widget', 'gadget', 'gizmo')
$states = @('ready', 'failed', 'pending')
$items = New-Object System.Collections.ObjectModel.ObservableCollection[object]
for ($i = 0; $i -lt $RowCount; $i++) {
    $items.Add([pscustomobject]@{
        Name   = "row-$i"
        Kind   = $kinds[$i % $kinds.Count]
        Status = $states[$i % $states.Count]
    })
}
$grid.ItemsSource = $items

$window.Add_ContentRendered({ Write-Host $Title })
$window.ShowDialog() | Out-Null
