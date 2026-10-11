import copy
from pathlib import Path
import socket
import tempfile
import time
import unittest
from app import ShowFiles, validate_rule, validate_preset, fade_args, encode_osc, select_rule
from test_bridge import packet, sacn_packet, ddp_packet


class FaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ShowFiles(Path(self.temp.name) / 'shows')
        self.bridge = self.store.bridge
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.settimeout(.3)
        self.raw = dict(name='Brightness', action='fade', host='127.0.0.1',
                        port=self.listener.getsockname()[1], address='/brightness', fade_rate=60,
                        fade_keyframes=[dict(level=0, values=[0]), dict(level=255, values=[1])])

    def tearDown(self):
        self.bridge.close()
        self.listener.close()
        self.temp.cleanup()

    def recv(self):
        return self.listener.recv(8192)

    def none(self):
        self.listener.settimeout(.06)
        with self.assertRaises(socket.timeout):
            self.recv()
        self.listener.settimeout(.3)

    def cue(self, **options):
        step = dict(name='Start', action='osc', host='127.0.0.1', port=self.raw['port'], args=[])
        return self.bridge.upsert(dict(name='Cues', action='sequence', sequence_source='dmx',
            steps=[dict(step, delay_ms=0, address='/zero'), dict(step, delay_ms=128, address='/mid'),
                   dict(step, delay_ms=127, address='/end')], **options))

    def test_interpolation_scalar_rgb_endpoints_and_curves(self):
        rule = validate_rule(self.raw)
        self.assertAlmostEqual(fade_args(rule, 128)[0]['value'], 128/255)
        rule = validate_rule(dict(self.raw, fade_keyframes=[dict(level=20, values=[1,0,0]), dict(level=220, values=[0,0,1])]))
        self.assertEqual([a['value'] for a in fade_args(rule, 120)], [.5,0,.5])
        self.assertEqual([a['value'] for a in fade_args(rule, 0)], [1,0,0])
        self.assertEqual([a['value'] for a in fade_args(rule, 255)], [0,0,1])
        rule['fade_curve'] = 'smooth'
        self.assertAlmostEqual(fade_args(rule, 70)[2]['value'], .15625)
        rule['fade_curve'] = 'hold'
        self.assertEqual(fade_args(rule, 219)[0]['value'], 1)
        self.assertEqual(fade_args(rule, 220)[0]['value'], 0)

    def test_integer_rounding_and_descending_values(self):
        rule = validate_rule(dict(self.raw, fade_type='i', fade_keyframes=[dict(level=0,values=[255,-10]), dict(level=255,values=[0,10])]))
        self.assertEqual(fade_args(rule,128), [dict(type='i',value=127),dict(type='i',value=0)])

    def test_real_dmx_input_sends_osc_and_deduplicates(self):
        rule = self.bridge.upsert(self.raw)
        self.bridge.receive(packet(128), 'test')
        self.assertEqual(self.recv(), encode_osc('/brightness', fade_args(rule,128)))
        self.bridge.receive(packet(128), 'test')
        self.none()
        self.assertEqual(self.bridge.sent,1)
        self.assertEqual(rule['threshold'],0)
        self.assertTrue(self.bridge.snapshot(0)['rules'][0]['active'])

    def test_rate_limit_coalesces_and_delivers_last_value_without_more_packets(self):
        rule = self.bridge.upsert(dict(self.raw,fade_rate=10))
        self.bridge.receive(packet(0), 'test');self.recv()
        for level in [40,128,255]:
            self.bridge.receive(packet(level),'test')
        self.assertEqual(self.recv(),encode_osc('/brightness',fade_args(rule,255)))
        self.none()
        self.assertEqual(self.bridge.sent,2)

    def test_return_to_last_sent_value_cancels_pending_update(self):
        self.bridge.upsert(dict(self.raw,fade_rate=10))
        self.bridge.receive(packet(0),'test');self.recv()
        self.bridge.receive(packet(255),'test')
        self.bridge.receive(packet(0),'test')
        self.assertFalse(self.bridge.fade_pending)
        self.none()

    def test_pause_edit_delete_and_show_change_cancel_pending(self):
        for operation in ['pause','edit','delete','show']:
            with self.subTest(operation=operation):
                self.bridge.set_runtime({'armed':True})
                rule=self.bridge.upsert(dict(self.raw,fade_rate=1))
                self.bridge.receive(packet(0),'test');self.recv()
                self.bridge.receive(packet(255),'test')
                generation=self.bridge.follow_generation
                if operation=='pause':self.bridge.set_runtime({'armed':False})
                elif operation=='edit':self.bridge.upsert(dict(rule,address='/changed'),rule['id'])
                elif operation=='delete':self.bridge.delete(rule['id'])
                else:self.store.change(dict(operation='new',name='other.json'))
                self.bridge.flush_fades(generation)
                self.assertFalse(self.bridge.fade_pending)
                self.none()
                if operation in ('pause','edit'):self.bridge.delete(rule['id'])

    def test_resume_sends_current_fade_without_replaying_old_values(self):
        rule=self.bridge.upsert(self.raw)
        self.bridge.receive(packet(0),'test');self.recv()
        self.bridge.set_runtime({'armed':False})
        self.bridge.receive(packet(100),'test');self.none()
        self.bridge.set_runtime({'armed':True})
        self.bridge.receive(packet(100),'test')
        self.assertEqual(self.recv(),encode_osc('/brightness',fade_args(rule,100)))

    def test_followers_do_not_compete_with_threshold_triggers(self):
        self.bridge.upsert(self.raw)
        trigger=self.bridge.upsert(dict(name='Go',host='127.0.0.1',port=self.raw['port'],address='/go',args=[]))
        self.assertEqual(select_rule(self.bridge.rules,128)['id'],trigger['id'])
        self.bridge.receive(packet(128),'test')
        self.assertTrue(self.recv().startswith(b'/brightness'))
        self.assertTrue(self.recv().startswith(b'/go'))

    def test_fade_presets_and_show_roundtrip(self):
        preset=self.bridge.upsert_preset(self.raw)
        rule=self.bridge.upsert(dict(name='Linked',preset_id=preset['id']))
        updated=copy.deepcopy(preset);updated['fade_keyframes'][1]['values']=[10];updated['fade_range']={'min':0,'max':10}
        self.bridge.upsert_preset(updated,preset['id'])
        self.assertEqual(self.bridge.rules[0]['fade_keyframes'][1]['values'],[10])
        document=self.bridge.document()
        self.store.change(dict(operation='upload',name='roundtrip.json',show=document))
        self.assertEqual(self.bridge.document(),document)
        self.assertEqual(self.bridge.rules[0]['id'],rule['id'])

    def test_sacn_and_sparse_ddp_follow_only_updated_channel(self):
        rule=self.bridge.upsert(dict(self.raw,universe=1))
        self.bridge.set_input({'protocol':'e131'})
        self.bridge.receive(sacn_packet(100),'test','e131')
        self.assertEqual(self.recv(),encode_osc('/brightness',fade_args(rule,100)))
        self.bridge.upsert(dict(self.raw,universe=0),rule['id'])
        self.bridge.set_input({'protocol':'ddp'})
        self.bridge.receive(ddp_packet([128]),'test','ddp');self.recv()
        self.bridge.receive(ddp_packet([255],offset=1),'test','ddp');self.none()

    def test_cues_baseline_forward_jump_reverse_rearm_and_duplicates(self):
        self.cue()
        self.bridge.receive(packet(0),'test');self.none()
        self.bridge.receive(packet(255),'test')
        self.assertEqual([self.recv().split(b'\0')[0] for _ in range(3)],[b'/zero',b'/mid',b'/end'])
        self.bridge.receive(packet(255),'test');self.none()
        self.bridge.receive(packet(100),'test');self.none()
        self.bridge.receive(packet(128),'test');self.assertTrue(self.recv().startswith(b'/mid'))

    def test_cues_both_directions_and_stale_baseline(self):
        self.cue(sequence_direction='both')
        self.bridge.receive(packet(255),'test');self.none()
        self.bridge.receive(packet(0),'test')
        self.assertEqual([self.recv().split(b'\0')[0] for _ in range(2)],[b'/mid',b'/zero'])
        self.bridge.universes[0]['slot_seen'][0]-=4
        self.bridge.receive(packet(255),'test');self.none()

    def test_cues_resume_and_edit_do_not_replay(self):
        rule=self.cue()
        self.bridge.receive(packet(0),'test')
        self.bridge.set_runtime({'armed':False})
        self.bridge.receive(packet(255),'test')
        self.bridge.set_runtime({'armed':True})
        self.bridge.receive(packet(255),'test');self.none()
        self.bridge.upsert(rule,rule['id'])
        self.bridge.receive(packet(255),'test');self.none()

    def test_validation_rejects_bad_frames_and_nesting(self):
        variants=[{'fade_keyframes':[]},{'fade_keyframes':[dict(level=0,values=[0])]*2},
                  {'fade_keyframes':[dict(level=0,values=[0]),dict(level=255,values=[1,2])]},
                  {'fade_keyframes':[dict(level=0,values=[False]),dict(level=255,values=[1])]},
                  {'fade_keyframes':[dict(level=0,values=[0]),dict(level=255,values=[float('nan')])]},
                  {'fade_type':'i','fade_keyframes':[dict(level=0,values=[0]),dict(level=255,values=[.5])]},
                  {'fade_rate':0},{'fade_rate':61},{'fade_curve':'bad'},{'fade_type':'s'}]
        for variant in variants:
            with self.subTest(variant=variant),self.assertRaises(ValueError):validate_rule(dict(self.raw,**variant))
        with self.assertRaises(ValueError):validate_rule(dict(name='nested',action='sequence',steps=[self.raw]))
        with self.assertRaises(ValueError):validate_preset(dict(self.raw,address=''))
        with self.assertRaises(ValueError):validate_rule(dict(name='empty clock',action='sequence',sequence_source='dmx',steps=[dict(name='step',action='osc')]))

    def test_manual_fade_test_requires_live_input(self):
        rule=self.bridge.upsert(self.raw)
        self.assertEqual(self.bridge.test(rule['id']),'error')
        self.bridge.receive(packet(255),'test');self.recv()
        self.assertEqual(self.bridge.test(rule['id']),'sent')
        self.assertEqual(self.recv(),encode_osc('/brightness',fade_args(rule,255)))

    def test_unrelated_edit_and_monitor_change_keep_pending_final_value(self):
        rule=self.bridge.upsert(dict(self.raw,fade_rate=10))
        self.bridge.receive(packet(0),'test');self.recv()
        self.bridge.receive(packet(255),'test')
        self.bridge.upsert(dict(name='Unrelated',channel=2))
        self.bridge.set_input(dict(protocol='artnet',monitor_universe=1))
        self.assertEqual(self.recv(),encode_osc('/brightness',fade_args(rule,255)))

    def test_multiple_fades_share_channel_without_competing(self):
        self.bridge.upsert(self.raw)
        self.bridge.upsert(dict(self.raw,name='Second',address='/second'))
        self.bridge.receive(packet(128),'test')
        self.assertEqual({self.recv().split(b'\0')[0] for _ in range(2)},{b'/brightness',b'/second'})

    def test_dmx_cue_errors_stop_or_continue_crossing_batch(self):
        for behavior,expected in [('stop',False),('continue',True)]:
            rule=self.cue(sequence_on_error=behavior)
            rule=copy.deepcopy(rule)
            rule['steps'][0]=dict(name='Missing',action='script',delay_ms=0,script_path='/missing/program')
            self.bridge.upsert(rule,rule['id'])
            self.bridge.receive(packet(0),'test')
            self.bridge.receive(packet(255),'test')
            if expected:
                self.assertTrue(self.recv().startswith(b'/mid'))
                self.assertTrue(self.recv().startswith(b'/end'))
            else:self.none()
            self.bridge.delete(rule['id'])

    def test_fade_script_asset_is_served(self):
        from http.server import ThreadingHTTPServer
        from urllib.request import urlopen
        import threading
        from app import make_handler
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.bridge))
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            with urlopen(f'http://127.0.0.1:{server.server_port}/faders.js') as response:
                self.assertEqual(response.status,200)
                self.assertIn(b'initializeFadeUI',response.read())
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_scale_settings_survive_preset_and_show_roundtrip(self):
        config=dict(self.raw,fade_range={'min':0,'max':255},fade_input_display='percent',
                    fade_keyframes=[{'level':0,'values':[0]},{'level':255,'values':[255]}])
        preset=self.bridge.upsert_preset(config)
        rule=self.bridge.upsert(dict(name='Scaled',preset_id=preset['id']))
        self.assertEqual(rule['fade_range'],{'min':0,'max':255})
        self.assertEqual(rule['fade_input_display'],'percent')
        self.bridge.receive(packet(128),'test')
        self.assertEqual(self.recv(),encode_osc('/brightness',[{'type':'f','value':128}]))
        document=self.bridge.document()
        self.store.change(dict(operation='upload',name='scaled.json',show=document))
        self.assertEqual(self.bridge.document(),document)

    def test_legacy_fades_infer_range_without_changing_values(self):
        raw=dict(self.raw,fade_keyframes=[{'level':0,'values':[-10]},{'level':255,'values':[100]}])
        result=validate_rule(raw)
        self.assertEqual(result['fade_range'],{'min':-10,'max':100})
        self.assertEqual(result['fade_input_display'],'dmx')
        self.assertEqual(result['fade_keyframes'],raw['fade_keyframes'])

    def test_output_range_validation(self):
        for bounds in [None,{}, {'min':0,'max':0},{'min':2,'max':1},
                       {'min':False,'max':1},{'min':0,'max':float('nan')},
                       {'min':0,'max':float('inf')},{'min':0,'max':10**400},
                       {'min':0,'max':.5}]:
            with self.subTest(bounds=bounds),self.assertRaises(ValueError):
                validate_rule(dict(self.raw,fade_range=bounds))
        with self.assertRaises(ValueError):validate_rule(dict(self.raw,fade_input_display='bad'))
