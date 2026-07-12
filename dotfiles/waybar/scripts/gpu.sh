#!/usr/bin/env bash

# Check if NVIDIA GPU is present and the driver responds
if command -v nvidia-smi &> /dev/null && nvidia-smi &> /dev/null; then
    # NVIDIA GPU
    gpu_usage=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits | head -n1)
    gpu_temp=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits | head -n1)

    # Determine icon based on temperature
    if [ "$gpu_temp" -ge 80 ]; then
        icon="󰢮"
    elif [ "$gpu_temp" -ge 60 ]; then
        icon="󰾲"
    else
        icon="󰾲"
    fi

    echo "{\"text\":\"$icon  $gpu_usage% $gpu_temp°C\",\"tooltip\":\"GPU Usage: $gpu_usage%\\nGPU Temperature: $gpu_temp°C\"}"

elif lspci | grep -i 'vga.*amd' &> /dev/null || lspci | grep -i 'vga.*radeon' &> /dev/null; then
    # AMD GPU
    if command -v radeontop &> /dev/null; then
        # Use radeontop for AMD
        gpu_data=$(radeontop -d - -l 1 2>/dev/null | tail -n1)
        gpu_usage=$(echo "$gpu_data" | grep -oP 'gpu \K[0-9]+' || echo "0")

        # Try to get temperature from sensors
        if command -v sensors &> /dev/null; then
            gpu_temp=$(sensors | grep -i 'edge\|junction' | head -n1 | grep -oP '\+\K[0-9]+' || echo "N/A")
        else
            gpu_temp="N/A"
        fi
    else
        # No way to read usage → hide the module
        echo '{"text": ""}'
        exit 0
    fi

    if [ "$gpu_temp" != "N/A" ] && [ "$gpu_temp" -ge 80 ]; then
        icon="󰢮"
    elif [ "$gpu_temp" != "N/A" ] && [ "$gpu_temp" -ge 60 ]; then
        icon="󰾲"
    else
        icon="󰾲"
    fi

    if [ "$gpu_temp" = "N/A" ]; then
        echo "{\"text\":\"$icon  $gpu_usage%\",\"tooltip\":\"GPU Usage: $gpu_usage%\"}"
    else
        echo "{\"text\":\"$icon  $gpu_usage% $gpu_temp°C\",\"tooltip\":\"GPU Usage: $gpu_usage%\\nGPU Temperature: $gpu_temp°C\"}"
    fi

elif lspci | grep -i 'vga.*intel' &> /dev/null; then
    # Intel integrated GPU
    if command -v intel_gpu_top &> /dev/null; then
        gpu_usage=$(timeout 1 intel_gpu_top -J -s 100 2>/dev/null | jq -r '.engines."Render/3D".busy' 2>/dev/null || echo "N/A")
    else
        gpu_usage="N/A"
    fi

    # Try to get temperature from sensors
    if command -v sensors &> /dev/null; then
        gpu_temp=$(sensors 2>/dev/null | grep -i 'package id 0' | grep -oP '\+\K[0-9]+' | head -n1 || echo "N/A")
    else
        gpu_temp="N/A"
    fi

    icon="󰾲"

    if [ "$gpu_usage" = "N/A" ]; then
        echo '{"text": ""}'
    elif [ "$gpu_temp" = "N/A" ]; then
        echo "{\"text\":\"$icon  $gpu_usage%\",\"tooltip\":\"GPU Usage: $gpu_usage%\"}"
    else
        echo "{\"text\":\"$icon  $gpu_usage% $gpu_temp°C\",\"tooltip\":\"GPU Usage: $gpu_usage%\\nGPU Temperature: $gpu_temp°C\"}"
    fi
else
    # No recognized GPU → hide the module
    echo '{"text": ""}'
fi
