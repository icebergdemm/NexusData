#!/bin/sh
set -eu

model_dir=/opt/models/voice-synthetic-model
revision=de3cde5a29c449bb5268814e421b46bf6ebdcd72
base_url="https://huggingface.co/MelodyMachine/Deepfake-audio-detection-V2/resolve/$revision"

install -d -o root -g qwen -m 0750 "$model_dir"
temp_dir=$(mktemp -d "$model_dir/.download.XXXXXX")
trap 'for file in "$temp_dir"/*; do [ ! -e "$file" ] || rm -f "$file"; done; rmdir "$temp_dir"' EXIT HUP INT TERM

download() {
    name=$1
    expected=$2
    curl --fail --location --retry 3 --connect-timeout 10 --max-time 300 \
        "$base_url/$name" --output "$temp_dir/$name"
    printf '%s  %s\n' "$expected" "$temp_dir/$name" | sha256sum --check --status
    chown root:qwen "$temp_dir/$name"
    chmod 0640 "$temp_dir/$name"
    mv -f "$temp_dir/$name" "$model_dir/$name"
}

download config.json a7ff31ca7ba4dc7fb5c4847d6dff0cb8daa1f0ec512e6ff8190664874c5b2806
download preprocessor_config.json 8cdfd65ff4115423185a1512bdae100e2e0cd744f5b322417429944aaafd0827
download model.safetensors 997d9ce59e63151d5e444a6fa7c863986d0e56d515f67321bd705ac3b01bc38c
