# VibeltexMenu — How It Works

A **fullscreen, dual-display game launcher** built with Python, Pygame, and SDL2.  
Designed for arcade-cabinet setups (portrait primary + landscape secondary) with Xbox 360 controller and touchscreen support.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                           main.py                                │
│   Entry point — initializes subsystems, runs the game loop       │
├──────────┬──────────┬──────────────┬─────────────────────────────┤
│ config.py│audio_    │input_        │carousel.py   grid_menu.py   │
│          │manager.py│manager.py    │(primary)     (secondary)    │
└──────────┴──────────┴──────────────┴─────────────────────────────┘
         ▲                                      ▲
         │                                      │
    settings.yaml                          games.yaml
```

### Module Responsibilities

| Module | Purpose |
|--------|---------|
| **`main.py`** | Bootstraps Pygame + SDL2, creates both windows, wires up all subsystems, runs the main loop |
| **`config.py`** | Loads and validates `settings.yaml` + `games.yaml`, resolves asset paths, provides defaults |
| **`audio_manager.py`** | ASIO-aware BGM playback with crossfade + one-shot SFX via `sounddevice` / `soundfile` |
| **`input_manager.py`** | Unified controller (buttons, axes, hats) + keyboard input with configurable keybinds and debounce |
| **`carousel.py`** | Primary display — Wii-style horizontal cover-flow with smooth scrolling, precomputed transforms, and sub-menu overlay |
| **`grid_menu.py`** | Secondary display — SDL2 Renderer-based touch-scrollable grid of game covers |

---

## Dual-Display System

The launcher drives **two screens simultaneously**:

### Primary Display (Carousel)
- **Resolution:** 1080×1920 (portrait) — configurable via `settings.yaml`
- **Renderer:** Standard Pygame `display.set_mode` with `FULLSCREEN | NOFRAME`
- **Input:** Xbox 360 controller (D-pad / analog stick) + keyboard arrows
- **Features:** Cover-flow animation, background crossfade, sub-menu for launch script selection

### Secondary Display (Grid Menu)
- **Resolution:** 1920×1080 (landscape) — hardcoded
- **Renderer:** `pygame._sdl2.video.Renderer` (hardware-accelerated)
- **Input:** Touchscreen (mouse events mapped with coordinate transform: `touch_x = mouse_y`, `touch_y = 1080 - mouse_x`)
- **Features:** Scrollable 2-row grid of covers, drag-to-scroll with inertia, tap-to-launch

Both windows update every frame in the same main loop.

---

## Main Loop (`main.py`)

The main loop runs at the configured FPS (default 60, set to 120 in `settings.yaml`) and does the following every frame:

1. **Tick** — `clock.tick(FPS)` → compute `dt` in seconds
2. **Events** — `pygame.event.get()` → forward mouse events to `GridMenu`, collect all events
3. **Input** — `InputManager.poll(events)` → returns a set of logical action strings (e.g. `"navigate_left"`, `"confirm"`, `"quit"`)
4. **Update** — `carousel.update(dt, actions)` and `grid_menu.update(dt)` — advance animations, process actions
5. **BGM change** — If the selected game index changed, crossfade to the new game's BGM (or stop if none)
6. **Launch check** — If either the carousel or grid menu has a `launch_request`, play the start sound, spawn the game's batch script via `subprocess.Popen`, then exit the launcher
7. **Render** — `carousel.draw(screen)` → `pygame.display.flip()` → `grid_menu.draw()` (presents via SDL2 renderer)

---

## Configuration

Both `settings.yaml` (or `settings.yml`) and `games.yaml` (or `game.yml` / `games.yml`) are loaded from next to the executable and are **automatically generated if missing or empty**.

The executable dynamically requires the external asset and hardware folders (`images/`, `sounds/`, `font/`, `IO/`) in the same directory.

### `settings.yaml` (or `settings.yml`)

Controls display, audio, keybinds, and input behavior. Missing keys fall back to hardcoded defaults in `config.py` via deep-merge.

```yaml
display:
  width: 1080           # Primary window width
  height: 1920          # Primary window height
  fullscreen: false     # Fullscreen mode
  monitor: 0            # Display index for primary
  secondary_monitor: 1  # Display index for grid menu
  fps: 120

audio:
  device: "XONAR SOUND CARD(64)" # ASIO device name (ASUS Xonar AE 7.1)
  output_channels: [0, 1]       # ASIO channel selectors
  sample_rate: 44100
  crossfade_ms: 80              # BGM crossfade duration
  volume: 0.7                   # Master volume (0.0–1.0)

keybinds:
  navigate_up:    { type: hat, index: 0, value: [0, 1] }
  navigate_down:  { type: hat, index: 0, value: [0, -1] }
  navigate_left:  { type: hat, index: 0, value: [-1, 0] }
  navigate_right: { type: hat, index: 0, value: [1, 0] }
  confirm:        { type: button, index: 0 }   # A button
  back:           { type: button, index: 1 }   # B button

input:
  navigate_cooldown_ms: 40     # Debounce delay for navigation repeats
  axis_deadzone: 0.5           # Analog stick threshold
  show_inputs: true            # Visual input debug overlay

sdvxio:
  enabled: true               # Direct bemanitools IO support without ViGEm
  dll_path: "IO/sdvxio.dll"   # Path to sdvxio DLL relative to launcher
  knob_sensitivity: 15        # Encoder ticks before triggering navigation
  key_lights: true            # Illuminate button lamps when pressed
  wing_lights: true           # Light wings according to background picture
  wing_brightness: 1.0        # Master brightness multiplier (0.0 - 1.0)
  start_lamp_attract: true    # Attract lighting on START button
```

**Keybind types:**
- `button` — joystick button press (`index` = button number)
- `hat` — D-pad direction (`index` = hat number, `value` = `[x, y]`)
- `axis` — analog stick (`index` = axis number, `threshold` = trigger value)

**Keyboard fallbacks** (always active):
- Arrow keys → navigation
- Enter → confirm
- Escape → back / quit

### `games.yaml`

Defines the game library. Each top-level key is a game name; order determines carousel position.

```yaml
Sound Voltex VI - Exceed Gear:
  bg image: exceedgear.png            # Filename in images/
  sound subfolder: sdvx-6             # Subfolder in sounds/
  bgm: title_bgm_S2.ogg              # BGM file in that subfolder
  start sound: start.ogg             # (optional) SFX on launch
  launch scripts:
    1:
      name: Final CGDev               # Display name in sub-menu
      path: D:\full-path-to-batch-file # Absolute path to .bat
    2:
      name: Final Marbleblue
      path: D:\full-path-to-batch-file
```

**Path resolution** (done in `config.py`):
- `bg_image_path` → `<repo>/images/<bg image>`
- `bgm_path` → `<repo>/sounds/<sound subfolder>/<bgm>`
- `start_sound_path` → `<repo>/sounds/<sound subfolder>/<start sound>`
- `launch_scripts[].path` → used as-is (absolute paths)

---

## Module Deep Dives

### `audio_manager.py` — ASIO Audio Engine

Uses `sounddevice` (PortAudio wrapper) with `SD_ENABLE_ASIO=1` for low-latency ASIO output.

**Key design:**
- A single `sd.OutputStream` runs a **PortAudio callback** (`_callback`) on a real-time audio thread
- The callback mixes three layers every block (2048 frames):
  1. **BGM** — looping playback from a numpy array
  2. **Crossfade** — blends old → new BGM using a linear per-sample envelope
  3. **SFX** — one-shot overlay, mixed additively
- Audio files are decoded via `soundfile` to float32 numpy arrays, resampled if needed, and **cached** in memory
- All state mutations are protected by a `threading.Lock`

**API:**
- `play_bgm(path)` — start a BGM track immediately
- `crossfade_to(path)` — smooth transition from current BGM to new track
- `play_sfx(path)` — fire-and-forget sound effect
- `stop()` / `shutdown()` — halt playback and close the stream

### `input_manager.py` — Controller, Keyboard & SDVX IO

Unifies arcade IO, gamepad, and keyboard input into semantic actions (`"navigate_left"`, `"navigate_right"`, `"confirm"`, `"back"`, `"quit"`).

**Flow per frame:**
1. Scan Pygame events for `JOYBUTTONDOWN`, `JOYHATMOTION`, `KEYDOWN`, device add/remove.
2. Poll joystick hat states and analog stick axes.
3. Check and debounce configured gamepad/keyboard keybinds.
4. **Poll SDVX IO (`sdvx_io.py`)**:
   - Reads 10-bit rotary encoders (VOL-L & VOL-R) and computes wrapped delta.
   - Left knob (VOL-L) navigates carousel horizontally; Right knob (VOL-R) selects launch options in the sub-menu.
   - START button triggers `"confirm"`.
   - Dual FX-L + FX-R buttons trigger `"back"`.
   - Cabinet TEST + SERVICE combo triggers `"quit"`.
   - Updates cabinet button lamps (attract mode for START + reactive lighting on press).
   - **Dynamic Cabinet Wing & Tape LEDs**: Real-time PWM lighting on cabinet wings, woofer, and deck that dynamically match and crossfade with the background picture of the selected game.
5. On game launch or exit, `input_manager.shutdown()` releases the COM/USB hardware handles cleanly so the launched game can access the IO board immediately without port conflicts.

### `sdvx_io.py` — Bemanitools SDVX IO Ctypes Bridge

Direct interface to `sdvxio.dll` and Valkyrie/AIO hardware (`libaio.dll`, `libaio-iob.dll`, etc.) without running ViGEm:
- Loads 64-bit DLLs with automatic DLL directory registration.
- Exports Win32 thread shims and logger callbacks required by `bemanitools`.
- Manages GPIO button reading, 10-bit encoder polling, and output lamp control (`sdvx_io_set_gpio_lights` & `sdvx_io_write_output`).
- Controls 18–23 PWM lighting channels for cabinet wings (Upper/Lower Left & Right), woofer, and controller deck.

### `carousel.py` — Primary Display UI

A Wii-style horizontal cover-flow renderer.

**At boot (Ultra-Fast Startup Optimization):**
1. **Parallel Threaded Asset Loading**: Background images are deduplicated and loaded/processed concurrently across multiple CPU threads using `concurrent.futures.ThreadPoolExecutor`.
2. **Dimension Bypass**: Images already matching 1080×1920 skip redundant `smoothscale` and cropping, saving CPU cycles.
3. **Parallel Multi-Core Precompute**: Instead of stalling boot time for 5.4+ seconds computing 671 rotation surfaces on a single thread or running unstable background threads during render, cover variants are precomputed concurrently across all available CPU cores using `ThreadPoolExecutor` at boot (~600ms total). This guarantees zero frame drops, zero runtime transforms, and completely eliminates surface-lock conflicts.
4. **Shared Cover Surfaces**: `GridMenu` reuses the 9:16 cover surfaces already processed in memory by `Carousel`, cutting secondary display cover loading from ~800ms down to ~20ms.
5. **Direct3D 11 Selection**: Window renderer preferentially targets `direct3d11`, eliminating the 1.1s fallback delay caused by legacy Direct3D 9 device creation failures on modern systems.

**Smooth Animation Engine:**
- **Frame-Rate Independent Glide**: `self._scroll_pos` glides toward `target` using exponential decay (`1.0 - math.exp(-speed * dt)`), providing identical, buttery-smooth glide across 60Hz, 120Hz, and 240Hz monitors.
- **Continuous Cinematic Crossfade**: Fullscreen backgrounds smoothly dissolve using Hermite smoothstep (`blend * blend * (3.0 - 2.0 * blend)`) based on continuous scroll position, eliminating all flicker even during high-speed knob spins.
- **Title Crossfade & Drift**: Game titles smoothly crossfade with a subtle 24px horizontal drift matching scroll direction.
- **Center Cover Breathing Aura**: The settled selected game pulses with a soft neon accent glow (`sin(time * 0.004)`).
- **Floating Hover Arrows**: Navigation arrows gently float and pulse with a soft breathing cycle.
- **Animated Sub-Menu**: Launch script sub-menu opens with cubic ease-out scale/fade-in, and moving up/down smoothly slides the glowing selection pill and indicator bar with spring physics.

### `grid_menu.py` — Secondary Display UI

A hardware-accelerated touch-friendly grid rendered via SDL2's Renderer API.

**Layout:** 2 rows of cover thumbnails, scrollable horizontally.

**Touch & Animation Physics:**
- Coordinates are remapped (`touch_x = mouse_y`, `touch_y = 1080 - mouse_x`) to account for a rotated touchscreen.
- **Inertial Scrolling with Rubber-Band Bounce**: Velocity decays using frame-rate independent exponential friction (`math.exp(-5.0 * dt)`), with smooth spring bounce when swiping past grid edges.
- **Dual-Screen Synchronization**: Automatically tracks `carousel.current_index`, highlights the selected game with an animated glowing neon border, and smoothly scrolls the viewport to keep the active game in view.
- Short taps (< 10px movement) open the game's launch sub-menu or launch directly.

**Rendering:** All cover textures are pre-cached as `video.Texture` objects directly from Carousel covers in memory. Only visible columns are drawn each frame.

---

## Directory Structure

```
VibeltexMenu/
├── main.py                  # Entry point
├── config.py                # YAML config loader
├── audio_manager.py         # ASIO audio engine
├── input_manager.py         # Controller + keyboard input
├── carousel.py              # Primary display (cover flow)
├── grid_menu.py             # Secondary display (touch grid)
├── games.yaml               # Game library definition
├── settings.yaml            # Runtime settings
├── start.bat                # Quick launcher (python .\main.py)
├── VibeltexLauncher.exe     # PyInstaller-built standalone binary
├── VibeltexLauncher.spec    # PyInstaller build spec
├── font/
│   └── NotoSansJP-Regular.otf   # CJK-capable font for Japanese game titles
├── images/
│   ├── booth.png, exceedgear.png, ...   # Game background / cover images (1080×1920)
│   └── ui/                              # UI assets (fonts, loading indicator, controls)
├── sounds/
│   ├── sdvx-1/ ... sdvx-7/    # Per-game sound folders (BGM, start sounds, .2dx archives)
│   ├── usc/                   # unnamed_sdvx_clone sounds
│   ├── piano/                 # Piano game sounds
│   └── ui/                    # UI cursor sounds
├── scratch/                   # Development test scripts
└── build/                     # PyInstaller build artifacts
```

---

## Game Launch Flow

```
User selects game → Presses A (confirm)
    │
    ├─ 1 launch script → Launch immediately
    │
    └─ Multiple scripts → Open sub-menu
                              │
                              └─ User picks script → Confirm
                                      │
                                      ▼
                              Play start sound (SFX)
                              subprocess.Popen("cmd /c <script_path>")
                              Audio shutdown → Pygame quit → sys.exit(0)
```

The launcher **exits itself** after launching a game. The game's batch script runs in a new console window.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `pygame` | Windowing, rendering, input, font rendering |
| `pygame._sdl2.video` | Hardware-accelerated secondary display renderer |
| `sounddevice` | PortAudio audio output (with ASIO support) |
| `soundfile` | Audio file decoding (OGG, WAV, etc.) |
| `numpy` | Audio buffer manipulation |
| `pyyaml` | YAML configuration parsing |

### Building the Executable

The standalone `.exe` is built with **PyInstaller** using the included `.spec` file:

```bash
pyinstaller VibeltexLauncher.spec
```

This produces a single-file executable (`console=False` for no terminal window).
