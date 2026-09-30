using Avalonia.Controls.Notifications;
using Injectio.Attributes;
using StabilityMatrix.Core.Services;

namespace StabilityMatrix.Avalonia.Services;

public interface IRemoteModelDownloadService
{
    bool IsEnabled { get; }
    Task QueueAsync(IEnumerable<Uri> urls, string relativePath, string? sha256 = null);
    string RelativePath(string directory, string filename);
}

[RegisterSingleton<IRemoteModelDownloadService, RemoteModelDownloadService>]
public sealed class RemoteModelDownloadService(
    ISettingsManager settings,
    ISecretsManager secretsManager,
    RemoteLibraryService library,
    INotificationService notifications
) : IRemoteModelDownloadService
{
    public bool IsEnabled => settings.Settings.UseRemoteInference;

    public string RelativePath(string directory, string filename)
    {
        var relative = Path.GetRelativePath(settings.ModelsDirectory, directory);
        if (Path.IsPathRooted(relative) || relative == ".." || relative.StartsWith("../") || relative.StartsWith("..\\"))
            throw new ArgumentException("Choose a model category inside Models for remote downloads. Set the server drive in Settings → Inference.");
        return Path.Combine(relative == "." ? "" : relative, filename).Replace('\\', '/');
    }

    public async Task QueueAsync(IEnumerable<Uri> urls, string relativePath, string? sha256 = null)
    {
        var sources = urls.ToArray();
        if (sources.Length == 0) throw new ArgumentException("Model has no download links.");
        var secrets = await secretsManager.SafeLoadAsync();
        var downloads = sources.Select(url => new Dictionary<string, object?>
        {
            ["url"] = url.AbsoluteUri,
            ["token"] = url.Host.Equals("civitai.com", StringComparison.OrdinalIgnoreCase) ? secrets.CivitApi?.ApiToken
                : url.Host.Equals("huggingface.co", StringComparison.OrdinalIgnoreCase) ? secrets.HuggingFaceToken : null,
        }).ToArray();
        await library.RequestAsync<RemoteResult>(new()
        {
            ["action"] = "job", ["operation"] = "download", ["sources"] = downloads,
            ["path"] = relativePath, ["sha256"] = sha256,
        });
        var destination = string.IsNullOrWhiteSpace(settings.Settings.RemoteModelsPath)
            ? settings.Settings.RemoteLibraryPath.TrimEnd('/') + "/Models" : settings.Settings.RemoteModelsPath.TrimEnd('/');
        notifications.Show("Download queued on " + settings.Settings.RemoteSshHost,
            $"{destination}/{relativePath}\nTrack progress in Checkpoint Manager → Server jobs and logs.", NotificationType.Information);
    }
}
