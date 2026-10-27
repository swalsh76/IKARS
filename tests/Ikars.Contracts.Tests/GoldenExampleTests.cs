using System.Text.Json;
using System.Text.Json.Nodes;

namespace Ikars.Contracts.Tests;

/// <summary>
/// The C# records against the engine's golden examples (contracts/examples, exported from contracts.py): each example
/// must read into its record with unknown fields refused and nullability respected, and write back to the same JSON.
/// A field added, renamed or retyped on either side fails here.
/// </summary>
public class GoldenExampleTests
{
    private static readonly Dictionary<string, Type> Records = new()
    {
        ["Hello"] = typeof(Hello),
        ["HealthView"] = typeof(HealthView),
        ["MarketView"] = typeof(MarketView),
        ["MarketPage"] = typeof(MarketPage),
        ["Level"] = typeof(Level),
        ["BookView"] = typeof(BookView),
        ["PositionView"] = typeof(PositionView),
        ["PortfolioView"] = typeof(PortfolioView),
        ["LedgerStatusView"] = typeof(LedgerStatusView),
        ["LedgerRecordView"] = typeof(LedgerRecordView),
        ["LedgerPage"] = typeof(LedgerPage),
        ["AlertView"] = typeof(AlertView),
        ["AlertPage"] = typeof(AlertPage),
        ["RecommendationView"] = typeof(RecommendationView),
        ["RecommendationPage"] = typeof(RecommendationPage),
        ["Subscriptions"] = typeof(Subscriptions),
        ["StreamOpened"] = typeof(StreamOpened),
        ["QuoteUpdate"] = typeof(QuoteUpdate),
        ["ApiError"] = typeof(ApiError),
    };

    public static TheoryData<string> Names() => new(Records.Keys);

    private static DirectoryInfo ContractsFolder()
    {
        for (var folder = new DirectoryInfo(AppContext.BaseDirectory); folder is not null; folder = folder.Parent)
        {
            var candidate = new DirectoryInfo(Path.Combine(folder.FullName, "contracts"));
            if (File.Exists(Path.Combine(candidate.FullName, "ikars-api.schema.json")))
            {
                return candidate;
            }
        }

        throw new InvalidOperationException("the contracts folder was not found above the test's folder");
    }

    [Theory]
    [MemberData(nameof(Names))]
    public void EachExampleReadsAndWritesBackUnchanged(string name)
    {
        var text = File.ReadAllText(Path.Combine(ContractsFolder().FullName, "examples", $"{name}.json"));
        var value = JsonSerializer.Deserialize(text, Records[name], ContractJson.Options);
        Assert.NotNull(value);
        var again = JsonSerializer.SerializeToNode(value, Records[name], ContractJson.Options);
        Assert.True(JsonNode.DeepEquals(JsonNode.Parse(text), again), $"{name} does not round-trip");
    }

    [Fact]
    public void EveryEngineContractHasARecordAndNoMore()
    {
        var folder = ContractsFolder();
        var examples = folder.GetFiles("*.json", new EnumerationOptions { RecurseSubdirectories = true })
            .Where(file => file.Directory?.Name == "examples")
            .Select(file => Path.GetFileNameWithoutExtension(file.Name))
            .Order()
            .ToList();
        Assert.Equal(Records.Keys.Order().ToList(), examples);
        using var schema = JsonDocument.Parse(File.ReadAllText(Path.Combine(folder.FullName, "ikars-api.schema.json")));
        var defined = schema.RootElement.GetProperty("$defs").EnumerateObject().Select(p => p.Name).Order().ToList();
        Assert.Equal(Records.Keys.Order().ToList(), defined);
        Assert.Equal(ContractJson.ApiVersion, schema.RootElement.GetProperty("version").GetString());
    }

    [Fact]
    public void AnUnknownFieldIsRefused()
    {
        const string text = """{"error": "x", "detail": "y", "surprise": 1}""";
        Assert.Throws<JsonException>(() => JsonSerializer.Deserialize<ApiError>(text, ContractJson.Options));
    }

    [Fact]
    public void AMissingRequiredFieldIsRefused()
    {
        const string text = """{"error": "x"}""";
        Assert.Throws<JsonException>(() => JsonSerializer.Deserialize<ApiError>(text, ContractJson.Options));
    }

    [Fact]
    public void ANullWhereNoneIsAllowedIsRefused()
    {
        const string text = """{"error": null, "detail": "y"}""";
        Assert.Throws<JsonException>(() => JsonSerializer.Deserialize<ApiError>(text, ContractJson.Options));
    }
}
