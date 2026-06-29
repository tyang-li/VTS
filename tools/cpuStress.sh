#!/bin/sh
# Copyright (C) 2024-2026 Intel Corporation

# --------------------------------------------------------------------
# cpuStress.sh — Stress CPUs and memory with cgroup v2 caps and headroom.
# POSIX sh version (Ubuntu-compatible without Bash features).
#
# Policy:
#  - cgroup memory.max = TARGET_MEM% of physical RAM (default 80%)
#  - stress-ng allocation = ALLOC_PCT% of memory.max (default 75%)
#  - memory.high = 95% of memory.max to throttle before hard cap
#
# Usage:
#   sudo ./cpuStress.sh
#   sudo TARGET_CPU=70 TARGET_MEM=80 ALLOC_PCT=70 ./cpuStress.sh
# --------------------------------------------------------------------

# Remove -e flag to prevent script from exiting on errors
# CPU stress failures should be non-fatal to VTS execution
set -u

# ---- Tunables ------------------------------------------------------
: "${TARGET_CPU:=80}"             # percent of total CPU capacity
: "${TARGET_MEM:=80}"             # percent of physical RAM for cgroup cap
: "${ALLOC_PCT:=75}"              # percent of cgroup cap to allocate
: "${PERIOD_US:=100000}"          # cgroup CPU period (us)
: "${CG:=/sys/fs/cgroup/cpuStress}"# cgroup path
: "${DISABLE_SWAP:=1}"            # disable swap inside cgroup
: "${OUTPUT_MODE:=screen}"        # output mode: 'screen' or 'csv'
: "${CSV_FILE:=}"                 # CSV file path (if OUTPUT_MODE=csv)

# ---- Helpers -------------------------------------------------------
warn() { echo "WARNING: $*" >&2; }
fail() { echo "ERROR: $*" >&2; return 1 2>/dev/null || exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || fail "$1 not found"; }

get_cpu_temp() {
  # Get individual CPU socket temperatures (max 2 sockets)
  cpu1_temp="N/A"
  cpu2_temp="N/A"
  cpu_temps_found=0
  
  # Collect all CPU thermal zones first
  cpu_zones=""
  for zone in /sys/class/thermal/thermal_zone*/temp; do
    if [ -r "$zone" ]; then
      zone_dir="${zone%/temp}"
      zone_type_file="$zone_dir/type"
      zone_type=""
      if [ -r "$zone_type_file" ]; then
        zone_type="$(cat "$zone_type_file" 2>/dev/null || echo "")"
      fi
      
      temp_raw="$(cat "$zone" 2>/dev/null || echo "0")"
      # Convert from millicelsius to celsius if value looks like millicelsius
      if [ "$temp_raw" -gt 1000 ]; then
        temp_celsius=$(( temp_raw / 1000 ))
      else
        temp_celsius="$temp_raw"
      fi
      
      # Only include reasonable temperature values (0-150C)
      if [ "$temp_celsius" -gt 0 ] && [ "$temp_celsius" -lt 150 ]; then
        # Check if this looks like a CPU/processor thermal zone
        if echo "$zone_type" | grep -qi "cpu\|processor\|x86_pkg_temp\|coretemp\|package"; then
          # Try multiple ways to extract socket/package number
          socket_id=""
          # Method 1: Look for numbers in zone type
          socket_id="$(echo "$zone_type" | grep -o '[0-9]\+' | head -1 || echo "")"
          # Method 2: If no number in type, use zone number
          if [ -z "$socket_id" ]; then
            zone_num="$(echo "$zone" | grep -o 'thermal_zone[0-9]\+' | grep -o '[0-9]\+' || echo "")"
            socket_id="$zone_num"
          fi
          
          # Add to list with socket ID and temperature
          if [ -n "$socket_id" ]; then
            if [ -z "$cpu_zones" ]; then
              cpu_zones="${socket_id}:${temp_celsius}"
            else
              cpu_zones="${cpu_zones} ${socket_id}:${temp_celsius}"
            fi
          fi
        fi
      fi
    fi
  done
  
  # Process the collected zones and assign to CPU1/CPU2
  if [ -n "$cpu_zones" ]; then
    # Sort zones by socket ID and assign
    sorted_zones="$(echo "$cpu_zones" | tr ' ' '\n' | sort -t':' -k1,1n | tr '\n' ' ')"
    cpu_count=0
    
    for zone_info in $sorted_zones; do
      temp_val="$(echo "$zone_info" | cut -d':' -f2)"
      cpu_count=$((cpu_count + 1))
      
      if [ "$cpu_count" -eq 1 ]; then
        cpu1_temp="$temp_val"
      elif [ "$cpu_count" -eq 2 ]; then
        cpu2_temp="$temp_val"
        break
      fi
    done
  fi
  
  printf "%s,%s" "$cpu1_temp" "$cpu2_temp"
}

get_cpu_power() {
  # Get individual CPU socket power (max 2 sockets)
  cpu1_power="N/A"
  cpu2_power="N/A"
  
  # Check multiple RAPL domains for multi-socket systems
  for rapl_domain in /sys/class/powercap/intel-rapl/intel-rapl:*/energy_uj; do
    if [ -r "$rapl_domain" ]; then
      domain_dir="${rapl_domain%/energy_uj}"
      domain_name_file="$domain_dir/name"
      domain_name=""
      if [ -r "$domain_name_file" ]; then
        domain_name="$(cat "$domain_name_file" 2>/dev/null || echo "")"
      fi
      
      # Only process package/socket domains, not sub-domains
      if echo "$domain_name" | grep -q "package" || [ -z "$domain_name" ]; then
        domain_num="$(echo "$rapl_domain" | grep -o 'intel-rapl:[0-9]\+' | grep -o '[0-9]\+' || echo "0")"
        # Validate domain_num is purely numeric before eval (prevents shell injection)
        case "$domain_num" in ''|*[!0-9]*) domain_num=0 ;; esac
        
        # Get previous energy for this domain
        eval "prev_domain_energy=\${prev_energy_${domain_num}:-0}"
        
        if [ -n "$prev_domain_energy" ] && [ "$prev_domain_energy" != "0" ]; then
          cur_energy="$(cat "$rapl_domain" 2>/dev/null || echo "0")"
          energy_diff=$(( cur_energy - prev_domain_energy ))
          
          if [ "$energy_diff" -gt 0 ] && [ "${time_diff_us:-0}" -gt 0 ]; then
            domain_power_uw=$(( energy_diff / time_diff_us * 1000000 ))
            domain_power_w="$(awk -v uw="$domain_power_uw" 'BEGIN{printf "%.1f", uw/1000000}')"
            
            if [ "$domain_num" = "0" ]; then
              cpu1_power="$domain_power_w"
            elif [ "$domain_num" = "1" ]; then
              cpu2_power="$domain_power_w"
            elif [ "$cpu1_power" = "N/A" ]; then
              # If no clear socket ID, assign to first available
              cpu1_power="$domain_power_w"
            fi
          fi
        fi
      fi
    fi
  done
  
  printf "%s,%s" "$cpu1_power" "$cpu2_power"
}

get_memory_info() {
  
  # Memory power - try different RAPL memory domain paths
  mem_power="N/A"
  rapl_mem_path=""
  
  # Try common RAPL memory paths
  for path in "/sys/class/powercap/intel-rapl/intel-rapl:0/intel-rapl:0:2/energy_uj" \
              "/sys/class/powercap/intel-rapl/intel-rapl:0/intel-rapl:0:1/energy_uj" \
              "/sys/class/powercap/intel-rapl/intel-rapl:1/energy_uj"; do
    if [ -r "$path" ]; then
      rapl_mem_path="$path"
      break
    fi
  done
  
  if [ -n "$rapl_mem_path" ] && [ -n "${prev_mem_energy:-}" ]; then
    cur_mem_energy="$(cat "$rapl_mem_path" 2>/dev/null || echo "0")"
    mem_energy_diff=$(( cur_mem_energy - prev_mem_energy ))
    if [ "$mem_energy_diff" -gt 0 ] && [ "${time_diff_us:-0}" -gt 0 ]; then
      mem_power_uw=$(( mem_energy_diff / time_diff_us * 1000000 ))
      mem_power="$(awk -v uw="$mem_power_uw" 'BEGIN{printf "%.1f", uw/1000000}')"
    fi
  fi
  
  printf "%s" "$mem_power"
}

human_bytes() {
  bytes="$1"
  units="B KiB MiB GiB TiB"
  i=0
  val="$bytes"
  while [ "$val" -ge 1024 ]; do
    val=$(( val / 1024 ))
    i=$(( i + 1 ))
    [ "$i" -ge 4 ] && break
  done
  case "$i" in
    0) unit=B ;;
    1) unit=KiB ;;
    2) unit=MiB ;;
    3) unit=GiB ;;
    4) unit=TiB ;;
  esac
  printf "%d %s" "$val" "$unit"
}

percent() {
  a="$1"; b="$2"
  if [ "$b" -eq 0 ]; then
    printf "0.00"
  else
    awk -v a="$a" -v b="$b" 'BEGIN{printf "%.2f", (a/b)*100}'
  fi
}

# ---- Sanity checks -------------------------------------------------
need_cmd stress-ng
need_cmd awk
need_cmd nproc

if ! mount | grep -q "type cgroup2"; then
  fail "cgroup v2 not detected. Enable unified cgroups or upgrade Ubuntu."
fi

[ "$(id -u)" -eq 0 ] || fail "Run as root (sudo)."

case "$TARGET_CPU" in ''|*[!0-9]*) fail "TARGET_CPU must be integer";; esac
case "$TARGET_MEM" in ''|*[!0-9]*) fail "TARGET_MEM must be integer";; esac
case "$ALLOC_PCT"  in ''|*[!0-9]*) fail "ALLOC_PCT must be integer";; esac

# Enable controllers
if [ -r /sys/fs/cgroup/cgroup.controllers ]; then
  echo "+cpu +memory" > /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || true
fi

# ---- Machine capacities --------------------------------------------
CORES="$(nproc --all)"
MEMTOTAL_KB="$(awk '/MemTotal:/ {print $2}' /proc/meminfo)"
MEMTOTAL_BYTES=$(( MEMTOTAL_KB * 1024 ))
MEMCAP_BYTES=$(( MEMTOTAL_BYTES * TARGET_MEM / 100 ))
MEMHIGH_BYTES=$(( MEMCAP_BYTES * 95 / 100 ))
CPUQUOTA_US=$(( PERIOD_US * CORES * TARGET_CPU / 100 ))
ALLOC_BYTES=$(( MEMCAP_BYTES * ALLOC_PCT / 100 ))
HEADROOM=$(( MEMCAP_BYTES / 100 ))
if [ "$ALLOC_BYTES" -gt $(( MEMCAP_BYTES - HEADROOM )) ]; then
  ALLOC_BYTES=$(( MEMCAP_BYTES - HEADROOM ))
fi

# ---- Create cgroup and set limits ---------------------------------
mkdir -p "$CG"
printf "%s %s" "$CPUQUOTA_US" "$PERIOD_US" > "${CG}/cpu.max"
printf "%s" "$MEMCAP_BYTES"  > "${CG}/memory.max"
printf "%s" "$MEMHIGH_BYTES" > "${CG}/memory.high" 2>/dev/null || true
[ -w "${CG}/memory.oom.group" ] && echo 1 > "${CG}/memory.oom.group" || true
[ "$DISABLE_SWAP" -eq 1 ] && [ -w "${CG}/memory.swap.max" ] && echo 0 > "${CG}/memory.swap.max" || true

echo "== Caps =="
echo "Cores:                $CORES"
echo "CPU quota:            ${CPUQUOTA_US} us per ${PERIOD_US} us (~${TARGET_CPU}% of $CORES cores)"
echo "Memory total:         $(human_bytes "$MEMTOTAL_BYTES")"
echo "memory.max:           $(human_bytes "$MEMCAP_BYTES") (${TARGET_MEM}% of physical)"
echo "stress-ng allocation: $(human_bytes "$ALLOC_BYTES") (${ALLOC_PCT}% of cap)"
echo

# ---- Start stress-ng ----------------------------------------------
echo "Starting stress-ng..."
stress-ng --cpu "$CORES" \
          --cpu-method all \
          --vm "$CORES" \
          --vm-bytes "${ALLOC_BYTES}B" \
          --vm-keep \
          --memthrash "$CORES" \
          --metrics-brief \
          --timeout 0 >/dev/null 2>&1 &
STRESS_PID="$!"
echo "$STRESS_PID" > "${CG}/cgroup.procs"

# ---- Cleanup on exit ----------------------------------------------
cleanup() {
  echo
  echo "Stopping stress-ng..."
  kill -INT "$STRESS_PID" 2>/dev/null || true
  sleep 1
  kill -KILL "$STRESS_PID" 2>/dev/null || true
  
  # Reset cgroup limits before removing directory
  if [ -d "$CG" ]; then
    printf "max %s" "$PERIOD_US" > "${CG}/cpu.max" 2>/dev/null || true
    printf "max" > "${CG}/memory.max" 2>/dev/null || true
    printf "max" > "${CG}/memory.high" 2>/dev/null || true
    # Remove the cgroup directory
    rmdir "$CG" 2>/dev/null || true
  fi
  echo "Cleanup complete."
}
trap cleanup INT TERM EXIT

# ---- Live monitor -------------------------------------------------
echo
if [ "$OUTPUT_MODE" = "csv" ]; then
  # Set CSV file path - use provided path or generate default
  if [ -z "$CSV_FILE" ]; then
    CSV_FILE="stress_metrics_$(date +%Y%m%d_%H%M%S).csv"
  fi
  echo "== Live metrics being written to: $CSV_FILE =="
  echo "Time,CPU%,CPU1_Temp_C,CPU1_Power_W,CPU2_Temp_C,CPU2_Power_W,Mem%,Mem_Power_W" > "$CSV_FILE"
else
  echo "== Live metrics (Ctrl+C to stop) =="
  printf "%-8s | %-6s | %-6s | %-7s | %-6s | %-7s | %-6s | %-7s\n" \
    "Time" "CPU%" "CPU1°C" "CPU1 W" "CPU2°C" "CPU2 W" "Mem%" "Mem W"
fi

prev_usage="$(awk '/usage_usec/ {print $2}' "${CG}/cpu.stat")"
prev_ts_ns="$(date +%s%N)"
# Initialize energy counters for power calculation (multi-socket support)
# Initialize all RAPL domains
for rapl_domain in /sys/class/powercap/intel-rapl/intel-rapl:*/energy_uj; do
  if [ -r "$rapl_domain" ]; then
    domain_num="$(echo "$rapl_domain" | grep -o 'intel-rapl:[0-9]\+' | grep -o '[0-9]\+' || echo "0")"
    # Validate domain_num is purely numeric before eval (prevents shell injection)
    case "$domain_num" in ''|*[!0-9]*) domain_num=0 ;; esac
    energy_value="$(cat "$rapl_domain" 2>/dev/null || echo "0")"
    eval "prev_energy_${domain_num}=\"$energy_value\""
  fi
done

# Try to find memory RAPL path for initialization
rapl_mem_init_path=""
for path in "/sys/class/powercap/intel-rapl/intel-rapl:0/intel-rapl:0:2/energy_uj" \
            "/sys/class/powercap/intel-rapl/intel-rapl:0/intel-rapl:0:1/energy_uj" \
            "/sys/class/powercap/intel-rapl/intel-rapl:1/energy_uj"; do
  if [ -r "$path" ]; then
    rapl_mem_init_path="$path"
    break
  fi
done
prev_mem_energy="$(cat "$rapl_mem_init_path" 2>/dev/null || echo "0")"

while kill -0 "$STRESS_PID" 2>/dev/null; do
  sleep 1
  cur_usage="$(awk '/usage_usec/ {print $2}' "${CG}/cpu.stat")"
  cur_ts_ns="$(date +%s%N)"
  du=$(( cur_usage - prev_usage ))
  dt=$(( cur_ts_ns - prev_ts_ns ))
  time_diff_us=$(( dt / 1000 ))
  
  cpu_pct="$(awk -v du="$du" -v dt="$dt" -v cores="$CORES" \
             'BEGIN{if(dt<=0||cores<=0){printf "0.00"} else {printf "%.2f", ((du/1000000.0)/(dt/1000000000.0))/cores*100}}')"
  
  mem_cur_bytes="$(cat "${CG}/memory.current")"
  mem_max_bytes="$(cat "${CG}/memory.max")"
  mem_pct="$(percent "$mem_cur_bytes" "$mem_max_bytes")"
  mem_cur_gb="$(awk -v bytes="$mem_cur_bytes" 'BEGIN{printf "%.2f", bytes/1073741824}')"
  mem_max_gb="$(awk -v bytes="$mem_max_bytes" 'BEGIN{printf "%.2f", bytes/1073741824}')"
  
  # Get temperature and power metrics
  cpu_temp_info="$(get_cpu_temp)"
  cpu_power_info="$(get_cpu_power)"
  mem_info="$(get_memory_info)"
  
  # Parse CPU info into separate variables
  cpu1_temp="$(echo "$cpu_temp_info" | awk -F',' '{print $1}')"
  cpu2_temp="$(echo "$cpu_temp_info" | awk -F',' '{print $2}')"
  cpu1_power="$(echo "$cpu_power_info" | awk -F',' '{print $1}')"
  cpu2_power="$(echo "$cpu_power_info" | awk -F',' '{print $2}')"
  
  # Parse memory info (only power now)
  mem_power="$mem_info"
  
  if [ "$OUTPUT_MODE" = "csv" ]; then
    # Write to CSV file
    printf "%s,%s,%s,%s,%s,%s,%s,%s\n" \
      "$(date '+%H:%M:%S')" \
      "$cpu_pct" \
      "$cpu1_temp" \
      "$cpu1_power" \
      "$cpu2_temp" \
      "$cpu2_power" \
      "$mem_pct" \
      "$mem_power" >> "$CSV_FILE"
  else
    # Display on screen
    printf "%-8s | %6s | %6s | %7s | %6s | %7s | %6s | %7s\n" \
      "$(date '+%H:%M:%S')" \
      "$cpu_pct" \
      "$cpu1_temp" \
      "$cpu1_power" \
      "$cpu2_temp" \
      "$cpu2_power" \
      "$mem_pct" \
      "$mem_power"
  fi
  
  # Update previous values for next iteration
  prev_usage="$cur_usage"
  prev_ts_ns="$cur_ts_ns"
  
  # Update all RAPL domain energies
  for rapl_domain in /sys/class/powercap/intel-rapl/intel-rapl:*/energy_uj; do
    if [ -r "$rapl_domain" ]; then
      domain_num="$(echo "$rapl_domain" | grep -o 'intel-rapl:[0-9]\+' | grep -o '[0-9]\+' || echo "0")"
      # Validate domain_num is purely numeric before eval (prevents shell injection)
      case "$domain_num" in ''|*[!0-9]*) domain_num=0 ;; esac
      energy_value="$(cat "$rapl_domain" 2>/dev/null)"
      if [ -n "$energy_value" ]; then
        eval "prev_energy_${domain_num}=\"$energy_value\""
      fi
    fi
  done
  
  # Update memory energy using the same path detection logic
  if [ -n "$rapl_mem_init_path" ]; then
    prev_mem_energy="$(cat "$rapl_mem_init_path" 2>/dev/null || echo "$prev_mem_energy")"
  fi
done
