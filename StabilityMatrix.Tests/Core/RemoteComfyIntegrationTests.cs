using Microsoft.Extensions.Logging.Abstractions;
using NSubstitute;
using Refit;
using SkiaSharp;
using StabilityMatrix.Avalonia.Models.TagCompletion;
using StabilityMatrix.Avalonia.Services;
using StabilityMatrix.Core.Api;
using StabilityMatrix.Core.Inference;
using StabilityMatrix.Core.Models.Api.Comfy.Nodes;
using StabilityMatrix.Core.Models.Settings;
using StabilityMatrix.Core.Services;

namespace StabilityMatrix.Tests.Core;

[TestClass]
public class RemoteComfyIntegrationTests
{
    private sealed class ApiFactory : IApiFactory
    {
        public T CreateRefitClient<T>(Uri address) => RestService.For<T>(address.ToString());
        public T CreateRefitClient<T>(Uri address, RefitSettings settings) =>
            RestService.For<T>(address.ToString(), settings);
    }

    [TestMethod]
    [TestCategory("RemoteIntegration")]
    public async Task InferenceManager_LoadsRemoteModelsWithoutInstalledPackages()
    {
        var address = Environment.GetEnvironmentVariable("SM_TEST_COMFY_URL");
        if (string.IsNullOrEmpty(address))
        {
            Assert.Inconclusive("Set SM_TEST_COMFY_URL to run against a real ComfyUI server.");
            return;
        }

        var settings = Substitute.For<ISettingsManager>();
        settings.Settings.Returns(new Settings { UseRemoteInference = true, RemoteInferenceUrl = address });
        var previousContext = SynchronizationContext.Current;
        InferenceClientManager manager;
        try
        {
            SynchronizationContext.SetSynchronizationContext(new SynchronizationContext());
            manager = new InferenceClientManager(
                NullLogger<InferenceClientManager>.Instance,
                new ApiFactory(),
                Substitute.For<IModelIndexService>(),
                settings,
                Substitute.For<ICompletionProvider>()
            );
        }
        finally
        {
            SynchronizationContext.SetSynchronizationContext(previousContext);
        }

        using (manager)
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(60));
            await manager.ConnectAsync(timeout.Token);
            Assert.IsTrue(manager.IsConnected);
            Assert.IsNull(manager.Client!.LocalServerPackage);
            Assert.AreEqual(ComfyEndpoint.Parse(address), manager.Client.BaseAddress);
            Assert.IsTrue(manager.Samplers.Count > 0);
            Assert.IsTrue(manager.VaeModels.Count > 0);
            await manager.CloseAsync();
            Assert.IsFalse(manager.IsConnected);
        }
    }

    // Opt in explicitly: writes one tiny input/output PNG, never loads GPU models or interrupts jobs.
    [TestMethod]
    [TestCategory("RemoteIntegration")]
    public async Task RemoteServer_UploadQueueAndDownload_WithoutLocalPackage()
    {
        var address = Environment.GetEnvironmentVariable("SM_TEST_COMFY_URL");
        if (string.IsNullOrEmpty(address))
        {
            Assert.Inconclusive("Set SM_TEST_COMFY_URL to run against a real ComfyUI server.");
            return;
        }

        using var client = new ComfyClient(new ApiFactory(), ComfyEndpoint.Parse(address));
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(120));
        await client.ConnectAsync(timeout.Token);
        Assert.IsNull(client.LocalServerPath);
        Assert.IsTrue((await client.GetSamplerNamesAsync(timeout.Token))?.Count > 0);
        Assert.IsNotNull(await client.GetModelNamesAsync(timeout.Token));

        using var bitmap = new SKBitmap(16, 16);
        bitmap.Erase(SKColors.CornflowerBlue);
        using var encoded = bitmap.Encode(SKEncodedImageFormat.Png, 100);
        using var input = encoded.AsStream();
        var name = $"remote-smoke-{Guid.NewGuid():N}.png";
        await client.UploadImageAsync(input, name, timeout.Token);
        var prompt = await client.QueuePromptAsync(new Dictionary<string, ComfyNode>
        {
            ["load"] = new()
            {
                ClassType = "LoadImage",
                Inputs = new() { ["image"] = $"Inference/{name}" },
            },
            ["save"] = new()
            {
                ClassType = "SaveImage",
                Inputs = new()
                {
                    ["images"] = new object[] { "load", 0 },
                    ["filename_prefix"] = "StabilityMatrixRemoteTest/smoke",
                },
            },
        }, timeout.Token);
        await prompt.Task.WaitAsync(timeout.Token);
        var outputs = await client.GetImagesForExecutedPromptAsync(prompt.Id, timeout.Token);
        Assert.IsTrue(outputs["save"]?.Count > 0);
        await using var output = await client.GetImageStreamAsync(outputs["save"]![0], timeout.Token);
        using var bytes = new MemoryStream();
        await output.CopyToAsync(bytes, timeout.Token);
        using var result = SKBitmap.Decode(bytes.ToArray());
        Assert.AreEqual(16, result.Width);
        Assert.AreEqual(16, result.Height);
        Assert.AreEqual(SKColors.CornflowerBlue, result.GetPixel(0, 0));
        await client.CloseAsync();
    }
}
