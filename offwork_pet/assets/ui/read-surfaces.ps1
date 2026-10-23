# Read-only UI Automation geometry worker. Never requests Name, Value or text patterns.
param([long]$TargetWindow)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class GeometryDpi {
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
}
'@
try { [void][GeometryDpi]::SetProcessDpiAwarenessContext([IntPtr](-4)) } catch {}
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
& {
    $rows = [System.Collections.Generic.List[object]]::new()
    try {
        $windowHandle = [IntPtr]$TargetWindow
        $element = [System.Windows.Automation.AutomationElement]::FromHandle($windowHandle)
        $queue = [System.Collections.Generic.Queue[object]]::new()
        $queue.Enqueue(@($element, 0))
        $count = 0
        $watch = [Diagnostics.Stopwatch]::StartNew()
        while ($queue.Count -gt 0 -and $count -lt 160 -and $watch.ElapsedMilliseconds -lt 500) {
            $item = $queue.Dequeue()
            if ($item[1] -ge 5) { continue }
            $child = $walker.GetFirstChild($item[0])
            while ($null -ne $child -and $count -lt 160 -and $watch.ElapsedMilliseconds -lt 500) {
                $count++
                $current = $child.Current
                if (-not $current.IsOffscreen) {
                    $rect = $current.BoundingRectangle
                    $kind = $current.ControlType.Id
                    # Pane, Group, ToolBar, Tab and Document; inputs and pop-up menus are excluded.
                    if ($kind -in @(50033, 50026, 50021, 50018, 50030) -and $rect.Width -ge 160 -and $rect.Height -ge 24 -and $rows.Count -lt 24) {
                        $rows.Add(@{id=($child.GetRuntimeId() -join '.'); left=$rect.Left; top=$rect.Top; right=$rect.Right; bottom=$rect.Bottom})
                    }
                    if ($rect.Width -ge 160 -and $rect.Height -ge 24) { $queue.Enqueue(@($child, ($item[1]+1))) }
                }
                $child = $walker.GetNextSibling($child)
            }
        }
    } catch { $rows.Clear() }
    [Console]::WriteLine((ConvertTo-Json -InputObject @($rows.ToArray()) -Compress -Depth 4))
}
