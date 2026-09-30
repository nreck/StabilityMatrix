using NSubstitute;
using Refit;
using StabilityMatrix.Core.Api;
using StabilityMatrix.Core.Inference;

namespace StabilityMatrix.Tests.Core;

[TestClass]
public class ComfyRemoteUploadTests
{
    [TestMethod]
    public async Task InputMaskUsesUploadApiWithRelativeSubfolder()
    {
        var api = Substitute.For<IComfyApi>();
        var factory = Substitute.For<IApiFactory>();
        factory.CreateRefitClient<IComfyApi>(Arg.Any<Uri>(), Arg.Any<RefitSettings>()).Returns(api);
        using var client = new ComfyClient(factory, new Uri("http://127.0.0.1:18188"));
        var path = Path.GetTempFileName();
        try
        {
            await File.WriteAllBytesAsync(path, [1, 2, 3]);
            await client.UploadFileAsync(path, "input/Inference/mask.png");
            await api.Received(1).PostUploadImage(
                Arg.Is<StreamPart>(part => part.FileName == "mask.png"),
                "true", "input", "Inference", Arg.Any<CancellationToken>()
            );
        }
        finally
        {
            File.Delete(path);
        }
    }

    [TestMethod]
    [DataRow("models/configs/model.yaml")]
    [DataRow("input/../models/weights.bin")]
    [DataRow("/input/mask.png")]
    public async Task RejectsUnsupportedRemoteFileDestinations(string destination)
    {
        var api = Substitute.For<IComfyApi>();
        var factory = Substitute.For<IApiFactory>();
        factory.CreateRefitClient<IComfyApi>(Arg.Any<Uri>(), Arg.Any<RefitSettings>()).Returns(api);
        using var client = new ComfyClient(factory, new Uri("http://127.0.0.1:18188"));
        await Assert.ThrowsExceptionAsync<NotSupportedException>(() =>
            client.UploadFileAsync("unused", destination)
        );
        Assert.AreEqual(0, api.ReceivedCalls().Count());
    }
}
