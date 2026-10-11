#!/usr/bin/env python3
"""Signal launcher and compatibility imports. Implementation lives in signal_bridge/."""

from signal_bridge.actions import midi_module
from signal_bridge.bridge import Bridge
from signal_bridge.cli import main
from signal_bridge.constants import ACTION_FIELDS, DDP_CHANNELS, PROTOCOLS, ROOT, STALE_SECONDS
from signal_bridge.http import make_handler
from signal_bridge.network import MulticastSubscriptions, network_addresses, receiver
from signal_bridge.protocols import encode_osc, osc_string, parse_artdmx, parse_ddp, parse_e131
from signal_bridge.shows import ShowFiles
from signal_bridge.validation import (
    condition_bounds,
    fade_args,
    follows_dmx,
    linked_rule,
    preshow_template,
    select_rule,
    validate_fade,
    validate_input,
    validate_preset,
    validate_rule,
    validate_script_fields,
)
from signal_bridge.values import integer, text_value


if __name__ == "__main__":
    main()
