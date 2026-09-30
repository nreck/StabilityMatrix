using StabilityMatrix.Core.Inference;

namespace StabilityMatrix.Tests.Core;

[TestClass]
public class ComfyEndpointTests
{
    [TestMethod]
    [DataRow("http://127.0.0.1:18188", "ws", 18188)]
    [DataRow("https://example.com", "wss", 443)]
    [DataRow("https://example.com:8443/", "wss", 8443)]
    [DataRow(" http://[::1]:8188/ ", "ws", 8188)]
    public void WebSocketPreservesTransportHostAndPort(string address, string scheme, int port)
    {
        var endpoint = ComfyEndpoint.Parse(address);
        var socket = ComfyEndpoint.GetWebSocketUri(endpoint, "test id");
        Assert.AreEqual(scheme, socket.Scheme);
        Assert.AreEqual(endpoint.Host, socket.Host);
        Assert.AreEqual(port, socket.Port);
        Assert.AreEqual("/ws", socket.AbsolutePath);
        Assert.AreEqual("?clientId=test%20id", socket.Query);
    }

    [TestMethod]
    [DataRow(null)]
    [DataRow("")]
    [DataRow("servivor:8188")]
    [DataRow("file:///tmp/comfy")]
    [DataRow("ws://example.com")]
    [DataRow("https://user:password@example.com")]
    [DataRow("https://example.com/comfy")]
    [DataRow("https://example.com/?token=abc")]
    [DataRow("https://example.com/#fragment")]
    public void RejectsUnsupportedServerAddresses(string? address)
    {
        Assert.ThrowsException<ArgumentException>(() => ComfyEndpoint.Parse(address));
    }
}
