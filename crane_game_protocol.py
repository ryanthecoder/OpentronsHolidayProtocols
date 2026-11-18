from opentrons.protocol_api import ProtocolContext
from opentrons.types import Mount, Point, Location
from collections import deque
import enum
import time
import asyncio


metadata = {
    "protocolName": "Crane Game Protocol",
    "description": "Opentrons Crane Game to be played with (TBD) input system!",
    "author": "Opentrons R&D Team",
}

requirements = {
	"robotType": "Flex",
	"apiLevel": "2.31"
}

class InputDirections(enum.Enum):
    TOWARDS_REAR = 1
    TOWARDS_FRONT = 2
    LEFT = 3
    RIGHT = 4

# Minimum distance (mm) to move the gantry along the X and Y axes based on user input
# NOTE: Some napkin-math:  5mm @ 10 samples/sec means we'd cross all deck slots
# right to left (350mm~) in 7 seconds~ (not accounting for command exec time), if
# that feels too fast or slow we can adjust!
MIN_MOVE_DISTANCE = 5.0

# Sample rate (S) for user inputs
INPUT_SAMPLE_RATE = 0.1 

# Maximum time (S) the user has to move the gantry around before the gripper tries to "Grab"
PLAY_TIMER = 30

# Global queue of user inputs for processing
user_input_queue: deque[InputDirections] = deque()

# Set of test inputs for debugging the script - DISBALE FOR LIVE RUNS
USE_TEST_INPUTS = True
TEST_INPUTS = [
    InputDirections.TOWARDS_FRONT,
    InputDirections.TOWARDS_FRONT,
    InputDirections.TOWARDS_FRONT,
    InputDirections.TOWARDS_FRONT,
    InputDirections.TOWARDS_FRONT,
    InputDirections.LEFT,
    InputDirections.LEFT,
    InputDirections.LEFT,
    InputDirections.LEFT,
    InputDirections.LEFT,
    InputDirections.TOWARDS_REAR,
    InputDirections.RIGHT
]

async def input_sampler_task():
    """This is the task used to sample user input, input is collected through -TBD-."""
    # TODO this whole task is TBD
    # We might sample input here through a controller driver
    # Or through a web socket or endpointed called from an app (or the ODD even)
    # We could even take input via python requests through Pyro from other devices (like a laptop)

    # Logic wise this task will need to queue up inputs and remove stale ones when it can
    
    start_time = time.monotonic()
    while time.monotonic() - start_time <= PLAY_TIMER:
        # TODO: DETERMINE REAL SAMPLE METHOD

        if USE_TEST_INPUTS:
            # Test the script by moving the gripper several times then grabbing and dropping
            if len(TEST_INPUTS) > 0:
                user_input_queue.append(TEST_INPUTS.pop(0))

        # Sample inputs 10 times a second (bump down time if we need higher fidelity)
        await asyncio.sleep(INPUT_SAMPLE_RATE)


async def protocol_actions_task(ctx: ProtocolContext):
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
            latest_input = user_input_queue.pop()
            match latest_input:
                case InputDirections.TOWARDS_REAR:
                    ctx.robot.move_axes_relative(
                        axis_map = {"Y": MIN_MOVE_DISTANCE}
                    )
                case InputDirections.TOWARDS_FRONT:
                    ctx.robot.move_axes_relative(
                        axis_map = {"Y": MIN_MOVE_DISTANCE * -1}
                    )
                case InputDirections.RIGHT:
                    ctx.robot.move_axes_relative(
                        axis_map = {"X": MIN_MOVE_DISTANCE}
                    )
                case InputDirections.LEFT:
                    ctx.robot.move_axes_relative(
                        axis_map = {"X": MIN_MOVE_DISTANCE * -1}
                    )
        await asyncio.sleep(INPUT_SAMPLE_RATE)
    
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
        async with asyncio.TaskGroup() as tg:
            t1 = tg.create_task(input_sampler_task())
            t2 = tg.create_task(protocol_actions_task(ctx))
        return t1.result(), t2.result()

    asyncio.run(runner())