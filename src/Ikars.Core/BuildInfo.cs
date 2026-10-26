using System.Reflection;

namespace Ikars.Core;

/// <summary>Build metadata of the Ikars.Core assembly.</summary>
public static class BuildInfo
{
    /// <summary>The informational version of this assembly, or "0.0.0" if none is stamped.</summary>
    public static string Version { get; } =
        typeof(BuildInfo).Assembly
            .GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion
        ?? "0.0.0";
}
