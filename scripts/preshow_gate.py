#!/usr/bin/env python3
"""Signal template: request FPP stop-preshow followed by fade when preshow is false."""
import json
import os
from urllib.request import Request, urlopen
from urllib.parse import urlsplit


def check_preshow(fields):
    url = fields['preshow_url']
    if urlsplit(url).scheme not in ('http', 'https'):
        raise ValueError('Preshow URL must use HTTP or HTTPS.')
    with urlopen(Request(url, headers={'Cache-Control': 'no-cache', 'Accept': 'application/json'}), timeout=3) as response:
        body = response.read(65537)
    if len(body) > 65536:
        raise ValueError('Preshow response is too large.')
    data = json.loads(body)
    if not isinstance(data, dict) or type(data.get('preshow')) is not bool:
        raise ValueError('Expected a JSON boolean named preshow.')
    return data['preshow']


def main():
    fields = json.loads(os.environ['SIGNAL_SCRIPT_FIELDS'])
    target = fields.get('fpp')
    selected = json.loads(os.environ['SIGNAL_SCRIPT_PRESETS'])
    if not target or target not in selected:
        raise ValueError('Select an FPP output preset.')
    actions = []
    if not check_preshow(fields):
        actions = [
            dict(preset_id=target, address=fields.get('fpp_stop_address', '/stoppre'), args=[]),
            dict(preset_id=target, address=fields.get('fpp_fade_address', '/preshow/fade'), args=[])]
    print(json.dumps({'preset_actions': actions}))


if __name__ == '__main__':
    main()
