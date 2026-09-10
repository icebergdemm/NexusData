using System.Diagnostics;
using System.Text;
using Xunit;

namespace VoiceProfileDecoder.Tests;

public sealed class DecoderTests
{
    private const string OpusFixture = "aIVOha4df6KAarMF1UB0VIXQu2V1/dVBnd0x4YjEUgbm6/pGUz5E3w==";

    [Fact]
    public async Task DecodesFixtureToMono48KhzPcmWave()
    {
        using var workspace = new TestWorkspace();
        var input = workspace.Path("speaker.vpk1");
        var output = workspace.Path("speaker.wav");
        var index = workspace.Path("speaker.json");
        WriteVpk1(input, new[] { (11790u, false, Convert.FromBase64String(OpusFixture)) });

        var result = await RunDecoder(input, output, index);

        Assert.Equal(0, result.ExitCode);
        var wave = await File.ReadAllBytesAsync(output);
        Assert.Equal("RIFF", Encoding.ASCII.GetString(wave, 0, 4));
        Assert.Equal((short)1, BitConverter.ToInt16(wave, 22));
        Assert.Equal(48000, BitConverter.ToInt32(wave, 24));
        Assert.Equal((short)16, BitConverter.ToInt16(wave, 34));
        Assert.True(BitConverter.ToInt32(wave, 40) > 0);
        Assert.Contains("11790", await File.ReadAllTextAsync(index));
    }

    [Fact]
    public async Task EmptyVpk1ProducesEmptyWaveAndTruncatedInputFails()
    {
        using var workspace = new TestWorkspace();
        var empty = workspace.Path("empty.vpk1");
        var emptyWave = workspace.Path("empty.wav");
        WriteVpk1(empty, Array.Empty<(uint, bool, byte[])>());

        var emptyResult = await RunDecoder(empty, emptyWave, null);

        Assert.Equal(0, emptyResult.ExitCode);
        var wave = await File.ReadAllBytesAsync(emptyWave);
        Assert.Equal(0, BitConverter.ToInt32(wave, 40));

        var truncated = workspace.Path("truncated.vpk1");
        await File.WriteAllBytesAsync(truncated, Encoding.ASCII.GetBytes("VPK1\x01"));
        var truncatedResult = await RunDecoder(truncated, workspace.Path("truncated.wav"), null);

        Assert.NotEqual(0, truncatedResult.ExitCode);
    }

    private static async Task<ProcessResult> RunDecoder(string input, string output, string? index)
    {
        var project = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "../../../../../tools/VoiceProfileDecoder/VoiceProfileDecoder.csproj"));
        var start = new ProcessStartInfo("dotnet")
        {
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        start.ArgumentList.Add("run");
        start.ArgumentList.Add("--project");
        start.ArgumentList.Add(project);
        start.ArgumentList.Add("-c");
        start.ArgumentList.Add("Debug");
        start.ArgumentList.Add("--no-restore");
        start.ArgumentList.Add("--");
        start.ArgumentList.Add("--input");
        start.ArgumentList.Add(input);
        start.ArgumentList.Add("--output");
        start.ArgumentList.Add(output);
        if (index is not null)
        {
            start.ArgumentList.Add("--index");
            start.ArgumentList.Add(index);
        }

        using var process = Process.Start(start) ?? throw new InvalidOperationException("Could not start decoder");
        await process.WaitForExitAsync();
        return new ProcessResult(process.ExitCode, await process.StandardOutput.ReadToEndAsync(), await process.StandardError.ReadToEndAsync());
    }

    private static void WriteVpk1(string path, IReadOnlyList<(uint Timestamp, bool Radio, byte[] Payload)> packets)
    {
        using var stream = File.Create(path);
        using var writer = new BinaryWriter(stream, Encoding.UTF8, leaveOpen: false);
        writer.Write(Encoding.ASCII.GetBytes("VPK1"));
        writer.Write((ushort)1);
        writer.Write((uint)packets.Count);
        foreach (var packet in packets)
        {
            writer.Write(packet.Timestamp);
            writer.Write((byte)(packet.Radio ? 1 : 0));
            writer.Write((ushort)packet.Payload.Length);
            writer.Write(packet.Payload);
        }
    }

    private readonly record struct ProcessResult(int ExitCode, string Output, string Error);

    private sealed class TestWorkspace : IDisposable
    {
        private readonly string _path = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "voice-profile-tests", Guid.NewGuid().ToString("N"));

        public TestWorkspace() => Directory.CreateDirectory(_path);
        public string Path(string filename) => System.IO.Path.Combine(_path, filename);
        public void Dispose() => Directory.Delete(_path, recursive: true);
    }
}
