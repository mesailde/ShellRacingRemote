#!/usr/bin/env python3
import argparse
import asyncio
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import pygame
from bleak import BleakClient

CONTROL_SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
CONTROL_CHARACTERISTIC_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
STATUS_CHARACTERISTIC_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"
BATTERY_SERVICE_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_CHARACTERISTIC_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

QueueItem = Tuple[str, Optional[object]]


@dataclass
class ControlState:
    mode: int = 1
    throttle: int = 0
    steering: int = 0
    lights: bool = False
    turbo: bool = False
    donut: bool = False
    battery_pct: Optional[int] = None
    last_payload: bytes = b""
    last_status: Dict[str, int] = field(default_factory=dict)
    last_status_hex: str = ""
    message: str = ""


def build_control_payload(state: ControlState) -> bytes:
    return bytes(
        [
            state.mode & 0xFF,
            1 if state.throttle > 0 else 0,
            1 if state.throttle < 0 else 0,
            1 if state.steering < 0 else 0,
            1 if state.steering > 0 else 0,
            int(state.lights),
            int(state.turbo),
            int(state.donut),
        ]
    )


def decode_status_payload(data: bytes) -> Dict[str, int]:
    length = len(data)
    if length == 1:
        return {"length": length, "battery_pct": data[0]}
    if length == 8:
        return {
            "length": length,
            "mode": data[0],
            "forward": data[1],
            "reverse": data[2],
            "left": data[3],
            "right": data[4],
            "lights": data[5],
            "turbo": data[6],
            "donut": data[7],
        }
    return {"length": length, "raw": data.hex()}


def throttle_label(value: int) -> str:
    if value > 0:
        return "Forward"
    if value < 0:
        return "Reverse"
    return "Stopped"


def steering_label(value: int) -> str:
    if value < 0:
        return "Left"
    if value > 0:
        return "Right"
    return "Straight"


class BleController:
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        address: str,
        state: ControlState,
        ui_queue: "asyncio.Queue[QueueItem]",
    ) -> None:
        self.loop = loop
        self.address = address
        self.state = state
        self.ui_queue = ui_queue
        self._client: Optional[BleakClient] = None
        self._status_notify = False
        self._battery_notify = False
        self._stop_event = asyncio.Event()
        self._write_lock = asyncio.Lock()
        self._pending_payload: Optional[bytes] = None
        self._last_sent_payload: Optional[bytes] = None
        self._stopped = False

    async def run(self) -> None:
        self._queue_ui(("message", f"Connecting to {self.address}..."))
        try:
            async with BleakClient(self.address) as client:
                self._client = client
                self._queue_ui(("connected", None))
                await self._enable_notifications(client)
                await self._send_pending()
                await self._read_battery(client)
                await self._stop_event.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - best effort logging
            self._queue_ui(("error", f"Connection error: {exc}"))
        finally:
            await self._disable_notifications()
            self._client = None
            self._queue_ui(("disconnected", None))

    async def _enable_notifications(self, client: BleakClient) -> None:
        try:
            await client.start_notify(STATUS_CHARACTERISTIC_UUID, self._status_handler)
            self._status_notify = True
        except Exception as exc:
            self._queue_ui(("warn", f"Status notify failed: {exc}"))
        try:
            await client.start_notify(BATTERY_CHARACTERISTIC_UUID, self._battery_handler)
            self._battery_notify = True
        except Exception as exc:
            self._queue_ui(("warn", f"Battery notify failed: {exc}"))

    async def _disable_notifications(self) -> None:
        client = self._client
        if not client:
            return
        if self._status_notify:
            try:
                await client.stop_notify(STATUS_CHARACTERISTIC_UUID)
            except Exception:
                pass
            finally:
                self._status_notify = False
        if self._battery_notify:
            try:
                await client.stop_notify(BATTERY_CHARACTERISTIC_UUID)
            except Exception:
                pass
            finally:
                self._battery_notify = False

    def _status_handler(self, _: int, data: bytearray) -> None:
        payload = bytes(data)
        decoded = decode_status_payload(payload)
        self.state.last_status = decoded
        self.state.last_status_hex = payload.hex()
        self._queue_ui(("status", None))

    def _battery_handler(self, _: int, data: bytearray) -> None:
        payload = bytes(data)
        decoded = decode_status_payload(payload)
        battery = decoded.get("battery_pct")
        if battery is None and payload:
            battery = payload[0]
        if battery is not None:
            self.state.battery_pct = int(battery)
            self._queue_ui(("battery", int(battery)))
        else:
            self._queue_ui(("status", None))

    async def _read_battery(self, client: BleakClient) -> None:
        try:
            data = await client.read_gatt_char(BATTERY_CHARACTERISTIC_UUID)
        except Exception as exc:
            self._queue_ui(("warn", f"Initial battery read failed: {exc}"))
            return
        if data:
            self.state.battery_pct = int(data[0])
            self._queue_ui(("battery", int(data[0])))

    async def send_control(self, payload: bytes) -> None:
        if self._stopped:
            return
        if payload == self._last_sent_payload and self._pending_payload is None:
            return
        self._pending_payload = payload
        if self._client:
            await self._write_pending()

    async def _write_pending(self) -> None:
        if not self._client:
            return
        async with self._write_lock:
            while self._pending_payload is not None and self._client:
                payload = self._pending_payload
                self._pending_payload = None
                try:
                    await self._client.write_gatt_char(
                        CONTROL_CHARACTERISTIC_UUID,
                        payload,
                        response=False,
                    )
                except Exception as exc:
                    self._queue_ui(("error", f"ERROR sending command: {exc}"))
                    break
                self._last_sent_payload = payload
                self.state.last_payload = payload
                self._queue_ui(("payload", payload))

    async def request_battery(self) -> None:
        if self._stopped:
            return
        if not self._client:
            self._queue_ui(("message", "Battery read queued; waiting for connection"))
            return
        await self._read_battery(self._client)

    async def _send_pending(self) -> None:
        if self._pending_payload is not None and self._client:
            await self._write_pending()

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._stop_event.set()
        await self._disable_notifications()

    def _queue_ui(self, item: QueueItem) -> None:
        try:
            self.ui_queue.put_nowait(item)
        except asyncio.QueueFull:
            pass


class PygameApp:
    BG_COLOR = (20, 20, 24)
    TEXT_COLOR = (230, 230, 230)
    ACCENT_COLOR = (120, 200, 255)

    def __init__(self, loop: asyncio.AbstractEventLoop, address: str) -> None:
        self.loop = loop
        self.address = address
        self.state = ControlState()
        self.ui_queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self.ble = BleController(loop, address, self.state, self.ui_queue)

        self.running = False
        self.message = ""

        self.throttle_keys_down: set[str] = set()
        self.steering_keys_down: set[str] = set()
        self.toggle_keys_down: set[str] = set()
        self.last_throttle_key: Optional[str] = None
        self.last_steering_key: Optional[str] = None

        self.screen: Optional[pygame.Surface] = None
        self.font: Optional[pygame.font.Font] = None
        self.small_font: Optional[pygame.font.Font] = None

    async def run(self) -> None:
        pygame.init()
        pygame.font.init()
        pygame.display.set_caption("Shell Racing Legends Controller (pygame)")
        self.screen = pygame.display.set_mode((720, 420))
        self.font = pygame.font.SysFont("Segoe UI", 22)
        self.small_font = pygame.font.SysFont("Segoe UI", 16)
        pygame.key.set_repeat(0)

        ble_task = asyncio.create_task(self.ble.run())
        ui_task = asyncio.create_task(self.ui_consumer())

        try:
            await self.mainloop()
        finally:
            await self.shutdown()
            await asyncio.gather(ble_task, return_exceptions=True)
            self.ui_queue.put_nowait(("shutdown", None))
            await asyncio.gather(ui_task, return_exceptions=True)
            pygame.quit()

    async def mainloop(self) -> None:
        self.running = True
        clock = pygame.time.Clock()
        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.loop.create_task(self.shutdown())
                elif event.type == pygame.KEYDOWN:
                    self.handle_keydown(event)
                elif event.type == pygame.KEYUP:
                    self.handle_keyup(event)

            self.draw()
            await asyncio.sleep(0)
            clock.tick(60)

    def handle_keydown(self, event: pygame.event.Event) -> None:
        if not self.running:
            return
        key_name = pygame.key.name(event.key).lower()
        if key_name in {"w", "s"}:
            self.throttle_keys_down.add(key_name)
            self.last_throttle_key = key_name
            if self._update_throttle_from_keys():
                self.loop.create_task(self.ble.send_control(build_control_payload(self.state)))
        elif key_name in {"a", "d"}:
            self.steering_keys_down.add(key_name)
            self.last_steering_key = key_name
            if self._update_steering_from_keys():
                self.loop.create_task(self.ble.send_control(build_control_payload(self.state)))
        elif key_name in {"l", "t", "o", "m", "b", "q"}:
            if key_name not in self.toggle_keys_down:
                self.toggle_keys_down.add(key_name)
                self._handle_toggle_press(key_name)

    def handle_keyup(self, event: pygame.event.Event) -> None:
        key_name = pygame.key.name(event.key).lower()
        if key_name in self.throttle_keys_down:
            self.throttle_keys_down.discard(key_name)
            if self._update_throttle_from_keys():
                self.loop.create_task(self.ble.send_control(build_control_payload(self.state)))
        elif key_name in self.steering_keys_down:
            self.steering_keys_down.discard(key_name)
            if self._update_steering_from_keys():
                self.loop.create_task(self.ble.send_control(build_control_payload(self.state)))
        if key_name in self.toggle_keys_down:
            self.toggle_keys_down.discard(key_name)

    def _update_throttle_from_keys(self) -> bool:
        old = self.state.throttle
        if "w" in self.throttle_keys_down and "s" in self.throttle_keys_down:
            new_value = 1 if self.last_throttle_key == "w" else -1
        elif "w" in self.throttle_keys_down:
            new_value = 1
        elif "s" in self.throttle_keys_down:
            new_value = -1
        else:
            new_value = 0
        if new_value != old:
            self.state.throttle = new_value
            self.state.message = throttle_label(new_value)
            return True
        return False

    def _update_steering_from_keys(self) -> bool:
        old = self.state.steering
        if "a" in self.steering_keys_down and "d" in self.steering_keys_down:
            new_value = -1 if self.last_steering_key == "a" else 1
        elif "a" in self.steering_keys_down:
            new_value = -1
        elif "d" in self.steering_keys_down:
            new_value = 1
        else:
            new_value = 0
        if new_value != old:
            self.state.steering = new_value
            self.state.message = steering_label(new_value)
            return True
        return False

    def _handle_toggle_press(self, key_name: str) -> None:
        if key_name == "l":
            self.state.lights = not self.state.lights
            self.state.message = f"Lights {'ON' if self.state.lights else 'OFF'}"
            self.loop.create_task(self.ble.send_control(build_control_payload(self.state)))
        elif key_name == "t":
            self.state.turbo = not self.state.turbo
            self.state.message = f"Turbo {'ON' if self.state.turbo else 'OFF'}"
            self.loop.create_task(self.ble.send_control(build_control_payload(self.state)))
        elif key_name == "o":
            self.state.donut = not self.state.donut
            self.state.message = f"Donut {'ON' if self.state.donut else 'OFF'}"
            self.loop.create_task(self.ble.send_control(build_control_payload(self.state)))
        elif key_name == "m":
            self.state.mode = 2 if self.state.mode == 1 else 1
            self.state.message = f"Mode set to {self.state.mode}"
            self.loop.create_task(self.ble.send_control(build_control_payload(self.state)))
        elif key_name == "b":
            self.state.message = "Battery refresh requested"
            self.loop.create_task(self.ble.request_battery())
        elif key_name == "q":
            self.loop.create_task(self.shutdown())

    async def ui_consumer(self) -> None:
        while True:
            kind, data = await self.ui_queue.get()
            if kind == "shutdown":
                break
            self._handle_ui_message(kind, data)

    def _handle_ui_message(self, kind: str, data: Optional[object]) -> None:
        if kind == "message":
            self.message = str(data)
        elif kind == "warn":
            self.message = f"WARN: {data}"
        elif kind == "error":
            self.message = f"ERROR: {data}"
        elif kind == "battery":
            self.state.battery_pct = int(data)
            self.message = f"Battery: {data}%"
        elif kind == "status":
            self.message = "Status notification received"
        elif kind == "payload":
            payload = data if isinstance(data, bytes) else b""
            self.state.last_payload = payload
            self.message = f"Command sent: {payload.hex() if payload else '--'}"
        elif kind == "connected":
            self.message = "Connected"
        elif kind == "disconnected":
            if self.running:
                self.message = "Disconnected"

    async def shutdown(self) -> None:
        if not self.running:
            return
        self.running = False
        self.throttle_keys_down.clear()
        self.steering_keys_down.clear()
        self.toggle_keys_down.clear()
        self.state.throttle = 0
        self.state.steering = 0
        await self.ble.send_control(build_control_payload(self.state))
        await self.ble.stop()

    def draw(self) -> None:
        if not self.screen or not self.font or not self.small_font:
            return
        self.screen.fill(self.BG_COLOR)

        lines = [
            f"Target: {self.address}",
            f"Battery: {'--' if self.state.battery_pct is None else str(self.state.battery_pct) + '%'}",
            f"Mode: {self.state.mode}",
            f"Throttle: {throttle_label(self.state.throttle)}",
            f"Steering: {steering_label(self.state.steering)}",
            f"Lights: {'ON' if self.state.lights else 'OFF'}",
            f"Turbo: {'ON' if self.state.turbo else 'OFF'}",
            f"Donut: {'ON' if self.state.donut else 'OFF'}",
            f"Last payload: {self.state.last_payload.hex() if self.state.last_payload else '--'}",
            f"Last status: {self._format_last_status()}",
        ]

        for idx, text in enumerate(lines):
            surface = self.font.render(text, True, self.TEXT_COLOR)
            self.screen.blit(surface, (24, 24 + idx * 28))

        message = self.message or self.state.message or "--"
        message_surface = self.font.render(f"Message: {message}", True, self.ACCENT_COLOR)
        self.screen.blit(message_surface, (24, 24 + len(lines) * 28 + 12))

        instructions = (
            "Keys: w/s throttle, a/d steering, l lights, t turbo, o donut, m mode, b battery, q quit"
        )
        instructions_surface = self.small_font.render(instructions, True, (180, 180, 180))
        self.screen.blit(instructions_surface, (24, self.screen.get_height() - 40))

        pygame.display.flip()

    def _format_last_status(self) -> str:
        if self.state.last_status:
            items = [f"{k}={v}" for k, v in self.state.last_status.items() if k != "length"]
            return ", ".join(items) if items else str(self.state.last_status)
        if self.state.last_status_hex:
            return self.state.last_status_hex
        return "--"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pygame-based controller for Shell Racing Legends cars",
    )
    parser.add_argument("address", help="Bluetooth MAC address of the car")
    return parser.parse_args()


async def main(address: str) -> None:
    loop = asyncio.get_running_loop()
    app = PygameApp(loop, address)
    await app.run()


def run() -> None:
    args = parse_args()
    asyncio.run(main(args.address))


if __name__ == "__main__":
    run()
