using System.Buffers.Binary;
using System.Text;
using System.Text.Json;
using Concentus.Structs;

const int SampleRate = 48_000;
const short Channels = 1;
const short BitsPerSample = 16;
const int MaxDecodedSamplesPerPacket = 5_760;

try
{
    var options = ParseArguments(args);
    DecodeVpk1ToWave(options.Input, options.Output, options.Index);
    return 0;
}
catch (Exception exception)
{
    Console.Error.WriteLine(exception.Message);
    return 1;
}

static DecoderOptions ParseArguments(string[] args)
{
    string? input = null;
    string? output = null;
    string? indexPath = null;
    for (var index = 0; index < args.Length; index++)
    {
        switch (args[index])
        {
            case "--input" when index + 1 < args.Length:
                input = args[++index];
                break;
            case "--output" when index + 1 < args.Length:
                output = args[++index];
                break;
            case "--index" when index + 1 < args.Length:
                indexPath = args[++index];
                break;
            default:
                throw new ArgumentException("Usage: VoiceProfileDecoder --input <file.vpk1> --output <file.wav>");
        }
    }

    if (string.IsNullOrWhiteSpace(input) || string.IsNullOrWhiteSpace(output))
    {
        throw new ArgumentException("Usage: VoiceProfileDecoder --input <file.vpk1> --output <file.wav>");
    }

    return new DecoderOptions(input, output, indexPath);
}

static void DecodeVpk1ToWave(string inputPath, string outputPath, string? indexPath)
{
    var outputDirectory = Path.GetDirectoryName(Path.GetFullPath(outputPath));
    if (string.IsNullOrWhiteSpace(outputDirectory))
    {
        throw new ArgumentException("Output path must include a valid directory");
    }
    Directory.CreateDirectory(outputDirectory);

    var temporaryPath = outputPath + ".tmp." + Environment.ProcessId;
    var temporaryIndexPath = indexPath is null ? null : indexPath + ".tmp." + Environment.ProcessId;
    try
    {
        var decoder = new OpusDecoder(SampleRate, Channels);
        var totalPcmBytes = 0;
        var index = new List<PacketIndex>();
        using (var input = File.OpenRead(inputPath))
        using (var reader = new BinaryReader(input, Encoding.UTF8, leaveOpen: false))
        using (var output = new FileStream(temporaryPath, FileMode.Create, FileAccess.ReadWrite, FileShare.None))
        using (var writer = new BinaryWriter(output, Encoding.UTF8, leaveOpen: true))
        {
            ValidateHeader(reader, inputPath, out var packetCount);
            WriteWaveHeader(writer, dataLength: 0);

            for (var packetIndex = 0; packetIndex < packetCount; packetIndex++)
            {
                var timestamp = ReadUInt32(reader, "timestamp");
                var radio = reader.ReadByte();
                var length = ReadUInt16(reader, "packet length");
                var payload = ReadExact(reader, length, "Opus payload");
                var decoded = new short[MaxDecodedSamplesPerPacket];
                int sampleCount;
                try
                {
                    sampleCount = decoder.Decode(payload, 0, payload.Length, decoded, 0, decoded.Length, false);
                }
                catch (Exception exception)
                {
                    Console.Error.WriteLine($"Skipping invalid Opus packet at {timestamp} ms: {exception.Message}");
                    continue;
                }
                if (sampleCount <= 0)
                {
                    continue;
                }

                index.Add(new PacketIndex(
                    timestamp,
                    totalPcmBytes / sizeof(short),
                    sampleCount,
                    radio != 0));
                for (var sampleIndex = 0; sampleIndex < sampleCount; sampleIndex++)
                {
                    writer.Write(decoded[sampleIndex]);
                }
                totalPcmBytes += checked(sampleCount * sizeof(short));
            }

            if (input.Position != input.Length)
            {
                throw new InvalidDataException("VPK1 contains trailing bytes");
            }

            writer.Flush();
            PatchWaveSizes(output, totalPcmBytes);
            output.Flush(true);
        }

        File.Move(temporaryPath, outputPath, overwrite: true);
        if (indexPath is not null && temporaryIndexPath is not null)
        {
            var indexDirectory = Path.GetDirectoryName(Path.GetFullPath(indexPath));
            if (string.IsNullOrWhiteSpace(indexDirectory))
            {
                throw new ArgumentException("Index path must include a valid directory");
            }
            Directory.CreateDirectory(indexDirectory);
            File.WriteAllText(
                temporaryIndexPath,
                JsonSerializer.Serialize(index, new JsonSerializerOptions {
                    PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
                }));
            File.Move(temporaryIndexPath, indexPath, overwrite: true);
        }
    }
    finally
    {
        if (File.Exists(temporaryPath))
        {
            File.Delete(temporaryPath);
        }
        if (temporaryIndexPath is not null && File.Exists(temporaryIndexPath))
        {
            File.Delete(temporaryIndexPath);
        }
    }
}

static void ValidateHeader(BinaryReader reader, string inputPath, out uint packetCount)
{
    if (!reader.ReadBytes(4).SequenceEqual("VPK1"u8.ToArray()))
    {
        throw new InvalidDataException($"Invalid VPK1 magic: {inputPath}");
    }

    var version = ReadUInt16(reader, "version");
    if (version != 1)
    {
        throw new InvalidDataException($"Unsupported VPK1 version: {version}");
    }
    packetCount = ReadUInt32(reader, "packet count");
}

static byte[] ReadExact(BinaryReader reader, int length, string field)
{
    var bytes = reader.ReadBytes(length);
    if (bytes.Length != length)
    {
        throw new InvalidDataException($"Truncated VPK1 {field}");
    }
    return bytes;
}

static ushort ReadUInt16(BinaryReader reader, string field)
{
    var bytes = ReadExact(reader, sizeof(ushort), field);
    return BinaryPrimitives.ReadUInt16LittleEndian(bytes);
}

static uint ReadUInt32(BinaryReader reader, string field)
{
    var bytes = ReadExact(reader, sizeof(uint), field);
    return BinaryPrimitives.ReadUInt32LittleEndian(bytes);
}

static void WriteWaveHeader(BinaryWriter writer, int dataLength)
{
    writer.Write(Encoding.ASCII.GetBytes("RIFF"));
    writer.Write(36 + dataLength);
    writer.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
    writer.Write(16);
    writer.Write((short)1);
    writer.Write(Channels);
    writer.Write(SampleRate);
    writer.Write(SampleRate * Channels * BitsPerSample / 8);
    writer.Write((short)(Channels * BitsPerSample / 8));
    writer.Write(BitsPerSample);
    writer.Write(Encoding.ASCII.GetBytes("data"));
    writer.Write(dataLength);
}

static void PatchWaveSizes(FileStream output, int dataLength)
{
    output.Position = 4;
    using (var writer = new BinaryWriter(output, Encoding.UTF8, leaveOpen: true))
    {
        writer.Write(36 + dataLength);
        output.Position = 40;
        writer.Write(dataLength);
    }
    output.Position = output.Length;
}

readonly record struct DecoderOptions(string Input, string Output, string? Index);
readonly record struct PacketIndex(uint TimestampMs, int SampleStart, int SampleCount, bool Radio);
