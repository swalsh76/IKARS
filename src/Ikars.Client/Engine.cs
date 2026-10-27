using System.Text.Json;
using Ikars.Contracts;

namespace Ikars.Client;

/// <summary>Where the running engine listens and the session's token, from its <c>api.json</c> (PLAN M1.7).</summary>
/// <param name="Port">The loopback port.</param>
/// <param name="Token">The session token: sent on every request, never shown or logged.</param>
/// <param name="ApiVersion">The engine's API version.</param>
public sealed record ApiInfo(int Port, string Token, string ApiVersion)
{
    /// <summary>The engine's data folder: <c>%LOCALAPPDATA%\IKARS\data</c>.</summary>
    public static string DefaultDataFolder { get; } =
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "IKARS", "data");

    /// <summary>The engine's <c>api.json</c>, or <see langword="null"/> when no engine is serving (or the file is unreadable).</summary>
    /// <param name="dataFolder">The engine's data folder.</param>
    /// <returns>The record, or <see langword="null"/>.</returns>
    public static ApiInfo? Read(string dataFolder)
    {
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(Path.Combine(dataFolder, "api.json")));
            var root = document.RootElement;
            return new ApiInfo(
                root.GetProperty("port").GetInt32(),
                root.GetProperty("token").GetString() ?? string.Empty,
                root.GetProperty("api_version").GetString() ?? string.Empty);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or KeyNotFoundException or InvalidOperationException)
        {
            return null;
        }
    }
}

/// <summary>The API refused a request; <see cref="Code"/> is its own error code (unauthorised, not_found, bad_request, unavailable).</summary>
public sealed class ApiException(int status, string code, string detail) : Exception($"{code}: {detail}")
{
    /// <summary>The HTTP status.</summary>
    public int Status { get; } = status;

    /// <summary>The API's error code.</summary>
    public string Code { get; } = code;

    /// <summary>What the API said.</summary>
    public string Detail { get; } = detail;
}

/// <summary>The engine cannot be reached (not running, or not answering).</summary>
public sealed class EngineUnavailableException(string message, Exception? inner = null) : Exception(message, inner);

/// <summary>The engine speaks a different major version of the API: the terminal must not guess.</summary>
public sealed class IncompatibleEngineException(string engineVersion)
    : Exception($"the engine speaks API {engineVersion}; this terminal speaks {ContractJson.ApiVersion}. Update the one that is older.")
{
    /// <summary>The engine's API version.</summary>
    public string EngineVersion { get; } = engineVersion;
}

/// <summary>The version handshake (PLAN D20 G): the same major version, or no connection.</summary>
public static class Handshake
{
    /// <summary>Whether the engine's API version is one this terminal speaks.</summary>
    /// <param name="engineVersion">The engine's version, as <c>major.minor</c>.</param>
    /// <returns><see langword="true"/> when the majors match.</returns>
    public static bool Compatible(string engineVersion) => Major(engineVersion) is { } major && major == Major(ContractJson.ApiVersion);

    private static int? Major(string version) => int.TryParse(version.Split('.')[0], out var major) ? major : null;
}
