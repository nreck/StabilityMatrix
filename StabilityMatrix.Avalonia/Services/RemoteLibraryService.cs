using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Text.Json;
using System.Text.RegularExpressions;
using Injectio.Attributes;
using StabilityMatrix.Core.Services;

namespace StabilityMatrix.Avalonia.Services;

[RegisterSingleton<RemoteLibraryService>]
public sealed class RemoteLibraryService(ISettingsManager settingsManager) : IDisposable
{
    private static readonly JsonSerializerOptions JsonOptions = new() { PropertyNameCaseInsensitive = true };
    private readonly SemaphoreSlim deploymentLock = new(1, 1);
    private string? deployedHost;
    private readonly Dictionary<string, (Process Process, int Port)> forwards = new();

    public async Task<Uri> ForwardPortAsync(int remotePort)
    {
        if (remotePort is < 1024 or > 65535) throw new ArgumentException("Port must be between 1024 and 65535.");
        var host = Host;
        var key = $"{host}:{remotePort}";
        if (forwards.TryGetValue(key, out var existing) && !existing.Process.HasExited)
            return new Uri($"http://127.0.0.1:{existing.Port}");
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        listener.Stop();
        var info = new ProcessStartInfo("ssh") { UseShellExecute = false, CreateNoWindow = true, RedirectStandardError = true };
        foreach (var arg in new[] { "-N", "-T", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
            "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
            "-L", $"127.0.0.1:{port}:127.0.0.1:{remotePort}", host }) info.ArgumentList.Add(arg);
        var process = Process.Start(info) ?? throw new IOException("Could not start SSH tunnel.");
        // Drain stderr continuously; retain only the startup error if the process exits.
        var stderr = process.StandardError.ReadToEndAsync();
        try
        {
            for (var attempt = 0; attempt < 50; attempt++)
            {
                if (process.HasExited) throw new IOException(await stderr);
                using var probe = new TcpClient();
                try
                {
                    await probe.ConnectAsync(IPAddress.Loopback, port);
                    forwards[key] = (process, port);
                    return new Uri($"http://127.0.0.1:{port}");
                }
                catch (SocketException) { await Task.Delay(200); }
            }
            throw new TimeoutException("SSH tunnel did not become ready.");
        }
        catch
        {
            if (!process.HasExited) process.Kill(true);
            process.Dispose();
            throw;
        }
    }

    public void Dispose()
    {
        foreach (var (process, _) in forwards.Values)
        {
            if (!process.HasExited) process.Kill(true);
            process.Dispose();
        }
        deploymentLock.Dispose();
    }

    private string Host
    {
        get
        {
            var host = settingsManager.Settings.RemoteSshHost.Trim();
            if (!Regex.IsMatch(host, "^[a-zA-Z0-9][a-zA-Z0-9_.@:-]*$"))
                throw new ArgumentException("Enter an SSH host alias such as servivor in Settings → Inference.");
            return host;
        }
    }

    private async Task<string> RunSshAsync(string host, string command, string input, Stream? file, CancellationToken token)
    {
        var info = new ProcessStartInfo("ssh")
        {
            RedirectStandardInput = true, RedirectStandardOutput = true,
            RedirectStandardError = true, UseShellExecute = false, CreateNoWindow = true,
        };
        foreach (var arg in new[] { "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                     "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3", host, command })
            info.ArgumentList.Add(arg);
        using var process = Process.Start(info) ?? throw new IOException("Could not start SSH.");
        var output = process.StandardOutput.ReadToEndAsync(token);
        var error = process.StandardError.ReadToEndAsync(token);
        try
        {
            await process.StandardInput.WriteLineAsync(input.AsMemory(), token);
            await process.StandardInput.FlushAsync(token);
            if (file is not null)
                await file.CopyToAsync(process.StandardInput.BaseStream, token);
            process.StandardInput.Close();
            await process.WaitForExitAsync(token);
            var result = await output;
            var errorText = await error;
            if (process.ExitCode != 0 && string.IsNullOrWhiteSpace(result))
                throw new IOException($"SSH to {host} failed: {errorText.Trim()}");
            return result;
        }
        finally
        {
            if (!process.HasExited)
                process.Kill(entireProcessTree: true);
        }
    }

    private async Task EnsureAgentAsync(string host, CancellationToken token)
    {
        await deploymentLock.WaitAsync(token);
        try
        {
            if (deployedHost == host)
                return;
            using var stream = typeof(RemoteLibraryService).Assembly.GetManifestResourceStream(
                "StabilityMatrix.Avalonia.Assets.remote_library_agent.py")
                ?? throw new IOException("Remote library agent is missing from this build.");
            using var reader = new StreamReader(stream);
            var source = await reader.ReadToEndAsync(token);
            // Fixed command; all source and user data travel on stdin, never through shell interpolation.
            const string deploy = "python3 -c 'import pathlib,sys,os; p=pathlib.Path.home()/\".local/share/stabilitymatrix-remote\"; p.mkdir(parents=True,exist_ok=True,mode=448); t=p/\"agent.py.new\"; t.write_text(sys.stdin.read()); os.chmod(t,384); os.replace(t,p/\"agent.py\"); print(\"installed\")'";
            var result = await RunSshAsync(host, deploy, source, null, token);
            if (!result.Contains("installed"))
                throw new IOException("Could not install the remote management helper.");
            deployedHost = host;
        }
        finally { deploymentLock.Release(); }
    }

    public async Task<T> RequestAsync<T>(Dictionary<string, object?> request, CancellationToken token = default, Stream? file = null)
    {
        var host = Host;
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token);
        timeout.CancelAfter(file is null ? TimeSpan.FromSeconds(60) : TimeSpan.FromHours(6));
        await EnsureAgentAsync(host, timeout.Token);
        request["library"] = settingsManager.Settings.RemoteLibraryPath;
        request["comfy"] = settingsManager.Settings.RemoteComfyPath;
        request["models"] = settingsManager.Settings.RemoteModelsPath;
        var json = await RunSshAsync(host, "python3 ~/.local/share/stabilitymatrix-remote/agent.py",
            JsonSerializer.Serialize(request), file, timeout.Token);
        using var response = JsonDocument.Parse(json);
        if (!response.RootElement.GetProperty("ok").GetBoolean())
            throw new IOException(response.RootElement.GetProperty("error").GetString());
        return response.RootElement.GetProperty("data").Deserialize<T>(JsonOptions)
            ?? throw new IOException("Empty response from remote library.");
    }
}

public sealed record RemotePackage(string Id, string Name, string Kind, string Path, string Status, bool Running, bool Managed, int Port);
public sealed record RemoteModel(string Root, string Path, string Name, string Category, long Size)
{
    public string SizeDisplay => $"{Size / 1073741824d:N2} GB";
    public string Location => Root == "downloads" ? "Download drive" : Root == "library" ? "Stability Matrix" : Root == "comfy" ? "Inference ComfyUI" : "Shared ComfyUI";
}
public sealed record RemoteChoice(string Id, string Name, string? Path = null);
public sealed record RemoteJob(string Id, string Description, string Status, string Message);
public sealed record RemoteResult(string Message, string? JobId = null);
public sealed record RemoteInventory(string Host, string Library, List<RemotePackage> Packages,
    List<RemoteModel> Models, List<RemoteJob> Jobs, List<RemoteChoice> Catalog, List<RemoteChoice> Roots, long FreeBytes);
