# Bidirectional Bandwidth Test — GuC Firmware Hang: Failure Analysis

**Status:** OPEN — root cause identified at the GuC firmware / `xe` driver level. The
user-space `ze_bandwidth` source mitigations reduce severity but do **not** eliminate the hang.

**Reproducer (exact command):**
```
python3 start_vts.py -tn 3 -inst -1 -dir bidirectional -mode parallel -tool ze_bandwidth -engine copy
```
(`-y True` added during investigation only to auto-confirm a pre-flight DRAM-bandwidth warning in a
non-interactive shell; it has no bearing on the hang.)

---

## 1. Executive Summary

When the PCIe bandwidth test is run in **bidirectional + parallel** mode across **all 16 GPUs**, the
**GuC firmware crashes on multiple GPUs**. Most GPUs recover via an automatic GT reset, but at least
one GPU's GuC enters an unrecoverable state (a `GuC Exception` with **no subsequent `reset done`**).
Once a GPU's GuC is dead, any process that tries to create a GuC submission queue on that GPU blocks
**forever** in the kernel (`guc_exec_queue_init`, uninterruptible `D` state). Because the test harness
launches one worker per GPU and then `wait`s for **all** of them, the single stuck worker hangs the
entire run.

Key facts from the latest clean-boot run:

- **7 distinct GPUs** emitted `GuC Crash dump notification`.
- **6** of them recovered (`reset done`); **1** (`0000:da:00.0`) did **not** → permanent hang.
- **~5 of 16 GPUs** completed their measurement before the wedge; the remaining **10 workers** are
  stuck unkillable in `D` state.
- This is **not** the earlier NUMA/UPI bandwidth bug. That bug is fixed (NUMA-local host buffers in
  the rebuilt binary). Single-GPU bidirectional runs are clean (24.9 GB/s). The hang is a separate,
  **load-induced GuC firmware failure** that only appears under full 16-way parallel bidirectional
  load.

---

## 2. Test Environment

| Item | Value |
|---|---|
| Platform | IEIT SYSTEMS NF5468-M7 |
| CPU | 2× Intel Xeon Platinum 8490H (240 logical CPUs, 2 NUMA nodes) |
| GPUs | 16× Intel Arc B-series "Battlemage" (PCI ID `8086:e211`, stepping B0), Gen5 x8 each |
| GPU↔NUMA | GPUs 0–7 → node 0; GPUs 8–15 → node 1 |
| Kernel | 6.17.0-1007-intel |
| GPU driver | `xe` 1.1.0 |
| GuC firmware | `xe/bmg_guc_70.bin` **version 70.55.3** |
| HuC firmware | `xe/bmg_huc.bin` version 8.2.10 |
| Tool | `tools/ze_bandwidth` (rebuilt with NUMA + single-queue source fixes) |
| Test parameters | `-i 500` iterations, `-s 268435456` (256 MiB) per direction, copy engine (`-g 1`) |

---

## 3. What the Test Actually Does

`start_vts.py -tn 3 ... -mode parallel` invokes the harness:

```
bash tools/runBwTest.sh ze_bandwidth bidirectional copy 0,1,...,15 500 268435456 parallel
```

`runBwTest.sh` launches **all 16 GPU workers simultaneously** (no stagger) and waits for every one:

```bash
# tools/runBwTest.sh  (lines 307-318)
for gpu in "${gpu_array[@]}"; do
    run_bw_test "$gpu" > "$temp_dir/gpu_$gpu.out" 2>&1 &   # background, no delay
    pids+=($!)
done
...
for pid in "${pids[@]}"; do
    wait "$pid"        # blocks on ALL workers — one stuck worker hangs the whole run
done
```

Each worker is an independent `ze_bandwidth` process targeting one GPU:

```
./ze_bandwidth -g 1 -d <gpu_id> -i 500 -s 268435456 -t bidir
```

In `-t bidir`, each iteration performs a **Host→Device copy of 256 MiB and a Device→Host copy of
256 MiB** (512 MiB of DMA traffic per iteration), repeated 500 times, on the GPU's single copy
(blitter / `bcs`) engine.

---

## 4. Source Fixes Already Applied (and why they were not enough)

Two fixes were applied to the `ze_bandwidth` source
(`ze_bandwidth_numa_fix/perf_tests/ze_bandwidth/src/ze_bandwidth.cpp`) and compiled into
`tools/ze_bandwidth`:

1. **NUMA-local host buffers** — allocates DMA host buffers on the GPU's local NUMA node, fixing the
   node-1 bandwidth collapse (the original "node-1 traffic over UPI" bug). Confirmed active at
   runtime: workers print `NUMA-local host buffers enabled (node=auto)`.

2. **Single GuC submission context per device** — the original code submitted H2D and D2H on **two
   separate command queues** (2 GuC contexts/device → up to 32 contexts for 16 GPUs). The fix appends
   both copies into **one** command list on **one** queue, and stops creating the second
   queue/list entirely.

**Effect of the fixes:** severity is clearly reduced — the catastrophic, *unrecoverable* reset storm
seen before these fixes (endless `trying reset from guc_exec_queue_timedout_job`) **no longer occurs**,
and ~5 GPUs now complete. **But the hang still happens**, because:

- Every Level-Zero process still creates a **per-device GPU VM with its own bind queue** *plus* the
  copy queue. So even with the single-queue fix, each GPU still receives multiple GuC
  queue-creation requests at startup.
- All 16 workers initialize **at the same instant** (no stagger in the harness), producing a
  thundering-herd of GuC queue/VM creation and heavy concurrent copy submission across all GPUs.
- Under that load the **GuC firmware itself crashes** — a failure mode below the user-space tool.

---

## 5. Observed Failure — Kernel Evidence

### 5.1 Process state: workers stuck uninterruptible

After the wedge, worker count freezes and stays frozen for 10+ minutes. Only one worker accumulates
CPU time; the rest sit at ~0 CPU in `D` (uninterruptible sleep) state:

```
$ ps -eo pid,etime,stat,args | grep ze_bandwidth
71091  20:23  D   ./ze_bandwidth -g 1 -d 3  -i 500 -s 268435456 -t bidir
71706  19:28  D   ./ze_bandwidth -g 1 -d 4  -i 500 -s 268435456 -t bidir
72625  17:39  D   ./ze_bandwidth -g 1 -d 10 -i 500 -s 268435456 -t bidir
72626  17:39  D   ./ze_bandwidth -g 1 -d 5  -i 500 -s 268435456 -t bidir
72631  17:39  D   ./ze_bandwidth -g 1 -d 9  -i 500 -s 268435456 -t bidir
72642  17:39  D   ./ze_bandwidth -g 1 -d 13 -i 500 -s 268435456 -t bidir
72643  17:39  D   ./ze_bandwidth -g 1 -d 1  -i 500 -s 268435456 -t bidir
72644  17:39  D   ./ze_bandwidth -g 1 -d 8  -i 500 -s 268435456 -t bidir
72645  17:39  D   ./ze_bandwidth -g 1 -d 12 -i 500 -s 268435456 -t bidir
72646  17:39  D   ./ze_bandwidth -g 1 -d 2  -i 500 -s 268435456 -t bidir
```

`D`-state processes cannot be killed (not even with `SIGKILL`) — they are blocked inside an
uninterruptible kernel ioctl. This is why the box ultimately requires a **reboot** to recover.

### 5.2 Kernel stacks: blocked creating a GuC queue

```
$ sudo cat /proc/71091/stack            # worker for GPU 3
[<0>] guc_exec_queue_init+0x21a/0x3c0 [xe]
[<0>] xe_exec_queue_create+0x6e/0x2d0 [xe]
[<0>] xe_exec_queue_create_ioctl+0x2c7/0x7c0 [xe]
[<0>] drm_ioctl_kernel+0xb2/0x110
[<0>] drm_ioctl+0x309/0x5e0
[<0>] xe_drm_ioctl+0xbf/0xe0 [xe]
[<0>] __x64_sys_ioctl+0xa0/0x100
[<0>] do_syscall_64+0x81/0xb50
[<0>] entry_SYSCALL_64_after_hwframe+0x76/0x7e

$ sudo cat /proc/71706/stack            # worker for GPU 4
[<0>] guc_exec_queue_init+0x21a/0x3c0 [xe]
[<0>] xe_exec_queue_create+0x6e/0x2d0 [xe]
[<0>] xe_exec_queue_create_bind+0xdd/0x100 [xe]
[<0>] xe_vm_create+0x836/0xdb0 [xe]
[<0>] xe_vm_create_ioctl+0xb8/0x260 [xe]
[<0>] drm_ioctl_kernel+0xb2/0x110
[<0>] drm_ioctl+0x309/0x5e0
[<0>] xe_drm_ioctl+0xbf/0xe0 [xe]
[<0>] __x64_sys_ioctl+0xa0/0x100
```

Both stacks are stuck in **`guc_exec_queue_init`** — i.e. waiting for the **GuC firmware** to
acknowledge creation of a submission queue (one for the copy queue, one for the VM's bind queue).
The GuC on the target GPU never responds because it has crashed, so the ioctl never returns.

### 5.3 dmesg: GuC crashes across many GPUs

Full sequence of GuC / reset / TLB errors for the run (kernel timestamps in seconds):

```
[1171.829] xe 0000:0e:00.0: *ERROR* TLB invalidation fence timeout, seqno=5216 recv=5215
[1190.445] xe 0000:12:00.0: *ERROR* Tile0: GT0: GuC Crash dump notification
[1190.523] xe 0000:12:00.0: *ERROR* Tile0: GT0: GuC Exception notification
[1190.565] xe 0000:12:00.0:        Tile0: GT0: reset done
[1193.623] xe 0000:5e:00.0: *ERROR* Tile0: GT0: GuC Crash dump notification
[1193.687] xe 0000:5e:00.0: *ERROR* Tile0: GT0: GuC Exception notification
[1193.713] xe 0000:5e:00.0:        Tile0: GT0: reset done
[1196.375] xe 0000:8a:00.0: *ERROR* Tile0: GT0: GuC Crash dump notification
[1196.410] xe 0000:8a:00.0: *ERROR* Tile0: GT0: GuC Exception notification
[1196.430] xe 0000:8a:00.0:        Tile0: GT0: reset done
[1196.451] xe 0000:8a:00.0:        Xe device coredump has been created (card10)
[1247.590] xe 0000:c6:00.0: *ERROR* Tile0: GT0: GuC Crash dump notification
[1247.612] xe 0000:c6:00.0:        Tile0: GT0: reset done                 (card14 coredump)
[1254.554] xe 0000:ae:00.0: *ERROR* Tile0: GT0: GuC Crash dump notification
[1254.603] xe 0000:ae:00.0: *ERROR* TLB invalidation fence timeout, seqno=3374 recv=3373
[1254.619] xe 0000:ae:00.0:        Tile0: GT0: reset done                 (card11 coredump)
[1269.856] xe 0000:0e:00.0: *ERROR* TLB invalidation fence timeout, seqno=5425 recv=5424
[1273.884] xe 0000:47:00.0:        Tile0: GT0: Engine reset: engine_class=bcs, guc_id=94  (card5 coredump)
[1273.896] xe 0000:5e:00.0:        Tile0: GT0: Engine reset: engine_class=bcs, guc_id=92  (card8 coredump)
[1273.907] xe 0000:12:00.0:        Tile0: GT0: Engine reset: engine_class=bcs, guc_id=85
[1273.933] xe 0000:5a:00.0:        Tile0: GT0: Engine reset: engine_class=bcs, guc_id=94  (card7 coredump)
[1769.687] xe 0000:0e:00.0: *ERROR* TLB invalidation fence timeout, seqno=10581 recv=10580
[1779.445] xe 0000:b2:00.0: *ERROR* Tile0: GT0: GuC Crash dump notification
[1781.136] xe 0000:b2:00.0:        Tile0: GT0: reset queued / reset started
[1781.136] xe 0000:b2:00.0: *ERROR* Tile0: GT0: GuC Exception notification
[1781.154] xe 0000:b2:00.0:        Tile0: GT0: reset done                 <-- RECOVERED
[1781.175] xe 0000:b2:00.0:        Xe device coredump has been created (card12)
[1833.498] xe 0000:da:00.0: *ERROR* Tile0: GT0: GuC Crash dump notification
[1833.556] xe 0000:da:00.0:        Tile0: GT0: reset queued / reset started
[1833.557] xe 0000:da:00.0: *ERROR* Tile0: GT0: GuC Exception notification
[1833.561] xe 0000:da:00.0:        Tile0: GT0: trying reset from process_g2h_msg.isra.0.cold
                                    <-- NO "reset done" EVER → GuC permanently dead → HANG
[1912.059] mei_gsc xe.mei-gscfi.3584: timer: connect/disconnect timeout.
[1928.113] xe 0000:0e:00.0: *ERROR* TLB invalidation fence timeout, seqno=10761 recv=10760
[1956.519] xe 0000:5e:00.0: *ERROR* TLB invalidation fence timeout, seqno=7961 recv=7960
```

**Summary of GuC crashes:** 7 distinct GPUs crashed their GuC
(`12,5e,8a,c6,ae,b2,da` at `:00.0`); 6 recovered, **`da:00.0` did not**.

### 5.4 The fatal case: `0000:da:00.0`

The contrast between a **recovered** GPU and the **fatal** GPU is the crux of the hang:

```
# RECOVERED (b2:00.0):
GuC Crash dump → reset queued → reset started → GuC Exception → reset done ✔ → coredump

# FATAL (da:00.0):
GuC Crash dump → reset queued → reset started → GuC Exception → trying reset… (loops, NEVER "reset done")  ✘ HANG
```

`da:00.0` corresponds to one of the in-flight GPU workers. With its GuC dead, that worker's
`guc_exec_queue_init` ioctl blocks forever (Section 5.2), the harness `wait` never returns, and the
whole bidirectional test hangs.

### 5.5 Device coredumps captured

The driver auto-captured GuC coredumps for the crashed GPUs, e.g.:

```
/sys/class/drm/card12/device/devcoredump/data   (b2:00.0)
   failing_device -> …/0000:b2:00.0
/sys/class/drm/card10/device/devcoredump/data   (8a:00.0)
/sys/class/drm/card11/device/devcoredump/data   (ae:00.0)
/sys/class/drm/card14/device/devcoredump/data   (c6:00.0)
/sys/class/drm/card5,7,8/device/devcoredump/data (47, 5a, 5e:00.0)
```

These `devcoredump` blobs are the firmware-level crash dumps and should be collected and handed to
the GPU/GuC firmware team for root-causing the firmware crash itself.

---

## 6. Failure Chain (cause → effect)

1. Harness launches **16 `ze_bandwidth` workers simultaneously**, each driving 500× (256 MiB H2D +
   256 MiB D2H) on a single copy engine.
2. The concurrent startup (per-GPU VM bind queue + copy queue creation) and sustained heavy DMA push
   the **GuC firmware past its stability envelope**.
3. The **GuC crashes** on multiple GPUs → `GuC Crash dump notification` + `GuC Exception
   notification`. Secondary symptoms: `TLB invalidation fence timeout`, copy-engine (`bcs`) resets,
   `mei_gsc` timeout.
4. The `xe` driver attempts a **GT reset** per crashed GPU. Most succeed (`reset done`).
5. On **`da:00.0`** the reset **fails to complete** — the GuC never comes back.
6. The worker targeting `da:00.0` blocks **permanently** in `guc_exec_queue_init` (`D` state,
   unkillable).
7. `runBwTest.sh`'s `wait` on all PIDs never returns → **test hangs**; only a reboot clears the
   `D`-state processes.

---

## 7. Why This Is a Firmware/Driver Issue, Not a Test-Logic Bug

- The same binary running a **single GPU** in bidirectional mode is **100% stable** (24.9 GB/s, no
  crash). The failure requires **concurrent multi-GPU load**.
- The crash signatures (`GuC Crash dump notification`, `GuC Exception notification`, GuC coredumps)
  originate **inside the GuC firmware**, surfaced by the `xe` kernel driver — not in `ze_bandwidth`.
- The user-space fixes (single submission queue, NUMA-local buffers) **measurably reduced** the
  blast radius (no more unrecoverable reset storm; partial completion) but cannot prevent a firmware
  crash that occurs below the Level-Zero API.
- The fatal step — a **GT reset that never completes** on `da:00.0` — is a **driver/firmware reset
  path defect**: a single GPU's failed recovery should not be possible from a well-behaved
  user-space workload, and certainly should not leave processes permanently stuck in `D` state.

---

## 8. Recommended Next Steps

**A. Firmware/driver team (root cause):**
1. Triage the captured `devcoredump` blobs (Section 5.5) to root-cause the **GuC crash** under
   concurrent copy load on Battlemage GuC **70.55.3**.
2. Investigate the **non-completing GT reset** on `da:00.0` (`reset started` → `GuC Exception` →
   no `reset done`) — this is the defect that turns a recoverable crash into a permanent hang.
3. Check for a newer GuC firmware / `xe` driver with fixes for multi-GPU GuC stability and reset
   recovery.

**B. Test harness / tool mitigations (reduce trigger probability — do not fix root cause):**
1. **Stagger worker launch** in `tools/runBwTest.sh` (e.g. a short delay between background starts)
   so 16 GPUs don't hit GuC queue/VM creation in the same instant.
2. Limit each `ze_bandwidth` worker to initialize **only its target GPU** instead of enumerating and
   creating a context across all 16 devices (`allDevicesInit()` → `initCountDevices(0)`).
3. Reduce per-run GuC pressure (smaller `-s` / fewer `-i`) for the parallel bidirectional case.
4. Add a **watchdog/timeout** around the parallel `wait` in the harness so a wedged GuC produces a
   clean FAIL + diagnostic capture instead of an indefinite hang.

**C. Operational:**
- After a hang, the node has unkillable `D`-state processes and at least one dead GuC; a **reboot**
  is currently the only reliable recovery.

---

## 9. Appendix — Quick Reference

| Signature | Meaning |
|---|---|
| `GuC Crash dump notification` | GuC firmware crashed on that GPU |
| `GuC Exception notification` | GuC raised an exception during/after crash |
| `Tile0: GT0: reset done` | GT reset **succeeded** (GPU recovered) |
| `reset started` with **no** `reset done` | GT reset **failed** → GPU permanently wedged |
| `TLB invalidation fence timeout` | GPU-side address-translation stall (secondary symptom) |
| `Engine reset: engine_class=bcs` | Copy (blitter) engine had to be reset |
| Process in `D` state, stack at `guc_exec_queue_init` | Blocked waiting on dead GuC; unkillable |

**Crash tally (this run):** 7 GPUs crashed GuC; 6 recovered, 1 (`0000:da:00.0`) fatal →
10 workers left unkillable, test hung, reboot required.
