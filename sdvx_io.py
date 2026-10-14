"""
sdvx_io.py — Python ctypes wrapper for bemanitools sdvxio.dll.

Directly manages Sound Voltex Valkyrie/AIO hardware without needing ViGEm.
Provides button inputs, rotary knob deltas, and button LED control.
"""

import os
import sys
import ctypes
from ctypes import (
    c_bool, c_uint8, c_uint16, c_uint32, c_void_p, c_char_p, c_int,
    CFUNCTYPE, POINTER
)

# ── GPIO Bit Definitions ───────────────────────────────────────────────────

# System inputs (sdvx_io_get_input_gpio_sys)
SDVX_IO_IN_GPIO_SYS_COIN    = 2  # 0x04
SDVX_IO_IN_GPIO_SYS_SERVICE = 4  # 0x10
SDVX_IO_IN_GPIO_SYS_TEST    = 5  # 0x20

# Gameplay GPIO Bank 0 (sdvx_io_get_input_gpio(0))
SDVX_IO_IN_GPIO_0_C         = 0  # 0x0001 (BT-C)
SDVX_IO_IN_GPIO_0_B         = 1  # 0x0002 (BT-B)
SDVX_IO_IN_GPIO_0_A         = 2  # 0x0004 (BT-A)
SDVX_IO_IN_GPIO_0_START     = 3  # 0x0008 (START)
SDVX_IO_IN_GPIO_0_RECORDER  = 4  # 0x0010 (Valkyrie sub-screen / REC)
SDVX_IO_IN_GPIO_0_HEADPHONE = 5  # 0x0020 (Headphone sense)

# Gameplay GPIO Bank 1 (sdvx_io_get_input_gpio(1))
SDVX_IO_IN_GPIO_1_FX_R      = 3  # 0x0008 (FX-R)
SDVX_IO_IN_GPIO_1_FX_L      = 4  # 0x0010 (FX-L)
SDVX_IO_IN_GPIO_1_D         = 5  # 0x0020 (BT-D)

# Output GPIO Lights (sdvx_io_set_gpio_lights)
SDVX_IO_OUT_GPIO_D           = 0   # 0x00000001
SDVX_IO_OUT_GPIO_FX_L        = 1   # 0x00000002
SDVX_IO_OUT_GPIO_FX_R        = 2   # 0x00000004
SDVX_IO_OUT_GPIO_GENERATOR_B = 3   # 0x00000008
SDVX_IO_OUT_GPIO_START       = 12  # 0x00001000
SDVX_IO_OUT_GPIO_A           = 13  # 0x00002000
SDVX_IO_OUT_GPIO_B           = 14  # 0x00004000
SDVX_IO_OUT_GPIO_C           = 15  # 0x00008000


# ── Win32 / ctypes Callback Definitions ─────────────────────────────────────

THREAD_PROC = CFUNCTYPE(c_int, c_void_p)
THREAD_CREATE_CB = CFUNCTYPE(c_int, THREAD_PROC, c_void_p, c_uint32, c_uint32)
THREAD_JOIN_CB   = CFUNCTYPE(None, c_int, POINTER(c_int))
THREAD_DESTROY_CB = CFUNCTYPE(None, c_int)
LOGGER_CB = CFUNCTYPE(None, c_char_p, c_char_p)

# Win32 Kernel32 imports for thread management shims
kernel32 = ctypes.windll.kernel32
CreateThread = kernel32.CreateThread
WaitForSingleObject = kernel32.WaitForSingleObject
CloseHandle = kernel32.CloseHandle
GetExitCodeThread = kernel32.GetExitCodeThread
INFINITE = 0xFFFFFFFF


def _win32_thread_create(proc, ctx, stack_sz, priority):
    thread_handle = CreateThread(None, stack_sz, proc, ctx, 0, None)
    return thread_handle


def _win32_thread_join(thread_id, result_ptr):
    WaitForSingleObject(thread_id, INFINITE)
    if result_ptr:
        exit_code = c_uint32()
        GetExitCodeThread(thread_id, ctypes.byref(exit_code))
        result_ptr.contents.value = exit_code.value


def _win32_thread_destroy(thread_id):
    CloseHandle(thread_id)


def _null_logger(module, fmt, *args):
    pass


# ── SDVXIO Class ────────────────────────────────────────────────────────────

class SDVXIO:
    """Manages direct hardware interaction via bemanitools sdvxio.dll."""

    def __init__(self, dll_path: str):
        self.dll_path = os.path.abspath(dll_path)
        self.dll_dir = os.path.dirname(self.dll_path)
        self.dll = None
        self._initialized = False

        # Retain callback objects to prevent garbage collection
        self._cb_refs = []

        # Previous knob positions (10-bit: 0..1023)
        self.last_vol = [0, 0]

        # Previous button state for edge detection
        self.prev_buttons = {
            "START": False,
            "A": False,
            "B": False,
            "C": False,
            "D": False,
            "FX_L": False,
            "FX_R": False,
            "COIN": False,
            "SERVICE": False,
            "TEST": False,
        }

    def init(self) -> bool:
        """Load the DLL and initialize the IO board."""
        if not os.path.isfile(self.dll_path):
            print(f"[sdvxio] DLL not found at: {self.dll_path}")
            return False

        try:
            # Ensure DLL dependencies (libaio.dll, libaio-iob.dll, etc.) are resolvable
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(self.dll_dir)
            os.environ["PATH"] = self.dll_dir + os.pathsep + os.environ.get("PATH", "")

            self.dll = ctypes.CDLL(self.dll_path)
        except Exception as e:
            print(f"[sdvxio] Failed to load DLL: {e}")
            return False

        # Bind function signatures
        self.dll.sdvx_io_set_loggers.argtypes = [LOGGER_CB, LOGGER_CB, LOGGER_CB, LOGGER_CB]
        self.dll.sdvx_io_set_loggers.restype = None

        self.dll.sdvx_io_init.argtypes = [THREAD_CREATE_CB, THREAD_JOIN_CB, THREAD_DESTROY_CB]
        self.dll.sdvx_io_init.restype = c_bool

        self.dll.sdvx_io_fini.argtypes = []
        self.dll.sdvx_io_fini.restype = None

        self.dll.sdvx_io_read_input.argtypes = []
        self.dll.sdvx_io_read_input.restype = c_bool

        self.dll.sdvx_io_get_input_gpio_sys.argtypes = []
        self.dll.sdvx_io_get_input_gpio_sys.restype = c_uint8

        self.dll.sdvx_io_get_input_gpio.argtypes = [c_uint8]
        self.dll.sdvx_io_get_input_gpio.restype = c_uint16

        self.dll.sdvx_io_get_spinner_pos.argtypes = [c_uint8]
        self.dll.sdvx_io_get_spinner_pos.restype = c_uint16

        self.dll.sdvx_io_set_gpio_lights.argtypes = [c_uint32]
        self.dll.sdvx_io_set_gpio_lights.restype = None

        self.dll.sdvx_io_set_pwm_light.argtypes = [c_uint8, c_uint8]
        self.dll.sdvx_io_set_pwm_light.restype = None

        self.dll.sdvx_io_write_output.argtypes = []
        self.dll.sdvx_io_write_output.restype = c_bool

        # Prepare callbacks
        t_create = THREAD_CREATE_CB(_win32_thread_create)
        t_join = THREAD_JOIN_CB(_win32_thread_join)
        t_destroy = THREAD_DESTROY_CB(_win32_thread_destroy)
        log_cb = LOGGER_CB(_null_logger)
        self._cb_refs.extend([t_create, t_join, t_destroy, log_cb])

        self.dll.sdvx_io_set_loggers(log_cb, log_cb, log_cb, log_cb)

        try:
            ok = self.dll.sdvx_io_init(t_create, t_join, t_destroy)
        except Exception as e:
            print(f"[sdvxio] Exception during sdvx_io_init: {e}")
            return False

        if not ok:
            print("[sdvxio] sdvx_io_init returned False (device not found or not connected)")
            return False

        self._initialized = True
        print(f"[sdvxio] Successfully initialized SDVX IO from {self.dll_path}")

        # Initialize knob positions
        self.dll.sdvx_io_read_input()
        self.last_vol[0] = self.dll.sdvx_io_get_spinner_pos(0)
        self.last_vol[1] = self.dll.sdvx_io_get_spinner_pos(1)

        return True

    def poll(self) -> dict | None:
        """Poll the IO board and return current button states, edge triggers, and knob deltas."""
        if not self._initialized:
            return None

        if not self.dll.sdvx_io_read_input():
            return None

        sys_b = self.dll.sdvx_io_get_input_gpio_sys()
        gpio0 = self.dll.sdvx_io_get_input_gpio(0)
        gpio1 = self.dll.sdvx_io_get_input_gpio(1)

        vol0 = self.dll.sdvx_io_get_spinner_pos(0)
        vol1 = self.dll.sdvx_io_get_spinner_pos(1)

        delta0 = self._get_wrapped_delta(vol0, self.last_vol[0])
        delta1 = self._get_wrapped_delta(vol1, self.last_vol[1])

        self.last_vol[0] = vol0
        self.last_vol[1] = vol1

        current_buttons = {
            "START":   bool(gpio0 & (1 << SDVX_IO_IN_GPIO_0_START)),
            "A":       bool(gpio0 & (1 << SDVX_IO_IN_GPIO_0_A)),
            "B":       bool(gpio0 & (1 << SDVX_IO_IN_GPIO_0_B)),
            "C":       bool(gpio0 & (1 << SDVX_IO_IN_GPIO_0_C)),
            "D":       bool(gpio1 & (1 << SDVX_IO_IN_GPIO_1_D)),
            "FX_L":    bool(gpio1 & (1 << SDVX_IO_IN_GPIO_1_FX_L)),
            "FX_R":    bool(gpio1 & (1 << SDVX_IO_IN_GPIO_1_FX_R)),
            "COIN":    bool(sys_b & (1 << SDVX_IO_IN_GPIO_SYS_COIN)),
            "SERVICE": bool(sys_b & (1 << SDVX_IO_IN_GPIO_SYS_SERVICE)),
            "TEST":    bool(sys_b & (1 << SDVX_IO_IN_GPIO_SYS_TEST)),
        }

        # Just-pressed edges
        just_pressed = {}
        for k, v in current_buttons.items():
            just_pressed[k] = v and not self.prev_buttons.get(k, False)

        self.prev_buttons = current_buttons

        return {
            "held": current_buttons,
            "pressed": just_pressed,
            "vol_l_delta": delta0,
            "vol_r_delta": delta1,
            "vol_l_pos": vol0,
            "vol_r_pos": vol1,
        }

    def set_button_lights(self, a=False, b=False, c=False, d=False, fx_l=False, fx_r=False, start=False, commit=True):
        """Set button lamps."""
        if not self._initialized:
            return

        mask = 0
        if d:     mask |= (1 << SDVX_IO_OUT_GPIO_D)
        if fx_l:  mask |= (1 << SDVX_IO_OUT_GPIO_FX_L)
        if fx_r:  mask |= (1 << SDVX_IO_OUT_GPIO_FX_R)
        if start: mask |= (1 << SDVX_IO_OUT_GPIO_START)
        if a:     mask |= (1 << SDVX_IO_OUT_GPIO_A)
        if b:     mask |= (1 << SDVX_IO_OUT_GPIO_B)
        if c:     mask |= (1 << SDVX_IO_OUT_GPIO_C)

        self.dll.sdvx_io_set_gpio_lights(mask)
        if commit:
            self.dll.sdvx_io_write_output()

    def set_wing_lights(
        self,
        upper_left: tuple[int, int, int] = (0, 0, 0),
        upper_right: tuple[int, int, int] = (0, 0, 0),
        lower_left: tuple[int, int, int] = (0, 0, 0),
        lower_right: tuple[int, int, int] = (0, 0, 0),
        woofer: tuple[int, int, int] = (0, 0, 0),
        controller: tuple[int, int, int] = (0, 0, 0),
        brightness: float = 1.0,
        commit: bool = True,
    ):
        """
        Set PWM wing and cabinet tape LEDs matching background image colors.
        Channels (from sdvxio-valk):
          0..2:   Upper Left Wing RGB
          3..5:   Upper Right Wing RGB
          6..8:   Lower Left Wing RGB
          9..11:  Lower Right Wing RGB
          12..14: Woofer RGB
          15..17: Controller Deck RGB
        """
        if not self._initialized:
            return

        b = max(0.0, min(1.0, brightness))

        def _scale(rgb):
            return (
                int(max(0, min(255, rgb[0] * b))),
                int(max(0, min(255, rgb[1] * b))),
                int(max(0, min(255, rgb[2] * b))),
            )

        ul = _scale(upper_left)
        ur = _scale(upper_right)
        ll = _scale(lower_left)
        lr = _scale(lower_right)
        wf = _scale(woofer)
        ct = _scale(controller)

        # Upper Left Wing (0..2)
        self.dll.sdvx_io_set_pwm_light(0, ul[0])
        self.dll.sdvx_io_set_pwm_light(1, ul[1])
        self.dll.sdvx_io_set_pwm_light(2, ul[2])

        # Upper Right Wing (3..5)
        self.dll.sdvx_io_set_pwm_light(3, ur[0])
        self.dll.sdvx_io_set_pwm_light(4, ur[1])
        self.dll.sdvx_io_set_pwm_light(5, ur[2])

        # Lower Left Wing (6..8)
        self.dll.sdvx_io_set_pwm_light(6, ll[0])
        self.dll.sdvx_io_set_pwm_light(7, ll[1])
        self.dll.sdvx_io_set_pwm_light(8, ll[2])

        # Lower Right Wing (9..11)
        self.dll.sdvx_io_set_pwm_light(9, lr[0])
        self.dll.sdvx_io_set_pwm_light(10, lr[1])
        self.dll.sdvx_io_set_pwm_light(11, lr[2])

        # Woofer (12..14)
        self.dll.sdvx_io_set_pwm_light(12, wf[0])
        self.dll.sdvx_io_set_pwm_light(13, wf[1])
        self.dll.sdvx_io_set_pwm_light(14, wf[2])

        # Controller (15..17)
        self.dll.sdvx_io_set_pwm_light(15, ct[0])
        self.dll.sdvx_io_set_pwm_light(16, ct[1])
        self.dll.sdvx_io_set_pwm_light(17, ct[2])

        if commit:
            self.dll.sdvx_io_write_output()

    def close(self):
        """Turn off lights and shut down IO emulation cleanly."""
        if not self._initialized:
            return

        try:
            # Turn off all button lamps and PWM tape LEDs
            self.dll.sdvx_io_set_gpio_lights(0)
            for ch in range(23):
                self.dll.sdvx_io_set_pwm_light(ch, 0)
            self.dll.sdvx_io_write_output()
        except Exception:
            pass

        try:
            self.dll.sdvx_io_fini()
            print("[sdvxio] Shut down cleanly.")
        except Exception as e:
            print(f"[sdvxio] Error during sdvx_io_fini: {e}")

        self._initialized = False

    @staticmethod
    def _get_wrapped_delta(curr: int, prev: int, bound: int = 1024) -> int:
        half = bound // 2
        delta = curr - prev
        if delta > half:
            delta -= bound
        elif delta < -half:
            delta += bound
        return delta
