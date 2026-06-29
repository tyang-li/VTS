# Copyright (C) 2024-2026 Intel Corporation
import os
import pwd
import subprocess # nosec
from collections import deque
from datetime import datetime

from common.loggerManager import Logger
import atexit
import sys
from pathlib import Path

# tkinter is imported lazily inside main() and GUI functions to avoid
# crashing on headless servers where tkinter is not installed.
# Only the subprocess-based start_gpu_usage_popup() is imported by start_vts.py.
tk = None  # module-level placeholder; set by _import_tk()


def _import_tk():
    """Lazily import tkinter and assign to module-level tk variable."""
    global tk
    if tk is None:
        import tkinter
        tk = tkinter
    return tk

_POPUP_PROC = None


def _get_user_xauthority_path() -> Path:
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user:
        user_home = Path(pwd.getpwnam(sudo_user).pw_dir)
        return Path(os.environ.get('XAUTHORITY', str(user_home / '.Xauthority')))
    return Path(os.environ.get('XAUTHORITY', str(Path.home() / '.Xauthority')))


def _invoker_username() -> str:
    # Prefer the sudo invoker if present
    return os.environ.get('SUDO_USER') or os.environ.get('LOGNAME') or os.environ.get('USER') or 'root'


def _home_for_user(username: str) -> Path:
    try:
        return Path(pwd.getpwnam(username).pw_dir)
    except Exception:
        return Path('/home') / username

def _invoker_xauthority_path() -> Path:
    inv = _invoker_username()
    home = _home_for_user(inv)
    return home / '.Xauthority'


def _parse_display(display: str):
    if not display or ':' not in display:
        return '', ''
    host, rest = display.split(':', 1)
    num = rest.split('.', 1)[0]
    return host, num


def _force_unix_display(env: dict, logger=None) -> None:
    """Force DISPLAY to unix form (:N) when it is localhost:N.0.

    This removes the common TCP(host) vs UNIX cookie mismatch for root GUI.
    """
    disp = env.get('DISPLAY', '')
    if disp.startswith('localhost:'):
        try:
            num = disp.split(':', 1)[1].split('.', 1)[0]
            env['DISPLAY'] = f':{num}'
            if logger:
                logger.info(f"Forcing UNIX DISPLAY={env['DISPLAY']} (was {disp})")
        except Exception:
            pass

def _run_cmd(cmd):
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return True, out.strip()
    except Exception as e:
        return False, str(e)


def x11_environment_self_check(logger=None) -> bool:
    """
    X11 + SSH forwarding diagnostics.
    Returns True if environment is good enough to launch GUI as root.
    """

    ok = True

    def log(level, msg):
        nonlocal ok
        if logger:
            getattr(logger, level)(msg)
        if level == "warning":
            ok = False

    # X11 client utilities (remote host)
    for tool in ("xauth", "xdpyinfo"):
        found, _ = _run_cmd(["which", tool])
        if not found:
            log("warning", f"[FAIL] {tool} not found (install x11-utils / x11-apps)")

    # SSH X11 forwarding for THIS session
    display = os.environ.get("DISPLAY")
    if not display:
        log("warning", "[FAIL] DISPLAY not set (ssh -X / ssh -Y required)")
        return False


    # Xauthority cookie for user
    ok_xauth, out = _run_cmd(["xauth", "list"])
    if not ok_xauth and out:
        log("warning", "[FAIL] No xauth cookies for user (SSH did not install them)")
        return False


    # Root Xauthority validation
    root_xauth = "/root/.Xauthority"
    ok_root_list, out = _run_cmd(["sudo", "xauth", "-f", root_xauth, "list"])

    if not ok_root_list and out:
        log("warning", "[FAIL] Root Xauthority missing cookies (must merge user cookies into /root/.Xauthority)")
        ok = False

    return ok

def _startup_guard_xauthority(logger=None) -> Path:
    """Ensure invoker ~/.Xauthority exists, is writable, and not locked.

    Fixes:
      - missing ~/.Xauthority
      - root-owned ~/.Xauthority (chown back to invoker)
      - wrong perms (0600)
      - stale lock/temp files (~/.Xauthority-*) causing xauth lock timeouts

    Returns the invoker Xauthority path (after repair).
    """
    inv = _invoker_username()
    home = _home_for_user(inv)
    xauth = home / '.Xauthority'

    # Remove stale lock/temp files
    try:
        for tmp in home.glob('.Xauthority-*'):
            try:
                tmp.unlink()
                if logger:
                    logger.info(f"Removed stale Xauthority temp/lock: {tmp}")
            except Exception as e:
                if logger:
                    logger.warning(f"Failed to remove stale Xauthority temp/lock {tmp}: {e}")
    except Exception:
        pass

    try:
        home.mkdir(parents=True, exist_ok=True)
        if not xauth.exists():
            xauth.touch(mode=0o600, exist_ok=True)
            if logger:
                logger.warning(f"Created missing Xauthority file: {xauth}")

        # Fix ownership/perms if we're root
        if os.geteuid() == 0:
            try:
                pw = pwd.getpwnam(inv)
                os.chown(xauth, pw.pw_uid, pw.pw_gid)
            except Exception as e:
                if logger:
                    logger.warning(f"Failed to chown {xauth} to {inv}: {e}")

        try:
            os.chmod(xauth, 0o600)
        except Exception as e:
            if logger:
                logger.warning(f"Failed to chmod 600 {xauth}: {e}")

        try:
            if logger and xauth.stat().st_size == 0:
                logger.warning(f"Xauthority exists but is empty (no cookies): {xauth}")
        except Exception:
            pass

    except Exception as e:
        if logger:
            logger.warning(f"Xauthority startup guard failed: {e}")

    return xauth


def _xauth_list(xauth_path: Path):
    try:
        out = subprocess.check_output(['xauth', '-f', str(xauth_path), 'list'], text=True, stderr=subprocess.STDOUT)
        return [ln.strip() for ln in out.splitlines() if ln.strip()]
    except Exception:
        return []

def ensure_root_xauthority_for_display(logger=None) -> bool:
    """Ensure /root/.Xauthority contains a cookie that matches the active DISPLAY.

    Behavior:
      1) repairs invoker ~/.Xauthority (missing/root-owned/lock files)
      2) tries to extract cookie from invoker xauth for DISPLAY candidates
      3) merges extracted cookie into /root/.Xauthority

    Notes:
      - This fixes the common failure where `xauth extract` output is discarded and
        `xauth merge` runs with empty stdin.
      - If the current SSH session has no cookie (because ssh login couldn't write
        ~/.Xauthority), this function cannot fabricate one; you must re-login with -Y.
    """
    display = os.environ.get("DISPLAY", "")
    if not display:
        if logger:
            logger.info("DISPLAY not set; popup will not be shown.")
        return False

    # Always use the invoker Xauthority that the startup guard repairs/creates.
    # This prevents root-owned/missing/locked ~/.Xauthority from breaking SSH X11 auth.
    inv_xauth = _startup_guard_xauthority(logger=logger)
    root_xauth = Path("/root/.Xauthority")

    # Build DISPLAY candidates: current, unix, localhost
    host, num = _parse_display(display)
    candidates = [display]
    if num:
        unix_disp = f":{num}"
        tcp_disp = f"localhost:{num}.0"
        for d in (unix_disp, tcp_disp):
            if d not in candidates:
                candidates.append(d)

    extracted = None
    last_err = None

    for d in candidates:
        try:
            extracted = subprocess.check_output(
                ["xauth", "-f", str(inv_xauth), "extract", "-", d],
                stderr=subprocess.STDOUT,
            )
            if extracted:
                if logger:
                    logger.info(f"xauth extract succeeded for DISPLAY={d}")
                break
        except Exception as e:
            last_err = e
            if logger:
                logger.warning(f"xauth extract failed for DISPLAY={d}: {e}")

    if not extracted:
        if logger:
            logger.warning(
                "No extractable cookie found in invoker .Xauthority for this DISPLAY. "
                "The file may have been repaired, but SSH installs cookies at login time. "
                "Logout and re-login with X forwarding (ssh -Y). "
                f"invoker_xauth={inv_xauth} display={display} last_error={last_err}"
            )
        return False

    try:
        root_xauth.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["xauth", "-f", str(root_xauth), "merge", "-"],
            input=extracted,          # <-- CRITICAL: pipe extract -> merge
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except Exception as e:
        if logger:
            logger.warning(f"Failed to merge cookie into {root_xauth}: {e}")
        return False

    # Verify root has a cookie for this display number (best-effort)
    if num:
        rlines = _xauth_list(root_xauth)
        have = any((f"/unix:{num}" in ln) or ln.split()[0].endswith(f":{num}") for ln in rlines)
        if not have and logger:
            logger.warning(
                f"Merged root Xauthority but could not verify cookie for display {num} in {root_xauth}"
            )

    if logger:
        logger.info(f"Root Xauthority populated successfully for DISPLAY={display}")

    return True

def start_gpu_usage_popup(logger):
    global _POPUP_PROC

    if _POPUP_PROC is not None and _POPUP_PROC.poll() is None:
        return _POPUP_PROC

       
    if not x11_environment_self_check(logger):
        logger.warning("X11 environment check failed; GPU usage popup disabled.")
        return None
    
    env = os.environ.copy()
    if not env.get('DISPLAY'):
        logger.info('No DISPLAY; skipping GPU usage popup.')
        return None

    # Force unix DISPLAY for the popup process to avoid localhost vs unix mismatches
    _force_unix_display(env, logger=logger)

    # Ensure root has the cookie for the (possibly original) session display
    if not ensure_root_xauthority_for_display(logger):
        return None

    env['XAUTHORITY'] = '/root/.Xauthority'

    project_root = Path(__file__).resolve().parents[1]
    logs_dir = project_root / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)

    try:
        _POPUP_PROC = subprocess.Popen(
            [sys.executable, '-m', 'tools.gpu_usage_popup'],
            cwd=str(project_root),
            env=env,
            stdout=subprocess.DEVNULL,
        )
        atexit.register(stop_popup, _POPUP_PROC)
        logger.info(f'Started GPU usage popup pid={_POPUP_PROC.pid}')
        return _POPUP_PROC
    except Exception as e:
        logger.warning(f'Could not start popup: {e}')
        return None


def stop_popup(proc: subprocess.Popen, timeout=3):
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

# ---- Settings ----
BAR_WIDTH = 10
UPDATE_MS = 700
HISTORY_LEN = 1000
RUNNING = True

def _ensure_logs_dir_owned_by_invoker(logs_dir: str, logger=None):
    """Ensure logs directory and files are owned by the invoking (non-root) user when possible.

    If this script is run as root via sudo, and SUDO_USER is set, chown logs_dir to SUDO_USER.
    This helps keep logs owned by the 'current user' (the invoker) rather than root.
    """
    try:
        sudo_user = os.environ.get("SUDO_USER")
        if not sudo_user:
            return
        if os.geteuid() != 0:
            return

        pw = pwd.getpwnam(sudo_user)
        uid, gid = pw.pw_uid, pw.pw_gid

        # Change ownership of logs dir (and existing files inside) to the invoker
        os.chown(logs_dir, uid, gid)
        for name in os.listdir(logs_dir):
            p = os.path.join(logs_dir, name)
            try:
                os.chown(p, uid, gid)
            except Exception:
                pass

        if logger:
            logger.info(f"Adjusted ownership of logs directory to {sudo_user}: {logs_dir}")
    except Exception as e:
        if logger:
            logger.warning(f"Could not adjust logs ownership: {e}")


def _init_popup_logger():
    """Create a dedicated popup log file using the project's Logger.

    Requirement:
      logger = Logger(log_file=os.path.join(os.getcwd(), "logs", f"GPU_usage_popup_YYYYMMDD_HHMMSS.log"))

    Also ensures the logs directory exists. Ownership is best-effort set to the invoking user.
    """
    logs_dir = os.path.join(os.getcwd(), "logs")
    os.makedirs(logs_dir, exist_ok=True)

    log_file = os.path.join(logs_dir, f"GPU_usage_popup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    logger = Logger(log_file=log_file)

    # If started via sudo, try to chown the logs directory and file to the invoker
    _ensure_logs_dir_owned_by_invoker(logs_dir, logger=logger)
    try:
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user and os.geteuid() == 0:
            pw = pwd.getpwnam(sudo_user)
            os.chown(log_file, pw.pw_uid, pw.pw_gid)
    except Exception:
        pass

    logger.info(f"GPU usage popup logger initialized: {log_file}")
    return logger


# ---------------- Gradient palette (green → yellow → red) ----------------

def _lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def _lerp_rgb(c1, c2, t: float) -> str:
    r = _lerp(c1[0], c2[0], t)
    g = _lerp(c1[1], c2[1], t)
    b = _lerp(c1[2], c2[2], t)
    return f"#{r:02x}{g:02x}{b:02x}"


def build_gy_r_palette(n: int):
    green = (0, 200, 0)
    yellow = (255, 200, 0)
    red = (220, 0, 0)
    if n <= 1:
        return [_lerp_rgb(green, red, 1.0)]
    palette = []
    for i in range(n):
        f = i / (n - 1)
        if f <= 0.5:
            palette.append(_lerp_rgb(green, yellow, f / 0.5))
        else:
            palette.append(_lerp_rgb(yellow, red, (f - 0.5) / 0.5))
    return palette


GRADIENT = build_gy_r_palette(BAR_WIDTH)
EMPTY_COLOR = "#9a9a9a"


def render_into(text_widget, value: float, *, max_value: int = 100, mirror: bool = False, unit_suffix: str = "%"):
    _import_tk()
    try:
        value_int = int(round(float(value)))
    except Exception:
        value_int = 0

    value_int = max(0, min(max_value, value_int))
    filled_total = (value_int * BAR_WIDTH) // max_value

    text_widget.config(state="normal")
    text_widget.delete("1.0", tk.END)
    text_widget.insert(tk.END, f"{value_int:3d}{unit_suffix} ")

    for display_pos in range(BAR_WIDTH):
        if not mirror:
            is_filled = display_pos < filled_total
            grad_index = display_pos
        else:
            progress_pos = (BAR_WIDTH - 1 - display_pos)
            is_filled = progress_pos < filled_total
            grad_index = progress_pos

        if is_filled:
            text_widget.insert(tk.END, "█", f"grad{grad_index}")
        else:
            text_widget.insert(tk.END, "░", "empty")

    text_widget.config(state="disabled")


def make_bar(parent):
    _import_tk()
    tw = tk.Text(
        parent,
        height=1,
        width=6 + BAR_WIDTH,
        font=("Courier New", 10),
        borderwidth=0,
        highlightthickness=0,
        padx=0,
        pady=0,
    )
    tw.tag_configure("empty", foreground=EMPTY_COLOR)
    for i, col in enumerate(GRADIENT):
        tw.tag_configure(f"grad{i}", foreground=col)
    tw.config(state="disabled")
    return tw


# ---------------- xpu-smi helpers ----------------

def get_device_to_bdf(logger, timeout=60):
    try:
        cmd = ["xpu-smi", "discovery", "--dump", "1,11"]
        if os.geteuid() != 0:
            cmd.insert(0, "sudo")
        disc = subprocess.check_output(
            cmd,
            text=True,
            timeout=timeout,
            stderr=subprocess.STDOUT,
        )
        device_to_bdf = {}
        for line in disc.splitlines()[1:]:
            line = line.strip()
            if not line or line.startswith("Device ID"):
                continue
            parts = line.split(",", 1)
            if len(parts) < 2:
                continue
            device_id = parts[0].strip()
            bdf = parts[1].replace('"', "").strip().replace("0000:", "")
            device_to_bdf[device_id] = bdf

        logger.info(f"Discovered {len(device_to_bdf)} GPU(s) via xpu-smi discovery.")
        return device_to_bdf
    except Exception as e:
        logger.warning(f"get_device_to_bdf failed: {e}")
        return {}


def _parse_csv_like(raw_output: str):
    device_data = {}
    for line in raw_output.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("timestamp"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            continue
        ts, device_id, usage_s, temp_s = parts
        try:
            usage = int(float(usage_s))
            temp = float(temp_s)
        except Exception:
            continue
        device_data[device_id] = {"timestamp": ts, "usage": usage, "temperature": temp}
    return device_data


def xpu_util_by_bdf(logger, device_to_bdf=None, timeout=20):
    device_to_bdf = device_to_bdf or {}
    try:
        raw_output = subprocess.check_output(
            ["sudo", "xpu-smi", "dump", "-m", "0,1", "-n", "1"],
            text=True,
            timeout=timeout,
            stderr=subprocess.STDOUT,
        )
        by_device = _parse_csv_like(raw_output)
        if not by_device:
            # Log a snippet for debugging (no stdout)
            snippet = "\n".join(raw_output.splitlines()[:20])
            logger.warning(f"xpu-smi output could not be parsed as CSV. First lines:\n{snippet}")
            return {}

        data = {}
        for device_id, metrics in by_device.items():
            key = device_to_bdf.get(device_id, device_id)
            data[key] = metrics
        logger.info(f"{data}")
        return data

    except Exception as e:
        logger.warning(f"xpu-smi dump failed: {e}")
        return {}


# ---------------- Live graph helpers ----------------

def init_live_graph(parent_frame, gpu_keys):
    _import_tk()
    try:
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure
    except ImportError as e:
        raise RuntimeError(f"matplotlib not available for GPU usage popup: {e}") from e
    
    fig = Figure(figsize=(6.0, 2.6), dpi=100)
    ax_usage = fig.add_subplot(211)
    ax_temp = fig.add_subplot(212, sharex=ax_usage)
    fig.subplots_adjust(hspace=0.45)

    ax_usage.set_title("Live GPU Usage (%)")
    ax_usage.set_ylabel("Usage (%)")
    ax_usage.set_ylim(0, 100)
    ax_usage.grid(True, alpha=0.3)

    ax_temp.set_title("Live GPU Temperature (°C)")
    ax_temp.set_ylabel("Temperature (°C)")
    ax_temp.set_ylim(0, 300)
    ax_temp.grid(True, alpha=0.3)

    canvas = FigureCanvasTkAgg(fig, master=parent_frame)
    canvas.draw()
    canvas.get_tk_widget().pack(fill="both", expand=True)

    x_hist = deque(maxlen=HISTORY_LEN)
    usage_hist = {k: deque(maxlen=HISTORY_LEN) for k in gpu_keys}
    temp_hist = {k: deque(maxlen=HISTORY_LEN) for k in gpu_keys}

    usage_lines = {}
    temp_lines = {}

    for k in gpu_keys:
        (u_line,) = ax_usage.plot([], [], label=str(k))
        (t_line,) = ax_temp.plot([], [], label=str(k))
        usage_lines[k] = u_line
        temp_lines[k] = t_line

    if gpu_keys:
        ax_usage.legend(loc="upper left", fontsize=7, ncol=3)
        ax_temp.legend(loc="upper left", fontsize=7, ncol=3)

    return fig, canvas, ax_usage, ax_temp, x_hist, usage_hist, temp_hist, usage_lines, temp_lines


def main() -> int:
    global RUNNING

    logger = _init_popup_logger()

    try:
        _import_tk()
    except ImportError:
        logger.warning("tkinter is not installed; GPU usage popup cannot run.")
        return 1

    try:
        if not os.environ.get("DISPLAY"):
            logger.info("No DISPLAY found; cannot show popup. Exiting.")
            return 0

        device_to_bdf = get_device_to_bdf(logger)
        gpu_keys = list(device_to_bdf.values()) if device_to_bdf else []

        if not gpu_keys:
            first = xpu_util_by_bdf(logger, device_to_bdf={}, timeout=20)
            gpu_keys = list(first.keys()) or ["0"]

        root = tk.Tk()
        root.title("GPU Usage")
        root.geometry("720x530")

        bars = {}
        sample_idx = 0


        def on_close():
            global RUNNING
            RUNNING = False
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", on_close)

        container = tk.Frame(root)
        container.pack(padx=12, pady=8, fill="x")

        for gpu_key in gpu_keys:
            row = tk.Frame(container)
            row.pack(fill="x", pady=4)
            row.grid_columnconfigure(0, weight=1)
            row.grid_columnconfigure(1, weight=0)
            row.grid_columnconfigure(2, weight=1)

            left_tw = make_bar(row)
            left_tw.grid(row=0, column=0, sticky="e")
            tk.Label(row, text=f"GPU {gpu_key}", width=12, anchor="center").grid(row=0, column=1, padx=2)
            right_tw = make_bar(row)
            right_tw.grid(row=0, column=2, sticky="w")
            bars[gpu_key] = (left_tw, right_tw)
        
       
        # --- Bottom labels under the bar area ---
        bottom = tk.Frame(container)
        bottom.pack(fill="x", pady=(2, 0))

        # Match the same 3-column layout used in each GPU row
        bottom.grid_columnconfigure(0, weight=1)
        bottom.grid_columnconfigure(1, weight=0)
        bottom.grid_columnconfigure(2, weight=1)

        tk.Label(bottom, text="Temperature", font=("Segoe UI", 9), fg="gray30").grid(row=0, column=0, sticky="e", padx=(0, 100))
        tk.Label(bottom, text="").grid(row=0, column=1)
        tk.Label(bottom, text="Usage", font=("Segoe UI", 9), fg="gray30").grid(row=0, column=2, sticky="w", padx=(100, 0)
)
        graph_frame = tk.Frame(root)
        graph_frame.pack(padx=12, pady=(0, 12), fill="both", expand=True)

        try:
            fig, canvas, ax_usage, ax_temp, x_hist, usage_hist, temp_hist, usage_lines, temp_lines = init_live_graph(graph_frame, gpu_keys)
        except RuntimeError as e:
            logger.error(f"Failed to initialize live graph: {e}")
            logger.info("GPU usage popup will run without live graph functionality")
            # Set up dummy variables to prevent errors in update functions
            fig = canvas = ax_usage = ax_temp = None
            x_hist = deque(maxlen=HISTORY_LEN)
            usage_hist = {k: deque(maxlen=HISTORY_LEN) for k in gpu_keys}
            temp_hist = {k: deque(maxlen=HISTORY_LEN) for k in gpu_keys}
            usage_lines = {}
            temp_lines = {}

        def update_live_graph():
            if canvas is None:  # matplotlib not available
                return
            x_list = list(x_hist)
            for k in usage_lines:
                usage_lines[k].set_data(x_list, list(usage_hist[k]))
                temp_lines[k].set_data(x_list, list(temp_hist[k]))
            if len(x_list) >= 2:
                ax_usage.set_xlim(x_list[0], x_list[-1])
            #canvas.draw_idle()
            canvas.draw()

        def update_all():
            nonlocal sample_idx
            if not RUNNING:
                return

            gpu_data = xpu_util_by_bdf(logger, device_to_bdf=device_to_bdf, timeout=20)
            #status.set("Telemetry OK" if gpu_data else "No telemetry parsed (check popup log)")

            for key, (left_tw, right_tw) in bars.items():
                usage = gpu_data.get(key, {}).get("usage", 0)
                temp = gpu_data.get(key, {}).get("temperature", 0)
                render_into(left_tw, temp, max_value=300, mirror=True, unit_suffix="°C")
                render_into(right_tw, usage, max_value=100, mirror=False, unit_suffix="%")

            x_hist.append(sample_idx)
            for key in usage_hist:
                usage_hist[key].append(gpu_data.get(key, {}).get("usage", 0))
                temp_hist[key].append(gpu_data.get(key, {}).get("temperature", 0))

            sample_idx += 1
            update_live_graph()
            root.after(UPDATE_MS, update_all)

        #update_all()
        root.after(200, update_all)
        root.mainloop()
        return 0

    except Exception as e:
        try:
            logger.warning(f"Unhandled exception in popup: {e}")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
