#!/bin/bash

function _transcode {
	local driverMod="xe"
	local realPath=$(realpath ${BASH_SOURCE[0]})
	local realPath="${realPath%/*}/"
#	local testVid="test_stream.265"
	local testVid="big-buck-bunny-1080p-60fps-30sec.h264"
	local encodeFrom="h264"
	local encodeTo="h265"
	local inPath="${realPath}${testVid}"
	local outPath="${realPath}temp.${encodeTo}"
	local encodeCmd="sample_multi_transcode"
	local pid=""
	local card=0
	local failed=0
	local tmpPath="${realPath}/content"

	source ${realPath}../../../../common/deviceDetect.sh

	#TODO: check for sample_multi_transcode and return -1 if missing.
	
	if type sample_multi_transcode>/dev/null 2>&1; then

		export LIBVA_DRIVER_NAME=iHD
		
		if [ -d /sys/bus/pci/drivers/${driverMod}/ ]; then
			printf "Running workload test\n"
		
			for pciDev in $(ls /sys/bus/pci/drivers/${driverMod}/ | grep 00); do
				path=/sys/bus/pci/drivers/${driverMod}/${pciDev}
				drmpath=/sys/bus/pci/drivers/${driverMod}/${pciDev}/drm
			
				if [ -d "$drmpath" ]; then
					device_id=$(cat $path/device)
					printf "Executing workload test on Device $((card+1)) PCI:$pci, ID:$device_id \n"
					dri_render=/dev/dri/$(ls "$drmpath" | grep render)
					echo card:/dev/dri/$(ls "$drmpath" | grep card) render:$dri_render
					card=$((card+1))
					
					if [[ -f "$outPath" ]]; then
						rm -f "$outPath"
					fi
					($encodeCmd -i::${encodeFrom} $inPath -o::${encodeTo} \
						$outPath -hw -u 7 -async 5 -b 8000 -qsv-ff \
						-device $dri_render &> ${tmpPath}device${card}.tmp) &
					pid="$! $pid"
				fi
			done
		
			printf "Waiting for workload test to complete.\n"
			_procWatch "$pid"

			for (( log=1; log < $((card+1)); log++ )); do
				if [[ $(grep "test PASSED" ${tmpPath}device${log}.tmp) ]]; then
					printf "Workload test passed on device $log\n"
				else
					cat ${tmpPath}device${log}.tmp
					printf "***********************************\n"
					printf "Workload test failed on device $log\n"
					printf "***********************************\n"
					failed=1
				fi
			done
		
			if [[ -f ${origPath}/$assets/temp1.tmp ]]; then
				rm -f ${origPath}/$assets/*.tmp
			fi
		
			if [ "$failed" -eq 1 ]; then
				printf "Workload test failed on at least one device.\n"
				return 1
			else 
				printf "************************************\n"
				printf "Workload test passed on all devices.\n"
				printf "************************************\n"
				return 0
			fi	
		fi
	else
		printf "sample_multi_transcode is missing. Test cannot run.\n"
		return -1
	fi
}
