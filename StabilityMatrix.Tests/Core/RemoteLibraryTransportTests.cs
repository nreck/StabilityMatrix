using NSubstitute;
using StabilityMatrix.Avalonia.Services;
using StabilityMatrix.Core.Models.Settings;
using StabilityMatrix.Core.Services;

namespace StabilityMatrix.Tests.Core;

[TestClass]
public class RemoteLibraryTransportTests
{
    [TestMethod]
    [TestCategory("RemoteIntegration")]
    public async Task SshInventoryAndWebUiTunnelReachServer()
    {
        var host = Environment.GetEnvironmentVariable("SM_TEST_REMOTE_SSH");
        if (string.IsNullOrEmpty(host))
        {
            Assert.Inconclusive("Set SM_TEST_REMOTE_SSH to test SSH management against a server.");
            return;
        }
        var settings = Substitute.For<ISettingsManager>();
        var modelsPath = Environment.GetEnvironmentVariable("SM_TEST_REMOTE_MODELS") ?? "";
        settings.Settings.Returns(new Settings { RemoteSshHost = host, RemoteModelsPath = modelsPath });
        using var service = new RemoteLibraryService(settings);
        var inventory = await service.RequestAsync<RemoteInventory>(new() { ["action"] = "inventory" });
        Assert.IsTrue(inventory.Packages.Count > 0);
        Assert.IsTrue(inventory.Models.Count > 0);
        Assert.IsTrue(inventory.Roots.Any(r => r.Id == "library"));
        if (!string.IsNullOrEmpty(modelsPath))
            Assert.AreEqual(modelsPath, inventory.Roots.First(r => r.Id == "downloads").Path);
        var address = await service.ForwardPortAsync(8188);
        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(15) };
        var response = await http.GetStringAsync(new Uri(address, "/system_stats"));
        StringAssert.Contains(response, "comfyui_version");
        Assert.AreEqual(address, await service.ForwardPortAsync(8188));
    }
}
