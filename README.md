### Holiday 2026
For the 2026 end of year, the R&D team has decided to build a crane game out of an Opentrons Flex!
This protocol will require an Opentrons Flex Gripper and an Opentrons Waste Chute!
It is *highly* recommended to fill the deck with prizes to grab (presumably not labware!)

#### Input System
TBD !

### Game Details
The crane game begins when the Player loads the protocol, and selects "Resume"! After that, they'll have XX seconds to try and line up the gripper with a prize!
The gripper will descend, attempt to grasp an object, and then dump said object out of the waste chute!

### Holiday 2025

With the launch of a new dynamic pipetting feature in 8.8 of Opentrons App we can now "draw" using the pipette. 
What better way to take advantage of this than to decorate some cookies with royal icing.

This protocol uses several 50ml test tubes in a rack to hold differnt color icings which can be "drawn" onto a Ansi Standard slot size cookie

## How to use Image to Flex Protocol Creator
The attached python app works by letting a user draw in a variety of colors, and exporting a waypoint csv file similar to the example file.

Required packages:
`pip install pygame`

To run:
`python3 image_to_flex_protocol_creator.py`
