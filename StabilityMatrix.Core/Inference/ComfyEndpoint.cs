namespace StabilityMatrix.Core.Inference;

/// <summary>Validates ComfyUI server addresses shared by HTTP and WebSocket clients.</summary>
public static class ComfyEndpoint
{
    public static Uri Parse(string? address)
    {
        if (
            !Uri.TryCreate(address?.Trim(), UriKind.Absolute, out var uri)
            || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps)
            || string.IsNullOrEmpty(uri.Host)
            || !string.IsNullOrEmpty(uri.UserInfo)
            || uri.AbsolutePath != "/"
            || !string.IsNullOrEmpty(uri.Query)
            || !string.IsNullOrEmpty(uri.Fragment)
        )
        {
            throw new ArgumentException(
                "Enter a ComfyUI server URL such as http://127.0.0.1:18188 (no path, credentials, query or fragment).",
                nameof(address)
            );
        }

        return uri;
    }

    public static Uri GetWebSocketUri(Uri endpoint, string clientId) =>
        new UriBuilder(endpoint)
        {
            Scheme = endpoint.Scheme == Uri.UriSchemeHttps ? "wss" : "ws",
            Path = "/ws",
            Query = $"clientId={Uri.EscapeDataString(clientId)}",
        }.Uri;
}
