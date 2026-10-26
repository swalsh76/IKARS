namespace Ikars.Core.Tests;

public class BuildInfoTests
{
    [Fact]
    public void VersionIsNotEmpty() => Assert.False(string.IsNullOrWhiteSpace(BuildInfo.Version));
}
