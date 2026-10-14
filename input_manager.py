"""
input_manager.py — Unified controller + keyboard + SDVX IO input with configurable keybinds.

Supports:
  • Direct Valkyrie/AIO hardware via bemanitools sdvxio.dll (no ViGEm required).
  • Xbox 360 controller buttons, axes (with deadzone), and hats (D-pad).
  • Keyboard arrows / Enter / Escape fallback.
  • Button reactive lighting and attract lighting for arcade cabinets.
"""

import os
import time
import pygame

from config import base_dir
from sdvx_io import SDVXIO


# Keyboard fallback mapping: action → pygame key constant
_KB_MAP = {
    "navigate_up":    pygame.K_UP,
    "navigate_down":  pygame.K_DOWN,
    "navigate_left":  pygame.K_LEFT,
    "navigate_right": pygame.K_RIGHT,
    "confirm":        pygame.K_RETURN,
    "back":           pygame.K_ESCAPE,
    "launch_script_up":   pygame.K_UP,
    "launch_script_down": pygame.K_DOWN,
}


class InputManager:
    """
    Polls pygame events, joystick, keyboard, and SDVX IO hardware.
    Exposes high-level logical actions.
    """

    def __init__(self, settings: dict):
        keybinds = settings.get("keybinds", {})
        input_cfg = settings.get("input", {})
        sdvxio_cfg = settings.get("sdvxio", {})

        self._bindings: dict[str, dict] = {}
        for action, bind in keybinds.items():
            self._bindings[action] = dict(bind)

        self._cooldown_ms = input_cfg.get("navigate_cooldown_ms", 250)
        self._deadzone = input_cfg.get("axis_deadzone", 0.5)

        # Timestamps of last trigger per action (for debounce)
        self._last_trigger: dict[str, float] = {}

        # Joystick reference
        self._joystick: pygame.joystick.JoystickType | None = None
        self._joystick_id: int | None = None
        pygame.joystick.init()
        self._init_first_joystick()

        # Track events this frame
        self._button_downs: set[int] = set()
        self._hat_values: dict[int, tuple[int, int]] = {}
        self._key_downs: set[int] = set()

        # ── SDVX IO initialization ──────────────────────────────────────────
        self.sdvx_io: SDVXIO | None = None
        self._knob_sensitivity = sdvxio_cfg.get("knob_sensitivity", 15)
        self._key_lights = sdvxio_cfg.get("key_lights", True)
        self._wing_lights = sdvxio_cfg.get("wing_lights", True)
        self._wing_brightness = float(sdvxio_cfg.get("wing_brightness", 1.0))
        self._start_lamp_attract = sdvxio_cfg.get("start_lamp_attract", True)
        self._knob_accum_l = 0
        self._knob_accum_r = 0
        self._last_vol_l_time = 0.0
        self._last_vol_r_time = 0.0

        self._sdvx_btn_map = sdvxio_cfg.get("buttons", {
            "START": "confirm",
            "FX_L": "back",
            "FX_R": "back",
            "A": "navigate_left",
            "B": "navigate_left",
            "C": "navigate_right",
            "D": "navigate_right",
        })

        if sdvxio_cfg.get("enabled", True):
            rel_path = sdvxio_cfg.get("dll_path", os.path.join("IO", "sdvxio.dll"))
            dll_full_path = os.path.abspath(
                rel_path if os.path.isabs(rel_path) else os.path.join(base_dir(), rel_path)
            )

            if os.path.isfile(dll_full_path):
                io = SDVXIO(dll_full_path)
                if io.init():
                    self.sdvx_io = io
                    print("[input] SDVX IO initialized and active.")
                else:
                    print("[input] SDVX IO init failed; will use controller/keyboard.")
            else:
                # Check for alternative sdvxio-valk.dll
                alt_path = os.path.join(os.path.dirname(dll_full_path), "sdvxio-valk.dll")
                if os.path.isfile(alt_path):
                    io = SDVXIO(alt_path)
                    if io.init():
                        self.sdvx_io = io
                        print(f"[input] SDVX IO ({alt_path}) initialized and active.")

    def _init_first_joystick(self):
        """Connect to the first available joystick."""
        try:
            count = pygame.joystick.get_count()
            if count > 0 and self._joystick is None:
                js = pygame.joystick.Joystick(0)
                js.init()
                self._joystick = js
                self._joystick_id = js.get_instance_id()
                print(f"[input] Controller: {js.get_name()}")
        except Exception:
            pass

    # ── public ──────────────────────────────────────────────────────────────

    def poll(self, events) -> set[str]:
        """
        Process this frame's inputs across SDVX IO, gamepad, and keyboard.
        Return set of triggered logical actions.
        """
        self._button_downs.clear()
        self._key_downs.clear()
        self._hat_values.clear()

        for event in events:
            if event.type == pygame.QUIT:
                return {"quit"}
            elif event.type == pygame.JOYBUTTONDOWN:
                self._button_downs.add(event.button)
            elif event.type == pygame.JOYHATMOTION:
                self._hat_values[event.hat] = event.value
            elif event.type == pygame.KEYDOWN:
                self._key_downs.add(event.key)
            elif event.type == pygame.JOYDEVICEADDED:
                if self._joystick is None:
                    self._init_first_joystick()
            elif event.type == pygame.JOYDEVICEREMOVED:
                if hasattr(event, 'instance_id') and event.instance_id == self._joystick_id:
                    self._joystick = None
                    self._joystick_id = None

        # Poll current hat state for held directions
        if self._joystick is not None:
            try:
                for h in range(self._joystick.get_numhats()):
                    val = self._joystick.get_hat(h)
                    if val != (0, 0):
                        self._hat_values[h] = val
            except Exception:
                pass

        # Poll analog stick axes for navigation
        axis_actions: set[str] = set()
        if self._joystick is not None:
            try:
                ax0 = self._joystick.get_axis(0)  # left stick X
                ax1 = self._joystick.get_axis(1)  # left stick Y
                if ax0 < -self._deadzone:
                    axis_actions.add("navigate_left")
                elif ax0 > self._deadzone:
                    axis_actions.add("navigate_right")
                if ax1 < -self._deadzone:
                    axis_actions.add("navigate_up")
                    axis_actions.add("launch_script_up")
                elif ax1 > self._deadzone:
                    axis_actions.add("navigate_down")
                    axis_actions.add("launch_script_down")
            except Exception:
                pass

        now = time.monotonic()
        triggered: set[str] = set()

        for action, bind in self._bindings.items():
            if self._check_bind(bind) or self._check_keyboard(action):
                if "navigate" in action or "launch_script" in action:
                    last = self._last_trigger.get(action, 0)
                    if (now - last) * 1000 < self._cooldown_ms:
                        continue
                self._last_trigger[action] = now
                triggered.add(action)

        # Add axis-triggered actions (with cooldown)
        for action in axis_actions:
            last = self._last_trigger.get(action, 0)
            if (now - last) * 1000 < self._cooldown_ms:
                continue
            self._last_trigger[action] = now
            triggered.add(action)

        # Keyboard Escape always maps to back
        if pygame.K_ESCAPE in self._key_downs:
            triggered.add("back")

        # ── Poll SDVX IO hardware ───────────────────────────────────────────
        if self.sdvx_io is not None:
            io_state = self.sdvx_io.poll()
            if io_state is not None:
                held = io_state["held"]
                pressed = io_state["pressed"]

                # Cabinet Test + Service combo exits launcher cleanly
                if held.get("TEST") and held.get("SERVICE"):
                    triggered.add("quit")

                # Both FX buttons held together always triggers back
                if held.get("FX_L") and held.get("FX_R"):
                    last = self._last_trigger.get("fx_dual_back", 0)
                    if (now - last) * 1000 >= self._cooldown_ms:
                        triggered.add("back")
                        self._last_trigger["fx_dual_back"] = now

                # Mapped buttons
                for btn, action in self._sdvx_btn_map.items():
                    if pressed.get(btn, False):
                        triggered.add(action)
                        self._last_trigger[action] = now
                    elif held.get(btn, False) and ("navigate" in action or "launch_script" in action):
                        last = self._last_trigger.get(action, 0)
                        if (now - last) * 1000 >= self._cooldown_ms:
                            triggered.add(action)
                            self._last_trigger[action] = now

                # Left Knob (VOL-L): carousel horizontal navigation
                dl = io_state["vol_l_delta"]
                if dl != 0:
                    self._knob_accum_l += dl
                    self._last_vol_l_time = now
                elif (now - self._last_vol_l_time) > 0.15:
                    self._knob_accum_l = 0

                if abs(self._knob_accum_l) >= self._knob_sensitivity:
                    last = self._last_trigger.get("sdvx_knob_l", 0)
                    if (now - last) * 1000 >= self._cooldown_ms:
                        if self._knob_accum_l > 0:
                            triggered.add("navigate_right")
                        else:
                            triggered.add("navigate_left")
                        self._last_trigger["sdvx_knob_l"] = now
                        self._knob_accum_l = 0

                # Right Knob (VOL-R): sub-menu script vertical navigation
                dr = io_state["vol_r_delta"]
                if dr != 0:
                    self._knob_accum_r += dr
                    self._last_vol_r_time = now
                elif (now - self._last_vol_r_time) > 0.15:
                    self._knob_accum_r = 0

                if abs(self._knob_accum_r) >= self._knob_sensitivity:
                    last = self._last_trigger.get("sdvx_knob_r", 0)
                    if (now - last) * 1000 >= self._cooldown_ms:
                        if self._knob_accum_r > 0:
                            triggered.add("navigate_down")
                            triggered.add("launch_script_down")
                        else:
                            triggered.add("navigate_up")
                            triggered.add("launch_script_up")
                        self._last_trigger["sdvx_knob_r"] = now
                        self._knob_accum_r = 0

                # Update button lamps
                if self._key_lights:
                    start_lamp = held.get("START", False) or self._start_lamp_attract
                    self.sdvx_io.set_button_lights(
                        a=held.get("A", False),
                        b=held.get("B", False),
                        c=held.get("C", False),
                        d=held.get("D", False),
                        fx_l=held.get("FX_L", False),
                        fx_r=held.get("FX_R", False),
                        start=start_lamp,
                        commit=not self._wing_lights,
                    )

        return triggered

    def update_wing_lights(self, wing_colors: dict):
        """Update cabinet wing and tape LEDs based on current background colors."""
        if self.sdvx_io is not None and self._wing_lights:
            self.sdvx_io.set_wing_lights(
                upper_left=wing_colors.get("upper_left", (0, 0, 0)),
                upper_right=wing_colors.get("upper_right", (0, 0, 0)),
                lower_left=wing_colors.get("lower_left", (0, 0, 0)),
                lower_right=wing_colors.get("lower_right", (0, 0, 0)),
                woofer=wing_colors.get("woofer", (0, 0, 0)),
                controller=wing_colors.get("controller", (0, 0, 0)),
                brightness=self._wing_brightness,
                commit=True,
            )

    def refresh_joystick(self):
        """Re-scan for controllers."""
        self._init_first_joystick()

    def shutdown(self):
        """Shut down SDVX IO and release hardware handles."""
        if self.sdvx_io is not None:
            self.sdvx_io.close()
            self.sdvx_io = None

    # ── private ─────────────────────────────────────────────────────────────

    def _check_bind(self, bind: dict) -> bool:
        if self._joystick is None:
            return False
        btype = bind.get("type", "")
        idx = bind.get("index", 0)

        if btype == "button":
            return idx in self._button_downs

        elif btype == "hat":
            expected = tuple(bind.get("value", [0, 0]))
            actual = self._hat_values.get(idx)
            if actual is None:
                return False
            return actual == expected

        elif btype == "axis":
            try:
                val = self._joystick.get_axis(idx)
            except Exception:
                return False
            threshold = bind.get("threshold", self._deadzone)
            if threshold >= 0:
                return val > threshold
            else:
                return val < threshold

        return False

    def _check_keyboard(self, action: str) -> bool:
        key = _KB_MAP.get(action)
        if key is None:
            return False
        return key in self._key_downs
