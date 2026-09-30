using NSubstitute;
using StabilityMatrix.Avalonia.Services;
using StabilityMatrix.Core.Models.Api;
using StabilityMatrix.Core.Models.FileInterfaces;
using StabilityMatrix.Core.Models.Settings;
using StabilityMatrix.Core.Services;

namespace StabilityMatrix.Tests.Avalonia;

[TestClass]
public class RemoteModelDownloadTests
{
    [TestMethod]
    public async Task CivitDownloadQueuesServerJobWithoutCreatingLocalDirectoryOrDownload()
    {
        var remote = Substitute.For<IRemoteModelDownloadService>();
        remote.IsEnabled.Returns(true);
        remote.RelativePath(Arg.Any<string>(), "example.safetensors").Returns("StableDiffusion/example.safetensors");
        var downloads = Substitute.For<IDownloadService>();
        var tracked = Substitute.For<ITrackedDownloadService>();
        var service = new ModelImportService(downloads, Substitute.For<INotificationService>(), tracked, remote);
        var directory = Path.Combine(Path.GetTempPath(), "remote-model-test-" + Guid.NewGuid());
        var file = new CivitFile { Name = "example.safetensors", DownloadUrl = "https://civitai.com/api/download/models/123",
            Id = 456, Hashes = new CivitFileHashes { SHA256 = "expected-hash" } };
        await service.DoImport(new CivitModel(), new DirectoryPath(directory), new CivitModelVersion(), file);
        await remote.Received(1).QueueAsync(Arg.Is<IEnumerable<Uri>>(urls => urls.Single().Query.Contains("fileId=456")),
            "StableDiffusion/example.safetensors", "expected-hash");
        Assert.IsFalse(Directory.Exists(directory));
        Assert.AreEqual(0, tracked.ReceivedCalls().Count());
        Assert.AreEqual(0, downloads.ReceivedCalls().Count());
    }

    [TestMethod]
    public async Task CustomImportQueuesAllMirrorsWithoutLocalFiles()
    {
        var remote = Substitute.For<IRemoteModelDownloadService>();
        remote.IsEnabled.Returns(true);
        remote.RelativePath(Arg.Any<string>(), "example.gguf").Returns("DiffusionModels/example.gguf");
        var tracked = Substitute.For<ITrackedDownloadService>();
        var service = new ModelImportService(Substitute.For<IDownloadService>(), Substitute.For<INotificationService>(), tracked, remote);
        var directory = Path.Combine(Path.GetTempPath(), "remote-model-test-" + Guid.NewGuid());
        Uri[] urls = [new("https://example.com/a"), new("https://example.com/b")];
        await service.DoCustomImport(urls, "example.gguf", new DirectoryPath(directory));
        await remote.Received(1).QueueAsync(Arg.Is<IEnumerable<Uri>>(sources => sources.SequenceEqual(urls)),
            "DiffusionModels/example.gguf");
        Assert.IsFalse(Directory.Exists(directory));
        Assert.AreEqual(0, tracked.ReceivedCalls().Count());
    }

    [TestMethod]
    public void RemotePathsKeepModelCategoriesAndRejectMacFoldersOutsideLibrary()
    {
        var settings = Substitute.For<ISettingsManager>();
        settings.Settings.Returns(new Settings { UseRemoteInference = true });
        settings.ModelsDirectory.Returns("/tmp/library/Models");
        using var library = new RemoteLibraryService(settings);
        var service = new RemoteModelDownloadService(settings, Substitute.For<ISecretsManager>(), library,
            Substitute.For<INotificationService>());
        Assert.AreEqual("Lora/SDXL/model.safetensors", service.RelativePath("/tmp/library/Models/Lora/SDXL", "model.safetensors"));
        Assert.ThrowsException<ArgumentException>(() => service.RelativePath("/tmp/elsewhere", "model.safetensors"));
    }
}
