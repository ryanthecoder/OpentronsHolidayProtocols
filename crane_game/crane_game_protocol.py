from opentrons.protocol_api import ProtocolContext
from opentrons.types import Mount, Point, Location
from collections import deque
import enum
import time
import asyncio
import os
import struct
import threading
import usb.core
import usb.util

metadata = {
    "protocolName": "Crane Game Protocol",
    "description": "Opentrons Crane Game to be played with (TBD) input system!",
    "author": "Opentrons R&D Team",
}

requirements = {
	"robotType": "Flex",
	"apiLevel": "2.31"
}

# Controller Input USB Global data
VENDOR_ID = 0x045E
PRODUCT_ID = 0x02DD

# Xbox One GIP initialization packet to wake up controller input streaming
XBOX_INIT_PACKET = bytes([0x05, 0x20, 0x00, 0x01, 0x00])

# Flag to control device input, used for XBox input control
USER_XBOX_INPUT = True

class InputDirections(enum.Enum):
    TOWARDS_REAR = 1
    TOWARDS_FRONT = 2
    LEFT = 3
    RIGHT = 4
    REAR_LEFT = 5
    REAR_RIGHT = 6
    FRONT_LEFT = 7
    FRONT_RIGHT = 8

# Minimum distance (mm) to move the gantry along the X and Y axes based on user input
# NOTE: Some napkin-math:  5mm @ 10 samples/sec means we'd cross all deck slots
# right to left (350mm~) in 7 seconds~ (not accounting for command exec time), if
# that feels too fast or slow we can adjust!
MIN_MOVE_DISTANCE = 10.0
MOVE_SCALE = 10.0 # Scales off the Joystick

# Sample rate (S) for user inputs
INPUT_SAMPLE_RATE = 0.01 

# Maximum time (S) the user has to move the gantry around before the gripper tries to "Grab"
PLAY_TIMER = 30

# Global queue of user inputs for processing
user_input_queue: deque[tuple[InputDirections, float]] = deque()

# Set of test inputs for debugging the script - DISBALE FOR LIVE RUNS
USE_TEST_INPUTS = False
TEST_INPUTS = [
    (InputDirections.TOWARDS_FRONT, 1),
    (InputDirections.TOWARDS_FRONT, 1),
    (InputDirections.TOWARDS_FRONT, 1),
    (InputDirections.TOWARDS_FRONT, 1),
    (InputDirections.TOWARDS_FRONT, 1),
    (InputDirections.LEFT, 1),
    (InputDirections.LEFT, 1),
    (InputDirections.LEFT, 1),
    (InputDirections.LEFT, 1),
    (InputDirections.LEFT, 1),
    (InputDirections.TOWARDS_REAR, 1),
    (InputDirections.RIGHT, 1),
]

def xbox_usb_reader(stop_event):
    """Background thread that reads raw USB packets and posts axis events."""
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        print("Xbox controller not found on USB.")
        return

    # Claim interface 0 (main gamepad interface)
    if dev.is_kernel_driver_active(0):
        dev.detach_kernel_driver(0)
    dev.set_configuration()
    usb.util.claim_interface(dev, 0)

    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]

    ep_in = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN,
    )
    ep_out = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT,
    )

    # Send initialization packet so the controller starts sending stick/button data
    ep_out.write(XBOX_INIT_PACKET)
    print(f"Connected: {dev.product} (via raw USB)")

    while not stop_event.is_set():
        try:
            data = ep_in.read(ep_in.wMaxPacketSize, timeout=100)

            # Packet ID 0x20 is the standard Xbox input report (18 bytes)
            if len(data) >= 18 and data[0] == 0x20:
                # Unpack signed 16-bit integers (little-endian) from bytes 10..17
                lx, ly, rx, ry = struct.unpack("<hhhh", bytes(data[10:18]))

                # Normalize to generic Joystick's -1.0 to 1.0 range (invert Y so up is -1.0, matching SDL)
                axes = [
                    max(-1.0, lx / 32767.0),
                    max(-1.0, -ly / 32767.0),
                    max(-1.0, rx / 32767.0),
                    max(-1.0, -ry / 32767.0),
                ]

                # Collect directions of given Axes
                used_directions: list[InputDirections] = []
                for axis_idx, val in enumerate(axes):
                    # Left Joystick X is Axis 0
                    # Left Joystick Y is Axis 1
                    # Below we will use ~0.1 as a deadzone buffer to account for stick drift

                    if axis_idx == 0 and val > 0.1:
                        used_directions.append(InputDirections.RIGHT)
                    elif axis_idx == 0 and val < -0.1:
                        used_directions.append(InputDirections.LEFT)
                    if axis_idx == 1 and val > 0.1:
                        used_directions.append(InputDirections.TOWARDS_FRONT)
                    elif axis_idx == 1 and val < -0.1:
                        used_directions.append(InputDirections.TOWARDS_REAR)

                # Coordinate directions to input queue, accounting for diagonal inputs
                if InputDirections.RIGHT in used_directions:
                    if InputDirections.TOWARDS_FRONT in used_directions:
                        user_input_queue.append((InputDirections.FRONT_RIGHT, abs(val) * MOVE_SCALE))
                    elif InputDirections.TOWARDS_REAR in used_directions:
                        user_input_queue.append((InputDirections.REAR_RIGHT, abs(val) * MOVE_SCALE))
                    else:
                        user_input_queue.append((InputDirections.RIGHT, abs(val) * MOVE_SCALE))
                elif InputDirections.LEFT in used_directions:
                    if InputDirections.TOWARDS_FRONT in used_directions:
                        user_input_queue.append((InputDirections.FRONT_LEFT, abs(val) * MOVE_SCALE))
                    elif InputDirections.TOWARDS_REAR in used_directions:
                        user_input_queue.append((InputDirections.REAR_LEFT, abs(val) * MOVE_SCALE))
                    else:
                        user_input_queue.append((InputDirections.LEFT, abs(val) * MOVE_SCALE))
                elif InputDirections.TOWARDS_REAR in used_directions:
                    user_input_queue.append((InputDirections.TOWARDS_REAR, abs(val) * MOVE_SCALE))
                elif InputDirections.TOWARDS_FRONT in used_directions:
                    user_input_queue.append((InputDirections.TOWARDS_REAR, abs(val) * MOVE_SCALE))
                

        except usb.core.USBTimeoutError:
            continue
        except usb.core.USBError as e:
            print(f"USB disconnected or error: {e}")
            break

    usb.util.release_interface(dev, 0)

async def input_sampler_task(terminate_signal: asyncio.Event):
    """This is the task used to sample user input, input is collected through -TBD-."""
    # TODO this whole task is TBD
    # We might sample input here through a controller driver
    # Or through a web socket or endpointed called from an app (or the ODD even)
    # We could even take input via python requests through Pyro from other devices (like a laptop)

    # Logic wise this task will need to queue up inputs and remove stale ones when it can

    device_thread_terminator = threading.Event()
    if USER_XBOX_INPUT:
        usb_thread = threading.Thread(target=xbox_usb_reader, args=(device_thread_terminator,), daemon=True)
        usb_thread.start()

    while not terminate_signal.is_set():
        # NOTE: This is designed to maintain sampling until the `protocol_actions_task`` is complete!

        if USE_TEST_INPUTS:
            # Test the script by moving the gripper several times then grabbing and dropping
            if len(TEST_INPUTS) > 0:
                user_input_queue.append(TEST_INPUTS.pop(0))

        # Sample inputs 10 times a second (bump down time if we need higher fidelity)
        await asyncio.sleep(INPUT_SAMPLE_RATE)

    # If we are using device, stop sampling it after timeout
    device_thread_terminator.set()


async def protocol_actions_task(ctx: ProtocolContext, terminate_signal: asyncio.Event):
    """Actual protocol PAPI logic for the Crane Game."""
    prize_chute = ctx.load_waste_chute()

    # We load a fake plate to get a low but safe "grip height" off the deck
    fake_plate = ctx.load_labware(load_name="opentrons_96_wellplate_200ul_pcr_full_skirt", location="A1")

    def _grab_prize():
        """Move the gripper towards the deck at it's current location, and then closes the jaws."""
        
        ctx.robot.move_axes_to(
            axis_map= {"Z_G": fake_plate.highest_z},
        )
        ctx.robot.close_gripper_jaw()

    def _dump_prize():
        """Moves the gripper to the `Prize Chute` and lets go of whatever it has (or has not!)."""
        # NOTE: Weirdly enough, the WasteChute location isn't considered a valid `Location` type
        # todo(chb, 2026-11-13): Make a legit JIRA ticket about that one - thats a real bug
        ctx.robot.move_to(
            mount=Mount.EXTENSION, 
            destination=Location(
                point=Point(x=392.0, y=43.0, z=prize_chute.height + 20.0),
                labware=None,
            )
        )
        ctx.robot.open_gripper_jaw()

    # Gameplay Logic
    ctx.pause(msg="Begin Playing Crane Game")
    # Gameplay is timer based, players have until time expires to position the gantry
    start_time = time.monotonic()
    while time.monotonic() - start_time <= PLAY_TIMER:
        # Process user inputs to move the gantry around
        if len(user_input_queue) > 0:
            last_input = user_input_queue.pop()
            direction = last_input[0]
            multiplier = last_input[1]
            match direction:
                case InputDirections.TOWARDS_REAR:
                    ctx.robot.move_axes_relative(
                        axis_map = {"Y": MIN_MOVE_DISTANCE * multiplier}
                    )
                case InputDirections.TOWARDS_FRONT:
                    ctx.robot.move_axes_relative(
                        axis_map = {"Y": (MIN_MOVE_DISTANCE * -1) * multiplier}
                    )
                case InputDirections.RIGHT:
                    ctx.robot.move_axes_relative(
                        axis_map = {"X": MIN_MOVE_DISTANCE * multiplier}
                    )
                case InputDirections.LEFT:
                    ctx.robot.move_axes_relative(
                        axis_map = {"X": (MIN_MOVE_DISTANCE * -1) * multiplier}
                    )
                case InputDirections.REAR_LEFT:
                    ctx.robot.move_axes_relative(
                        axis_map = {"Y": MIN_MOVE_DISTANCE * multiplier, "X": (MIN_MOVE_DISTANCE * -1) * multiplier}
                    )
                case InputDirections.REAR_RIGHT:
                    ctx.robot.move_axes_relative(
                        axis_map = {"Y": MIN_MOVE_DISTANCE * multiplier, "X": MIN_MOVE_DISTANCE * multiplier}
                    )
                case InputDirections.FRONT_LEFT:
                    ctx.robot.move_axes_relative(
                        axis_map = {"Y": (MIN_MOVE_DISTANCE * -1) * multiplier, "X": (MIN_MOVE_DISTANCE * -1) * multiplier}
                    )
                case InputDirections.FRONT_RIGHT:
                    ctx.robot.move_axes_relative(
                        axis_map = {"Y": (MIN_MOVE_DISTANCE * -1) * multiplier, "X": MIN_MOVE_DISTANCE * multiplier}
                    )
        
        await asyncio.sleep(INPUT_SAMPLE_RATE)

    terminate_signal.set()
    # Game timer has expired - attempt to grab and return a prize
    _grab_prize()
    _dump_prize()

    ctx.comment("GAME OVER!!!! Hope you got something!")


def run(ctx: ProtocolContext):
    # NOTE: The logic for this protocol is split between two tasks as follows:
    # In the Input Sampler Task we collect inputs and post them to the shared input queue.
    # The queue is to be cleared at all times, so only the freshest inputs remain.
    # In the game logic we do a few things:
    # 1. Assert that the player wants to start by "Unpausing"
    # 2. During play, move the gripper through a number of samples until play time expires
    # 3. When time is up, the gripper will:
    #    (1). Move down to near-deck level and execute `close_gripper_jaw`
    #    (2). Retract fully
    #    (3). Return to the Waste Chute (Prize Chute) and execute `open_gripper_jaw`
    # 4. At the end of the "Game" the protocol will cleanly teardown

    # NOTE: On Analysis: This protocol will take at least as long to analyze as the PLAY_TIMER length.

    # Spin up the Input Sampler and Protocol Actions tasks
    async def runner():
        terminate_signal = asyncio.Event()
        async with asyncio.TaskGroup() as tg:
            t1 = tg.create_task(input_sampler_task(terminate_signal))
            t2 = tg.create_task(protocol_actions_task(ctx, terminate_signal))
        return t1.result(), t2.result()

    asyncio.run(runner())