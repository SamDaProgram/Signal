import json
import csv
import io
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from app import Bridge, encode_osc, make_handler, parse_artdmx, receiver, validate_rule, parse_e131, parse_ddp, MulticastSubscriptions, condition_bounds, select_rule


def packet(level, universe=0, count=512):
    return b'Art-Net\0' + struct.pack('<H', 0x5000) + struct.pack('>H', 14) + b'\0\0' + struct.pack('<H', universe) + struct.pack('>H', count) + bytes([level]) + bytes(count-1)


def sacn_packet(level=100, universe=1, options=0, count=512):
    data = bytearray(126 + count)
    data[:16] = b'\x00\x10\x00\x00ASC-E1.17\x00\x00\x00'
    for offset in (16, 38, 115):
        struct.pack_into('>H', data, offset, 0x7000 | (len(data) - offset))
    struct.pack_into('>I', data, 18, 4)
    struct.pack_into('>I', data, 40, 2)
    data[108], data[112] = 100, options
    struct.pack_into('>H', data, 113, universe)
    data[117:123] = b'\x02\xa1\x00\x00\x00\x01'
    struct.pack_into('>H', data, 123, count + 1)
    if count:
        data[126] = level
    return bytes(data)


def ddp_packet(values, offset=0, flags=0x41, kind=0x0b, destination=1):
    header = bytes([flags, 0, kind, destination]) + struct.pack('>IH', offset, len(values))
    if flags & 0x10:
        header += bytes(4)
    return header + bytes(values)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(Path(self.temp.name) / 'settings.json')
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.settimeout(.15)
        self.raw = dict(name='Playback', universe=0, channel=1, threshold=100, host='127.0.0.1',
                        port=self.listener.getsockname()[1], address='/go', args=[{'type':'i', 'value':1}], enabled=True)

    def tearDown(self):
        self.listener.close()
        self.bridge.output.close()
        self.temp.cleanup()

    def test_group_labels_persistence_validation_and_save_failure(self):
        self.bridge.upsert(self.raw)
        self.bridge.set_group_label(dict(universe=0, channel=1, label=' Playback '))
        self.bridge.set_input({'protocol': 'ddp'})
        self.bridge.upsert_preset(dict(name='Device', host='127.0.0.1', port=9000))
        restored = Bridge(self.bridge.path)
        try:
            self.assertEqual(restored.snapshot(0)['group_labels'], {'0:1': 'Playback'})
        finally:
            restored.output.close()
        for change in [dict(label=4), dict(label='x'*101), dict(label='a\nb'), dict(channel=0), dict(universe=-1)]:
            with self.assertRaises(ValueError):
                self.bridge.set_group_label(dict(dict(universe=0, channel=1, label='OK'), **change))
        path = self.bridge.path
        self.bridge.path = Path(self.temp.name)
        with self.assertRaises(OSError):
            self.bridge.set_group_label(dict(universe=0, channel=1, label='Changed'))
        self.assertEqual(self.bridge.group_labels, {'0:1': 'Playback'})
        self.bridge.path = path
        self.bridge.set_group_label(dict(universe=0, channel=1, label=''))
        self.assertEqual(self.bridge.group_labels, {})

    def test_channel_export_multiple_triggers_escaping_and_ddp(self):
        self.bridge.set_input({'protocol': 'ddp'})
        self.bridge.upsert(dict(self.raw, universe=1, name='Cue, "A"', operator='range', threshold_max=149))
        self.bridge.upsert(dict(self.raw, universe=1, name='=formula', enabled=False))
        self.bridge.set_group_label(dict(universe=1, channel=1, label='Stage, left'))
        rows = list(csv.DictReader(io.StringIO(self.bridge.export_channels().decode('utf-8-sig'))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['DDP absolute channel'], '513')
        self.assertEqual(rows[0]['Group label'], 'Stage, left')
        self.assertEqual(rows[0]['Trigger'], 'Cue, "A"')
        self.assertEqual(rows[0]['Condition'], '100–149 (inclusive)')
        self.assertEqual(json.loads(rows[0]['OSC arguments (JSON)']), self.raw['args'])
        self.assertEqual(rows[1]['Trigger'], "'=formula")
        self.assertEqual(rows[1]['Enabled'], 'No')
        self.assertEqual(self.bridge.sent, 0)

    def test_wire_encoding(self):
        self.assertEqual(encode_osc('/go', [{'type':'i','value':1}]), b'/go\0,i\0\0\0\0\0\1')
        result = encode_osc('/x', [{'type':'f','value':.5}, {'type':'s','value':'go'}, {'type':'T'}, {'type':'F'}])
        self.assertEqual(result, b'/x\0\0,fsTF\0\0\0'+struct.pack('>f',.5)+b'go\0\0')

    def test_parser_rejects_bad_packets(self):
        good=packet(100, 32767)
        self.assertEqual(parse_artdmx(good), (32767,[100]+[0]*511))
        for bad in [b'',good[:20],b'bad-data'+good[8:],good[:10]+b'\0\1'+good[12:],packet(1,count=3),good[:15]+b'\x80'+good[16:]]:
            self.assertIsNone(parse_artdmx(bad))

    def test_crossing_and_rearm(self):
        self.bridge.upsert(self.raw)
        for level in [0,99,100,101,255,100]:
            self.bridge.receive(packet(level),'127.0.0.1')
        self.assertEqual(self.listener.recv(1024), encode_osc('/go',self.raw['args']))
        with self.assertRaises(socket.timeout): self.listener.recv(1024)
        self.bridge.receive(packet(99),'127.0.0.1')
        self.bridge.receive(packet(100),'127.0.0.1')
        self.listener.recv(1024)
        self.assertEqual(self.bridge.sent,2)

    def test_stale_does_not_rearm_and_short_frame_does_not_invent_zero(self):
        self.raw['channel']=512
        self.bridge.upsert(self.raw)
        full=packet(0)[:-1]+bytes([100])
        self.bridge.receive(full,'127.0.0.1')
        self.listener.recv(1024)
        self.bridge.universes[0]['seen']-=10
        self.bridge.universes[0]['slot_seen']=[0]*512
        self.assertFalse(self.bridge.snapshot(0)['rules'][0]['live'])
        self.bridge.receive(packet(0,count=2),'127.0.0.1')
        self.assertIsNone(self.bridge.snapshot(0)['rules'][0]['level'])
        self.bridge.receive(full,'127.0.0.1')
        self.assertEqual(self.bridge.sent,1)

    def test_edit_baseline_disable_and_persistence(self):
        self.bridge.receive(packet(200),'127.0.0.1')
        rule=self.bridge.upsert(self.raw)
        self.bridge.receive(packet(201),'127.0.0.1')
        self.assertEqual(self.bridge.sent,0)
        self.bridge.upsert(dict(self.raw,enabled=False),rule['id'])
        self.bridge.receive(packet(0),'127.0.0.1')
        self.bridge.receive(packet(255),'127.0.0.1')
        self.assertEqual(self.bridge.sent,0)
        restored=Bridge(self.bridge.path)
        try: self.assertEqual(restored.rules,self.bridge.rules)
        finally: restored.output.close()
        self.bridge.delete(rule['id'])
        self.assertEqual(json.loads(self.bridge.path.read_text())['rules'],[])

    def test_validation(self):
        for change in [dict(channel=0),dict(channel=513),dict(threshold=256),dict(universe=-1),dict(port=0),dict(host='bad'),dict(address='go'),dict(enabled='yes'),dict(args=[{'type':'i','value':1.5}]),dict(args=[{'type':'f','value':float('nan')}])]:
            with self.assertRaises(ValueError): validate_rule(dict(self.raw,**change))

    def test_failed_save_keeps_live_rules(self):
        self.bridge.path=Path(self.temp.name) # cannot replace a directory
        with self.assertRaises(OSError): self.bridge.upsert(self.raw)
        self.assertEqual(self.bridge.rules,[])

    def test_sacn_parser_validation(self):
        good = sacn_packet(151, 63999)
        self.assertEqual(parse_e131(good), (63999, [151] + [0]*511))
        for bad in [good[:125], good[:-1], sacn_packet(universe=0), sacn_packet(universe=64000),
                    sacn_packet(options=0x80), sacn_packet(options=0x40), sacn_packet(options=0x20)]:
            self.assertIsNone(parse_e131(bad))
        for offset in (0, 4, 16, 18, 38, 40, 115, 117, 118, 120, 122, 125):
            bad=bytearray(good);bad[offset]^=1
            self.assertIsNone(parse_e131(bad), offset)
        bad=bytearray(good);bad[110]=1
        self.assertIsNone(parse_e131(bad))

    def test_ddp_parser_validation(self):
        self.assertEqual(parse_ddp(ddp_packet([10,20], 511)), (511,[10,20],True))
        self.assertEqual(parse_ddp(ddp_packet([9], flags=0x51)), (0,[9],True))
        for bad in [b'', ddp_packet([1])[:-1], ddp_packet([1],flags=0x81), ddp_packet([1],flags=0x43),
                    ddp_packet([1],flags=0x45),ddp_packet([1],flags=0x49),ddp_packet([1],kind=0x0c),
                    ddp_packet([1],destination=250),ddp_packet([1],offset=131072)]:
            self.assertIsNone(parse_ddp(bad))
        timed=bytearray(ddp_packet([1],flags=0x51));timed[13]=1
        self.assertIsNone(parse_ddp(timed))

    def test_one_selected_trigger_per_channel_all_protocols(self):
        for protocol in ('artnet','e131','ddp'):
            with self.subTest(protocol=protocol):
                for old in self.bridge.rules[:]: self.bridge.delete(old['id'])
                self.bridge.set_input({'protocol':protocol})
                self.bridge.universes.clear();self.bridge.active.clear();self.bridge.selected.clear()
                universe=1 if protocol=='e131' else 0
                base=dict(self.raw,universe=universe)
                self.bridge.upsert(dict(base,address='/lower'))
                self.bridge.upsert(dict(base,address='/tie'))
                self.bridge.upsert(dict(base,address='/higher',threshold=200))
                self.bridge.upsert(dict(base,address='/disabled',operator='eq',threshold=220,enabled=False))
                make={'artnet':packet,'e131':sacn_packet,'ddp':lambda level:ddp_packet([level])}[protocol]
                def receive(level): self.bridge.receive(make(level),'127.0.0.1',protocol)
                receive(0);receive(220) # Jump over all lower thresholds: only one send.
                self.assertEqual(self.listener.recv(1024),encode_osc('/higher',base['args']))
                receive(230);receive(220)
                with self.assertRaises(socket.timeout):self.listener.recv(1024)
                receive(150) # Select lower on the downward transition.
                self.assertEqual(self.listener.recv(1024),encode_osc('/lower',base['args']))
                receive(160)
                with self.assertRaises(socket.timeout):self.listener.recv(1024)
                receive(0);receive(150)
                self.assertEqual(self.listener.recv(1024),encode_osc('/lower',base['args']))
                with self.assertRaises(socket.timeout):self.listener.recv(1024)

    def test_comparison_boundaries(self):
        cases=[('gte',100,255),('gt',101,255),('lte',0,100),('lt',0,99),('eq',100,100),('range',100,149)]
        for operator,low,high in cases:
            rule=validate_rule(dict(self.raw,operator=operator,threshold_max=149))
            self.assertEqual(condition_bounds(rule),(low,high))
            for level in range(256):
                self.assertEqual(select_rule([rule],level) is not None,low<=level<=high,(operator,level))
        for change in [dict(operator='invalid'),dict(operator='lt',threshold=0),dict(operator='gt',threshold=255),
                       dict(operator='range',threshold=150,threshold_max=100),dict(threshold_max=256)]:
            with self.assertRaises(ValueError):validate_rule(dict(self.raw,**change))

    def test_ranges_select_only_one_and_equality_overrides_broad_match(self):
        low=self.bridge.upsert(dict(self.raw,operator='range',threshold=100,threshold_max=149,address='/low'))
        high=self.bridge.upsert(dict(self.raw,operator='range',threshold=150,threshold_max=199,address='/high'))
        exact=self.bridge.upsert(dict(self.raw,operator='eq',threshold=175,address='/exact'))
        for level,address in [(100,'/low'),(149,None),(150,'/high'),(175,'/exact'),(176,'/high'),(200,None),(150,'/high')]:
            self.bridge.receive(packet(level),'127.0.0.1')
            if address:self.assertEqual(self.listener.recv(1024),encode_osc(address,self.raw['args']))
            with self.assertRaises(socket.timeout):self.listener.recv(1024)
        self.assertTrue(self.bridge.active[high['id']])
        self.assertFalse(self.bridge.active[low['id']])
        self.assertFalse(self.bridge.active[exact['id']])

    def test_edit_delete_baseline_and_clear_log_do_not_retrigger(self):
        self.bridge.receive(packet(120),'127.0.0.1')
        broad=self.bridge.upsert(self.raw)
        exact=self.bridge.upsert(dict(self.raw,operator='eq',threshold=120))
        self.bridge.receive(packet(120),'127.0.0.1')
        self.assertEqual(self.bridge.sent,0)
        self.bridge.delete(exact['id'])
        self.bridge.receive(packet(120),'127.0.0.1')
        self.assertEqual(self.bridge.sent,0)
        self.bridge.receive(packet(0),'127.0.0.1')
        self.bridge.receive(packet(120),'127.0.0.1')
        self.listener.recv(1024)
        self.assertEqual(len(self.bridge.events),1)
        self.bridge.clear_log()
        self.assertEqual(list(self.bridge.events),[])
        self.bridge.receive(packet(120),'127.0.0.1')
        self.assertEqual(self.bridge.sent,1)
        self.assertTrue(self.bridge.active[broad['id']])

    def test_switch_ignores_other_protocol_and_persists_rules(self):
        rule=self.bridge.upsert(self.raw)
        self.bridge.set_input({'protocol':'ddp','monitor_universe':2})
        self.bridge.receive(packet(255),'127.0.0.1')
        self.assertEqual(self.bridge.packets,0)
        self.bridge.receive(ddp_packet([99]),'127.0.0.1','ddp')
        self.bridge.set_input({'protocol':'artnet'})
        self.assertEqual(self.bridge.universes,{})
        self.assertEqual(self.bridge.rules,[rule])
        restored=Bridge(self.bridge.path)
        try:
            self.assertEqual(restored.input,self.bridge.input)
            self.assertEqual(restored.rules,[rule])
        finally:restored.output.close()
        with self.assertRaises(ValueError):self.bridge.set_input({'protocol':'e131','monitor_universe':0})
        with self.assertRaises(ValueError):self.bridge.set_input({'protocol':'invalid'})

    def test_ddp_offsets_push_partial_updates_and_source_isolation(self):
        self.bridge.set_input({'protocol':'ddp'})
        self.bridge.upsert(dict(self.raw,universe=1))
        self.bridge.receive(ddp_packet([33,100],offset=511,flags=0x40),'source-a','ddp')
        self.assertEqual(self.bridge.universes,{})
        self.bridge.receive(ddp_packet([],destination=255),'source-b','ddp')
        self.assertEqual(self.bridge.sent,0)
        self.bridge.receive(ddp_packet([],destination=255),'source-a','ddp')
        self.listener.recv(1024)
        self.assertEqual(self.bridge.universes[0]['levels'][511],33)
        self.assertEqual(self.bridge.universes[1]['levels'][0],100)
        self.assertIsNone(self.bridge.universes[1]['levels'][1])
        self.bridge.universes[1]['slot_seen'][0]=0
        self.bridge.receive(ddp_packet([70],offset=513),'source-a','ddp')
        self.assertFalse(self.bridge.snapshot(1)['rules'][0]['live'])
        self.assertEqual(self.bridge.universes[1]['levels'][:2],[100,70])
        self.bridge.receive(ddp_packet([100],offset=512),'source-a','ddp')
        self.assertEqual(self.bridge.sent,1) # unrelated updates must never re-arm channel 1

    def test_legacy_settings_load_without_modification(self):
        rule=dict(self.raw,id='12345678-1234-1234-1234-123456789abc')
        original=json.dumps({'rules':[rule]})
        self.bridge.path.write_text(original)
        restored=Bridge(self.bridge.path)
        try:
            self.assertEqual(restored.input['protocol'],'artnet')
            self.assertEqual(restored.rules,[dict(rule,operator='gte',threshold_max=255)])
            self.assertEqual(restored.path.read_text(),original)
        finally:restored.output.close()

    def test_multicast_memberships_follow_rules_monitor_and_interface(self):
        from unittest.mock import Mock
        sock=Mock();groups=MulticastSubscriptions(sock)
        self.bridge.set_input({'protocol':'e131','monitor_universe':3})
        one=self.bridge.upsert(dict(self.raw,universe=1))
        self.bridge.upsert(dict(self.raw,universe=1))
        self.bridge.upsert(dict(self.raw,universe=9,enabled=False))
        groups.sync(self.bridge)
        self.assertEqual(groups.joined,{(1,'0.0.0.0'),(3,'0.0.0.0')})
        self.assertEqual(sock.setsockopt.call_count,2)
        self.bridge.set_input({'protocol':'e131','monitor_universe':4,'sacn_interface':'127.0.0.1'})
        groups.sync(self.bridge)
        self.assertEqual(groups.joined,{(1,'127.0.0.1'),(4,'127.0.0.1')})
        self.bridge.set_input({'protocol':'artnet'})
        groups.sync(self.bridge)
        self.assertEqual(groups.joined,set())

    def test_real_udp_receivers_for_sacn_and_ddp(self):
        for protocol in ('e131','ddp'):
            with self.subTest(protocol=protocol):
                for old in self.bridge.rules[:]:self.bridge.delete(old['id'])
                self.bridge.set_input({'protocol':protocol})
                self.bridge.upsert(dict(self.raw,universe=1 if protocol=='e131' else 0))
                udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);udp.bind(('127.0.0.1',0))
                stop=threading.Event();worker=threading.Thread(target=receiver,args=(self.bridge,udp,stop,protocol));worker.start()
                try:
                    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender:
                        sender.sendto(sacn_packet() if protocol=='e131' else ddp_packet([100]),udp.getsockname())
                    self.assertEqual(self.listener.recv(1024),encode_osc('/go',self.raw['args']))
                finally:stop.set();worker.join();udp.close()

    def test_device_presets_persist_without_retargeting_rules(self):
        original=self.bridge.upsert(self.raw)
        preset=self.bridge.upsert_preset(dict(name='Playback',host='127.0.0.1',port=9000,address='/cue'))
        self.assertEqual(self.bridge.snapshot(0)['presets'],[preset])
        self.bridge.set_input({'protocol':'ddp'})
        self.bridge.upsert(dict(self.raw,name='Another'))
        restored=Bridge(self.bridge.path)
        try:self.assertEqual(restored.presets,[preset])
        finally:restored.output.close()
        changed=self.bridge.upsert_preset(dict(preset,port=9001),preset['id'])
        self.assertEqual(changed['port'],9001)
        self.assertEqual(self.bridge.rules[0],original)
        self.bridge.delete_preset(preset['id'])
        self.assertEqual(self.bridge.presets,[])
        self.assertEqual(self.bridge.rules[0],original)
        self.assertEqual(json.loads(self.bridge.path.read_text())['presets'],[])

    def test_device_preset_validation_and_save_failure(self):
        raw=dict(name='Device',host='127.0.0.1',port=8000,address='')
        for change in [dict(name=''),dict(host='invalid'),dict(port=0),dict(port=65536),dict(address='bad'),dict(address=None)]:
            with self.assertRaises(ValueError):self.bridge.upsert_preset(dict(raw,**change))
        preset=self.bridge.upsert_preset(raw)
        with self.assertRaises(KeyError):self.bridge.delete_preset('missing')
        with self.assertRaises(KeyError):self.bridge.upsert_preset(raw,'missing')
        self.bridge.path=Path(self.temp.name)
        with self.assertRaises(OSError):self.bridge.upsert_preset(dict(raw,port=9000),preset['id'])
        self.assertEqual(self.bridge.presets,[preset])
        with self.assertRaises(OSError):self.bridge.delete_preset(preset['id'])
        self.assertEqual(self.bridge.presets,[preset])

    def test_http_and_udp_end_to_end(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.bridge))
        thread=threading.Thread(target=server.serve_forever);thread.start()
        udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);udp.bind(('127.0.0.1',0))
        stop=threading.Event();worker=threading.Thread(target=receiver,args=(self.bridge,udp,stop));worker.start()
        base=f'http://127.0.0.1:{server.server_port}'
        def request(path, method='GET', body=None, headers=None):
            req=Request(base+path, data=json.dumps(body).encode() if body else None, method=method,headers=headers or {'Content-Type':'application/json'})
            with urlopen(req,timeout=2) as response:return json.loads(response.read())
        try:
            preset=request('/api/presets','POST',dict(name='Test device',host='127.0.0.1',port=9000,address=''))
            request('/api/presets/'+preset['id'],'PUT',dict(preset,port=9001))
            self.assertEqual(request('/api/state')['presets'][0]['port'],9001)
            request('/api/presets/'+preset['id'],'DELETE')
            self.assertEqual(request('/api/state')['presets'],[])
            settings=request('/api/input','PUT',{'protocol':'ddp'})
            self.assertEqual(settings['protocol'],'ddp')
            self.assertEqual(request('/api/state')['input_port'],4048)
            request('/api/input','PUT',{'protocol':'artnet'})
            rule=request('/api/rules','POST',self.raw)
            request('/api/groups','PUT',dict(universe=0,channel=1,label='Playback'))
            self.assertEqual(request('/api/state')['group_labels'], {'0:1':'Playback'})
            with urlopen(base+'/api/export/channels',timeout=2) as response:
                self.assertIn('attachment;',response.headers['Content-Disposition'])
                rows=list(csv.DictReader(io.StringIO(response.read().decode('utf-8-sig'))))
                self.assertEqual(rows[0]['Group label'],'Playback')
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender: sender.sendto(packet(100),udp.getsockname())
            self.assertEqual(self.listener.recv(1024),encode_osc('/go',self.raw['args']))
            self.assertEqual(request('/api/state')['levels'][0],100)
            request('/api/rules/'+rule['id'],'PUT',dict(self.raw,threshold=200))
            self.assertEqual(request('/api/rules/'+rule['id']+'/test','POST')['status'],'sent')
            self.listener.recv(1024)
            self.assertTrue(request('/api/state')['events'])
            sent=request('/api/state')['sent']
            request('/api/log','DELETE')
            self.assertEqual(request('/api/state')['events'],[])
            self.assertEqual(request('/api/state')['sent'],sent)
            with self.assertRaises(HTTPError) as exc:request('/api/rules','POST',self.raw,{'Origin':'http://evil.example','Content-Type':'application/json'})
            self.assertEqual(exc.exception.code,403)
            exc.exception.close()
            request('/api/rules/'+rule['id'],'DELETE')
            self.assertEqual(request('/api/state')['rules'],[])
        finally:
            server.shutdown();server.server_close();thread.join();stop.set();worker.join();udp.close()


if __name__=='__main__':unittest.main()
