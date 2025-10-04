import argparse
import asyncio
from typing import Dict, List

from bleak import BleakClient

CONTROL_SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
CONTROL_CHARACTERISTIC_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
STATUS_CHARACTERISTIC_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"
BATTERY_SERVICE_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_CHARACTERISTIC_UUID = "00002a19-0000-1000-8000-00805f9b34fb"


def build_control_payload(
    throttle: int,
    steering: int,
    *,
    lights: bool,
    turbo: bool,
    donut: bool,
    mode: int = 0x01,
) -> bytes:
    forward = 1 if throttle > 0 else 0
    reverse = 1 if throttle < 0 else 0
    turn_left = 1 if steering < 0 else 0
    turn_right = 1 if steering > 0 else 0

    payload = bytes(
        [
            mode & 0xFF,
            forward,
            reverse,
            turn_left,
            turn_right,
            int(lights),
            int(turbo),
            int(donut),
        ]
    )
    return payload


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


async def run(address: str, args: argparse.Namespace) -> None:
    async with BleakClient(address) as client:
        print(f"[INFO] Connected to {address}")

        battery = await client.read_gatt_char(BATTERY_CHARACTERISTIC_UUID)
        if battery:
            print(f"[BAT] {int(battery[0])}% ({battery.hex()})")

        any_notification = False
        notifications_received: List[str] = []

        async def notification_handler(characteristic, data: bytearray) -> None:
            nonlocal any_notification
            any_notification = True
            if hasattr(characteristic, "handle"):
                marker = f"0x{characteristic.handle:04x}"
            elif isinstance(characteristic, int):
                marker = f"0x{characteristic:04x}"
            else:
                marker = str(characteristic)
            notifications_received.append(marker)
            status = decode_status_payload(bytes(data))
            print(f"[NOTIFY {marker}] {status} ({data.hex()})")

        subscribed: List[str] = []
        try:
            await client.start_notify(STATUS_CHARACTERISTIC_UUID, notification_handler)
            subscribed.append(STATUS_CHARACTERISTIC_UUID)
        except Exception as exc:  # pragma: no cover - best effort logging
            print(
                "[WARN] Failed to subscribe to status notifications (0xFFF2):"
                f" {exc}"
            )
        try:
            await client.start_notify(BATTERY_CHARACTERISTIC_UUID, notification_handler)
            subscribed.append(BATTERY_CHARACTERISTIC_UUID)
        except Exception as exc:  # pragma: no cover
            print(
                "[WARN] Failed to subscribe to battery notifications (0x2A19):"
                f" {exc}"
            )

        payload = build_control_payload(
            throttle=args.throttle,
            steering=args.steering,
            lights=args.lights,
            turbo=args.turbo,
            donut=args.donut,
            mode=args.mode,
        )
        print(f"[SEND] {payload.hex()}")
        await client.write_gatt_char(
            CONTROL_CHARACTERISTIC_UUID, payload, response=False
        )

        if args.read_status:
            status_bytes = await client.read_gatt_char(STATUS_CHARACTERISTIC_UUID)
            if status_bytes:
                decoded = decode_status_payload(bytes(status_bytes))
                print(
                    f"[READ 0xFFF2] {decoded} ({status_bytes.hex()})"
                )
            else:
                print("[READ 0xFFF2] (no data returned)")

            battery_bytes = await client.read_gatt_char(BATTERY_CHARACTERISTIC_UUID)
            if battery_bytes:
                decoded_batt = decode_status_payload(bytes(battery_bytes))
                print(
                    f"[READ 0x2A19] {decoded_batt} ({battery_bytes.hex()})"
                )
            else:
                print("[READ 0x2A19] (no data returned)")

        try:
            await asyncio.sleep(args.listen)
        finally:
            for uuid in subscribed:
                await client.stop_notify(uuid)
            if not any_notification:
                print(
                    "[WARN] No notifications received on 0xFFF2/0x2A19; "
                    "increase --listen or verify that the car emits telemetry."
                )
            else:
                joined = ",".join(sorted(set(notifications_received)))
                print(f"[INFO] Notifications observed on handles: {joined}")
            print("[INFO] Done")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Shell RC BLE validation tool",
        epilog=(
            "Payload layout follows DBluetoothConnection.SendMessageToDevice: "
            "[mode, up, down, left, right, light, turbo, donut]. "
            "Throttle and steering arguments only evaluate their sign and are converted "
            "to digital up/down/left/right bits. Official app captures show that "
            "characteristic 0x2A19 notifies a single battery percentage byte, while "
            "0xFFF2 still returns zeroed buffers unless the car exposes richer telemetry."
        ),
    )
    parser.add_argument(
        "address",
        help=(
            "Bluetooth MAC address of the car (example: 13:05:AA:05:6D:05). "
            "The script will connect over BLE using this identifier."
        ),
    )
    parser.add_argument(
        "--throttle",
        type=int,
        default=1,
        metavar="VALUE",
        help=(
            "Acceleration hint; only the sign is used. >0 sets the 'up' bit (byte 1), "
            "<0 sets the 'down' bit (byte 2), 0 keeps both cleared."
        ),
    )
    parser.add_argument(
        "--steering",
        type=int,
        default=0,
        metavar="VALUE",
        help=(
            "Steering hint; only the sign is used. >0 sets the 'right' bit (byte 4), "
            "<0 sets the 'left' bit (byte 3), 0 keeps both cleared."
        ),
    )
    parser.add_argument(
        "--mode",
        type=int,
        default=1,
        choices=(1, 2),
        help="Value copied to byte 0 of the packet. Known modes: 1 (normal), 2 (alternate).",
    )
    parser.add_argument(
        "--lights",
        action="store_true",
        help="Set byte 5 to 1, enabling headlights.",
    )
    parser.add_argument(
        "--turbo",
        action="store_true",
        help="Set byte 6 to 1, enabling turbo mode.",
    )
    parser.add_argument(
        "--donut",
        action="store_true",
        help="Set byte 7 to 1, enabling donut mode.",
    )
    parser.add_argument(
        "--listen",
        type=float,
        default=5.0,
        metavar="SECONDS",
        help="Seconds to keep the connection open for status notifications after sending the command.",
    )
    parser.add_argument(
        "--read-status",
        action="store_true",
        help="Immediately read characteristics 0xFFF2 and 0x2A19 after writing to capture telemetry payloads.",
    )
    args = parser.parse_args()
    asyncio.run(run(args.address, args))


if __name__ == "__main__":
    main()