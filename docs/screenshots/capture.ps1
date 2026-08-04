param(
    [Parameter(Mandatory=$true)][string]$OutPath,
    [int]$SettleMs = 1200
)
Add-Type -AssemblyName System.Drawing
$cs = @'
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;

public class WinCap {
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool ShowWindowAsync(IntPtr h, int c);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
    struct RECT { public int Left, Top, Right, Bottom; }

    public static string Capture(IntPtr h, string path) {
        ShowWindowAsync(h, 3);            // SW_MAXIMIZE
        SetForegroundWindow(h);
        System.Threading.Thread.Sleep(600);
        RECT r;
        GetWindowRect(h, out r);
        int w = r.Right - r.Left, hh = r.Bottom - r.Top;
        if (w <= 0 || hh <= 0) return "ERROR: empty rect";
        using (var bmp = new Bitmap(w, hh))
        using (var g = Graphics.FromImage(bmp)) {
            g.CopyFromScreen(r.Left, r.Top, 0, 0, bmp.Size);
            bmp.Save(path, ImageFormat.Png);
        }
        return string.Format("OK {0}x{1}", w, hh);
    }
}
'@
Add-Type -TypeDefinition $cs -ReferencedAssemblies System.Drawing
$proc = Get-Process LuckyDBrowser -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $proc) { Write-Output 'ERROR: browser window not found'; exit 1 }
Start-Sleep -Milliseconds $SettleMs
$result = [WinCap]::Capture($proc.MainWindowHandle, $OutPath)
Write-Output "$result -> $OutPath ($((Get-Item $OutPath -ErrorAction SilentlyContinue).Length) bytes)"
