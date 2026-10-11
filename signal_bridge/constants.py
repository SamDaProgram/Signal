"""Shared protocol limits, action fields, and project paths."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STALE_SECONDS = 3
PROTOCOLS = {'artnet': ('Art-Net', 6454, 0, 32767), 'e131': ('E1.31 / sACN', 5568, 1, 63999),
             'ddp': ('DDP', 4048, 0, 255)}
DDP_CHANNELS = 256 * 512


ACTION_FIELDS = {
    'osc': ('host', 'port', 'address'),
    'midi': ('midi_port', 'midi_type', 'midi_channel', 'midi_number', 'midi_value'),
    'script': ('script_path', 'script_args', 'script_cwd', 'script_timeout', 'script_fields', 'script_output'),
    'sequence': ('steps', 'sequence_on_error', 'sequence_source', 'sequence_direction'),
    'fade': ('host', 'port', 'address', 'fade_keyframes', 'fade_type', 'fade_curve', 'fade_rate', 'fade_range', 'fade_input_display'),
}
