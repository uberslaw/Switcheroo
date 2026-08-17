using System.IO;
using System.Windows;
using LaunchControl.Standard.Host;

namespace Switcheroo.LaunchControl;

public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        var root = FindRoot();
        Directory.CreateDirectory(Path.Combine(root, "data"));
        var python = ProcessUtil.FindPython(root) ?? "python";
        var data = Path.Combine(root, "data");
        var pidFile = Path.Combine(data, "switcheroo.pid");
        var diagFlag = Path.Combine(data, "diagnostics.enabled");
        var log = Path.Combine(data, "switcheroo.log");
        var (host, port) = ReadBind(root);

        LaunchControlApp.Run(this, new LaunchControlProfile
        {
            ProductId = "switcheroo",
            ProductName = "Switcheroo",
            AppDataFolder = "Switcheroo",
            ServiceNames = ["Switcheroo"],
            InstallRoot = root,
            HealthUrl = $"http://{Normalize(host)}:{port}/health",
            BrowserUrl = $"http://{Normalize(host)}:{port}/",
            LogPaths = [log, Path.Combine(data, "diagnostics.log")],
            CrashLogPath = Path.Combine(root, "logs", "launch-control.log"),
            DiagnosticsFlagPath = diagFlag,
            DefaultColors = new Dictionary<string, string>
            {
                ["ChromeColor"] = "#1B365D",
                ["PrimaryActionColor"] = "#2A9D8F"
            },
            MetaText = () => $"Bind http://{Normalize(host)}:{port}   Python {python}",
            ProcessFallback = new ProcessFallbackSpec
            {
                WorkingDirectory = () => root,
                StartInfo = () => (python, "-m app"),
                PidFile = pidFile
            },
            ExtraActions =
            [
                new("Install Windows service", w =>
                {
                    var ps1 = Path.Combine(root, "scripts", "install-service.ps1");
                    w.BeginElevatedPowerShell(ps1, root, Path.Combine(data, "install-service.log"));
                }, "Windows service"),
                new("Uninstall Windows service", w =>
                {
                    var ps1 = Path.Combine(root, "scripts", "uninstall-service.ps1");
                    w.BeginElevatedPowerShell(ps1, root, Path.Combine(data, "uninstall-service.log"));
                }, "Windows service"),
                new("Open data folder", _ => ProcessUtil.OpenPath(data), "Logs"),
                new("Open switcheroo.log", _ => ProcessUtil.OpenPath(log), "Logs"),
                new("Open audit.log", _ => ProcessUtil.OpenPath(Path.Combine(data, "audit.log")), "Logs"),
                new("Open diagnostics.log", _ => ProcessUtil.OpenPath(Path.Combine(data, "diagnostics.log")), "Logs"),
                new("Diagnostics ON", w =>
                {
                    Directory.CreateDirectory(data);
                    File.WriteAllText(diagFlag, "on");
                    w.AppendLog("Diagnostics ON (data\\diagnostics.enabled).");
                }, "Diagnostics"),
                new("Diagnostics OFF", w =>
                {
                    if (File.Exists(diagFlag)) File.Delete(diagFlag);
                    w.AppendLog("Diagnostics OFF");
                }, "Diagnostics"),
            ]
        });
    }

    private static string FindRoot()
    {
        var dir = AppContext.BaseDirectory;
        for (var i = 0; i < 8 && !string.IsNullOrEmpty(dir); i++)
        {
            if (File.Exists(Path.Combine(dir, "requirements.txt")) || Directory.Exists(Path.Combine(dir, "app")))
                return dir;
            dir = Directory.GetParent(dir)?.FullName ?? "";
        }
        return @"C:\Switcheroo";
    }

    private static (string Host, int Port) ReadBind(string root)
    {
        var host = "127.0.0.1";
        var port = 8080;
        var envPath = Path.Combine(root, ".env");
        if (!File.Exists(envPath)) return (host, port);
        foreach (var raw in File.ReadAllLines(envPath))
        {
            var line = raw.Trim();
            if (line.Length == 0 || line.StartsWith('#') || !line.Contains('=')) continue;
            var idx = line.IndexOf('=');
            var key = line[..idx].Trim();
            var val = line[(idx + 1)..].Trim().Trim('"').Trim('\'');
            if (key.Equals("SWITCHEROO_HOST", StringComparison.OrdinalIgnoreCase) && val.Length > 0)
                host = val;
            if (key.Equals("SWITCHEROO_PORT", StringComparison.OrdinalIgnoreCase) && int.TryParse(val, out var p) && p > 0)
                port = p;
        }
        return (host, port);
    }

    private static string Normalize(string host) =>
        host is "0.0.0.0" or "::" or "[::]" ? "127.0.0.1" : host;
}
