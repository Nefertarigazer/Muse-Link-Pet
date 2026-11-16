using System;
using System.Diagnostics;
using System.Text;

// launcher.exe - start a PowerShell script with no console window.
//   launcher.exe <script.ps1> [arg1 arg2 ...]
// runs
//   powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "<script.ps1>" "<arg1>" ...
// Built as a GUI-subsystem program (/target:winexe) and started with
// CreateNoWindow, so scheduled tasks never flash a console ("no black box").
// Build it on the target machine with build-launcher.ps1 (uses the csc.exe
// that ships with .NET Framework 4); the repository carries no binary.
//
// v3 (2026-11-12): arguments are quoted with the CommandLineToArgvW rules
// (backslashes before a quote are doubled), so paths ending in a backslash
// and arguments containing quotes survive intact. Behaviour is otherwise the
// same as v2 (2026-11-03, forwards all arguments).
class Launcher
{
    static string Quote(string arg)
    {
        var sb = new StringBuilder("\"");
        int backslashes = 0;
        foreach (char c in arg)
        {
            if (c == '\\') { backslashes++; continue; }
            if (c == '"') { sb.Append('\\', backslashes * 2 + 1); sb.Append('"'); }
            else { sb.Append('\\', backslashes); sb.Append(c); }
            backslashes = 0;
        }
        sb.Append('\\', backslashes * 2);
        sb.Append('"');
        return sb.ToString();
    }

    static int Main(string[] args)
    {
        if (args.Length == 0) return 2;
        var sb = new StringBuilder("-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ");
        for (int i = 0; i < args.Length; i++)
        {
            if (i > 0) sb.Append(' ');
            sb.Append(Quote(args[i]));
        }
        var psi = new ProcessStartInfo("powershell.exe", sb.ToString());
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        psi.WindowStyle = ProcessWindowStyle.Hidden;
        Process.Start(psi);
        return 0;
    }
}
