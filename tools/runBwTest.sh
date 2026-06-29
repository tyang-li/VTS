#!/bin/bash
# Copyright (C) 2024-2026 Intel Corporation

Usage() {
    echo "Usage: $0 <tool> <direction> <engine> <gpus> [iterations] [size] [mode] [additional_args]"
    echo
    echo "Parameters:"
    echo "  tool:       ze_bandwidth | memory_benchmark_l0"
    echo "  direction:  h2d | d2h | bidirectional"
    echo "  engine:     copy | compute"
    echo "  gpus:       GPU list (e.g., '0,1,2' or '0-3' or 'all')"
    echo "  iterations: Number of test iterations (optional, default: 500)"
    echo "  size:       Buffer size in bytes (optional, default: 268435456)"
    echo "  mode:       Execution mode (optional, default: serial)"
    echo "  additional_args: Extra tool-specific arguments (optional)"
    echo
    echo "Examples:"
    echo "  $0 ze_bandwidth h2d copy 0,1,2"
    echo "  $0 memory_benchmark_l0 bidirectional compute all 1000 2147483648 parallel"
    echo "  $0 ze_bandwidth d2h copy 0-3 500 1073741824 serial --csv"
    echo
    exit 1
}

# Check minimum arguments
if [[ $# -lt 4 ]]; then
    Usage
fi

# Parse command line arguments
tool=$1
direction=$2
engine=$3
gpu_spec=$4
iterations=${5:-500}  # Default to 500 iterations if not specified
size=${6:-268435456}  # Default to 256MB if not specified
mode=${7:-serial}     # Default to serial mode if not specified
shift 7
additional_args="$@"

# Validate tool selection
if [[ "$tool" != "ze_bandwidth" && "$tool" != "memory_benchmark_l0" ]]; then
    echo "Error: Invalid tool '$tool'. Must be 'ze_bandwidth' or 'memory_benchmark_l0'"
    Usage
fi

# Validate direction selection
if [[ "$direction" != "h2d" && "$direction" != "d2h" && "$direction" != "bidirectional" ]]; then
    echo "Error: Invalid direction '$direction'. Must be 'h2d', 'd2h', or 'bidirectional'"
    Usage
fi

# Validate engine selection
if [[ "$engine" != "copy" && "$engine" != "compute" ]]; then
    echo "Error: Invalid engine '$engine'. Must be 'copy' or 'compute'"
    Usage
fi

# Function to parse GPU specification
parse_gpu_list() {
    local spec=$1
    local gpu_list=()
    
    if [[ "$spec" == "all" ]]; then
        # Auto-detect available GPUs using memory_benchmark_l0 --hwInfo
        if [[ -f "./memory_benchmark_l0" ]]; then
            local hwinfo_output=$(./memory_benchmark_l0 --hwInfo 2>/dev/null)
            # Extract l0DeviceIndex values from lines like:
            # "Device: ... , select this device with --l0DriverIndex=0 --l0DeviceIndex=N"
            local device_indices=($(echo "$hwinfo_output" | grep -o -E -- '--l0DeviceIndex=[0-9]+' | cut -d'=' -f2 | sort -n))
            
            if [[ ${#device_indices[@]} -gt 0 ]]; then
                gpu_list=("${device_indices[@]}")
            else
                echo "Warning: No GPUs detected via memory_benchmark_l0 --hwInfo, using default fallback" >&2
                gpu_list=(0)  # Default fallback to single GPU
            fi
        else
            echo "Warning: memory_benchmark_l0 not found, using default fallback" >&2
            gpu_list=(0)  # Default fallback to single GPU
        fi
    elif [[ "$spec" =~ ^[0-9]+-[0-9]+$ ]]; then
        # Range format: 0-3
        local start=$(echo $spec | cut -d'-' -f1)
        local end=$(echo $spec | cut -d'-' -f2)
        for ((i=start; i<=end; i++)); do
            gpu_list+=($i)
        done
    elif [[ "$spec" =~ ^[0-9,]+$ ]]; then
        # Comma-separated format: 0,1,2
        IFS=',' read -ra gpu_list <<< "$spec"
    else
        echo "Error: Invalid GPU specification '$spec'"
        echo "Use format: 'all', '0,1,2', or '0-3'"
        exit 1
    fi
    
    echo "${gpu_list[@]}"
}

# Function to run bandwidth test on a single GPU
run_bw_test() {
    local gpu_id=$1
    
    # Add clear device identification header
    echo "DEVICE: $gpu_id"
    
    # Set Level Zero environment for device targeting
    export ZE_ENABLE_PCI_ID_DEVICE_ORDER=1
    # Note: Using -d parameter for device targeting instead of ZE_AFFINITY_MASK to avoid conflicts
    
    if [[ "$tool" == "ze_bandwidth" ]]; then
        # Map direction to ze_bandwidth test types
        local ze_test_type
        case "$direction" in
            "h2d") ze_test_type="h2d" ;;
            "d2h") ze_test_type="d2h" ;;
            "bidirectional") ze_test_type="bidir" ;;
        esac
        
        # Map engine to ze_bandwidth engine group
        local engine_args=""
        case "$engine" in
            "copy") engine_args="-g 1" ;;  # Copy engines are in group 1
            "compute") engine_args="-g 0" ;;  # Compute engines are in group 0
        esac
        
        echo "Running ze_bandwidth on GPU $gpu_id (test=$ze_test_type, engine_group=${engine_args#-g }, iterations=$iterations, size=$size)"
        echo "Command: ./ze_bandwidth $engine_args -d $gpu_id -i $iterations -s $size -t $ze_test_type $additional_args"
        ./ze_bandwidth $engine_args -d $gpu_id -i $iterations -s $size -t $ze_test_type $additional_args 2>&1
    elif [[ "$tool" == "memory_benchmark_l0" ]]; then
        # For UsmCopyConcurrentMultipleBlits test, direction is controlled by blitter parameters only
        # Map engine to blitter bit masks for UsmCopyConcurrentMultipleBlits
        # Both h2dBlitters and d2hBlitters parameters are always required
        local blitter_args=""
        case "$engine" in
            "copy") 
                # Use copy engines - set bit masks for h2d and d2h blitters
                case "$direction" in
                    "h2d") blitter_args="--h2dBlitters=1 --d2hBlitters=0" ;;  # All copy engines for h2d, none for d2h
                    "d2h") blitter_args="--h2dBlitters=0 --d2hBlitters=1" ;;  # None for h2d, all copy engines for d2h
                    "bidirectional") blitter_args="--h2dBlitters=1 --d2hBlitters=1" ;;  # Both directions
                esac
                ;;
            "compute") 
                # UsmConcurrentCopy test uses only specific parameters - device targeting via environment variables
                blitter_args=""  # Parameters handled directly in command execution
                ;;
        esac
        
        echo "Running memory_benchmark_l0 on GPU $gpu_id (direction=$direction, engine=$engine, iterations=$iterations, size=$size)"
        if [[ "$engine" == "compute" ]]; then
            echo "Command: ./memory_benchmark_l0 --test=UsmConcurrentCopy --size=$size --h2dEngine=CCS0 --d2hEngine=BCS --withCopyOffload=0 $additional_args"
            ./memory_benchmark_l0 --test=UsmConcurrentCopy --size=$size --h2dEngine=CCS0 --d2hEngine=BCS --withCopyOffload=0 $additional_args 2>&1
        else
            echo "Command: ./memory_benchmark_l0 --l0DriverIndex=0 --l0DeviceIndex=$gpu_id --iterations=$iterations --test=UsmCopyConcurrentMultipleBlits --size=$size $blitter_args $additional_args"
            ./memory_benchmark_l0 --l0DriverIndex=0 --l0DeviceIndex=$gpu_id --iterations=$iterations --test=UsmCopyConcurrentMultipleBlits --size=$size $blitter_args $additional_args 2>&1
        fi
    fi
    
    # Add clear device identification footer
    echo ""
}

# Parse GPU list
gpu_array=($(parse_gpu_list "$gpu_spec"))

if [[ ${#gpu_array[@]} -eq 0 ]]; then
    echo "Error: No GPUs specified or found"
    exit 1
fi

echo "=========================================="
echo "Bandwidth Test Configuration:"
echo "  Tool: $tool"
echo "  Direction: $direction" 
echo "  Engine: $engine"
echo "  GPUs: ${gpu_array[*]}"
echo "  Mode: $mode"
#echo "  Iterations: $iterations"
#echo "  Size: $size bytes"
#echo "  Additional Args: $additional_args"
echo "=========================================="

# Create array to store background process PIDs
declare -a pids

# Setup signal handler for cleanup
cleanup() {
    echo "Cleaning up background processes..."
    for pid in "${pids[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
        fi
    done
    exit 1
}
trap cleanup SIGINT SIGTERM

# Create temporary directory for individual GPU outputs
temp_dir=$(mktemp -d)
trap "rm -rf $temp_dir" EXIT

# Launch bandwidth tests in parallel for each GPU with output redirection
for gpu in "${gpu_array[@]}"; do
    run_bw_test "$gpu" > "$temp_dir/gpu_$gpu.out" 2>&1 &
    pids+=($!)
done

echo "Launched ${#pids[@]} parallel bandwidth tests..."

# Wait for all background processes to complete
for pid in "${pids[@]}"; do
    wait "$pid"
done

# Display results sequentially for each GPU
echo ""
echo "==================== COMBINED RESULTS ===================="
for gpu in "${gpu_array[@]}"; do
    if [[ -f "$temp_dir/gpu_$gpu.out" ]]; then
        cat "$temp_dir/gpu_$gpu.out"
        echo ""
    fi
done
