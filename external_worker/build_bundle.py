"""Create a minimal deployable bundle without NexusData application config or secrets."""
import argparse
import shutil
import stat
from pathlib import Path


def main():
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path, help="new or existing bundle directory")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit("Output directory must be empty so no application configuration is copied along")
    output.mkdir(parents=True, exist_ok=True)
    package = repo / "external_worker"
    worker_package = output / "external_worker"
    plugins_out = worker_package / "plugins"
    plugins_out.mkdir(parents=True, exist_ok=True)
    for relative in ("__init__.py", "runner.py", "plugins/__init__.py"):
        destination = worker_package / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(package / relative, destination)
    for plugin in (package / "plugins").glob("*.py"):
        if plugin.name != "__init__.py":
            shutil.copy2(plugin, plugins_out / plugin.name)
    for filename in ("requirements.txt", ".env.example", "README.md", "nexusdata-worker.service.example"):
        shutil.copy2(package / filename, worker_package / filename)
    shutil.copy2(repo / "services_voice_profiles.py", output / "services_voice_profiles.py")
    shutil.copy2(repo / "external_worker" / "requirements.txt", output / "requirements.txt")
    shutil.copy2(repo / "external_worker" / ".env.example", output / ".env.example")
    shutil.copy2(repo / "external_worker" / "README.md", output / "README.md")
    decoder_publish = (repo / "tools" / "VoiceProfileDecoder" / "bin" / "Release" /
                       "net8.0" / "linux-x64" / "publish")
    decoder_build = repo / "tools" / "VoiceProfileDecoder" / "bin" / "Release" / "net8.0"
    decoder_output = output / "tools" / "VoiceProfileDecoder"
    if decoder_publish.is_dir():
        decoder_output.mkdir(parents=True, exist_ok=True)
        for artifact in decoder_publish.iterdir():
            if artifact.is_file():
                destination = decoder_output / artifact.name
                shutil.copy2(artifact, destination)
                if artifact.name == "VoiceProfileDecoder":
                    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    elif decoder_build.is_dir():
        # Framework-dependent fallback for worker images that include .NET 8.
        decoder_output.mkdir(parents=True, exist_ok=True)
        for suffix in (".dll", ".deps.json", ".runtimeconfig.json"):
            for artifact in decoder_build.glob(f"*{suffix}"):
                shutil.copy2(artifact, decoder_output / artifact.name)
    print(f"Worker bundle created: {output}")


if __name__ == "__main__":
    main()
