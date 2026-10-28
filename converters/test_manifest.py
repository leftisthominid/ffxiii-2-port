import tempfile
import unittest
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch
from x2dlc import manifest, wpd, archive
from x2dlc import __main__ as cli


class Index:
    def __init__(self, entries, blobs=None):
        self.files={path:(arc,0,1,1,path.replace('.trb','.win32.trb').replace('.xgr','.win32.xgr'),code,16)
                    for code,(path,arc) in enumerate(entries.items(),1)}
        self.blobs=blobs or {}
        self.sources=[('synthetic base/Steam index',len(entries))]
    def read(self,key): return self.blobs.get(key)
    def close(self): pass


class SteamInventory(unittest.TestCase):
    def test_build_install_and_failed_rebuild_need_no_xbox_dlc_metadata(self):
        path='sys/dlc/key/key00000000.dat'
        S=Index({path:'white_imgu.win32.bin'},{path:b'Steam content'})
        doc=manifest.build(S,Index({}))
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);work=root/'work';user=root/'userdata'
            manifest.save(doc,work)
            args=['x2dlc','build','--work',str(work),'--out',str(root/'packages'),
                  '--userdata',str(user),'--install','--serial']
            def build():
                with patch.object(cli.sys,'argv',args),patch.object(cli.learn,'load',return_value={}), \
                     patch.object(cli,'open_steam',return_value=S),contextlib.redirect_stdout(io.StringIO()):
                    return cli.main()
            self.assertEqual(build(),0)
            installed=user/'0000000000000000/53510806/00000002/XIII2SteamDLC0000071'
            white=installed/'white_p0000071img_u.x360.bin'
            before=white.read_bytes()
            self.assertTrue(before)
            S.blobs[path]=None
            self.assertEqual(build(),1)
            self.assertEqual(white.read_bytes(),before)
            self.assertFalse(json.loads((work/'build_report.json').read_text())['complete'])

    def test_action_cinematics_and_backgrounds_are_not_limited_by_root_names(self):
        paths={'ac/schedule/action/a00_us.white.bin':'white_scru.win32.bin',
               'bg/loc161/m000/block000.trb':'white_imgu.win32.bin'}
        doc=manifest.build(Index(paths),Index({}))
        actual={f[2] for p in doc['packs'].values() for l in p['lists'] for f in l['files']}
        self.assertEqual(actual,{'ac/schedule/action/a00_us.white.bin','bg/loc161/m000/block000.x360.trb'})

    def test_existing_episode_battle_pack_is_updated_from_global_steam_archive(self):
        path='btscene/pack/clb/z0161.bin'
        doc=manifest.build(Index({path:'white_imgu.win32.bin'}),Index({path:'base.bin'}))
        selected={f[2]for p in doc['packs'].values()for group in p['lists']for f in group['files']}
        self.assertEqual(selected,{path})

    def test_effect_bank_with_new_members_keeps_updated_existing_bank_and_sounds(self):
        new='sound/pack/8540/tech_new.scd';old='sound/pack/8540/tech_old.scd';bank='sound/pack/8540/tech.wpd'
        voice='sound/pack/usa/episode/new.scd';base_voice='sound/pack/usa/episode/old.scd'
        paths={p:'white_imgu.win32.bin'for p in (new,old,bank,voice,base_voice)}
        doc=manifest.build(Index(paths),Index({p:'base.bin'for p in (old,bank,base_voice)}))
        selected={f[2]for p in doc['packs'].values()for group in p['lists']for f in group['files']}
        self.assertEqual(selected,{new,old,bank,voice})
        self.assertEqual(doc['provenance']['reasons']['matching Steam sound-bank files'],2)

    def test_inventory_uses_new_assets_regions_and_new_ui_definitions(self):
        paths={'chr/mon/m001/m001.trb':'white_imgu.win32.bin',
               'chr/mon/m999/m999.trb':'white_imgu.win32.bin',
               'scene/lay/scene00131/scene00131.xwp':'white_z0130u_img.win32.bin',
               'gui/scene/s00131.xgr':'white_scru.win32.bin',
               'sound/etc/filelist_sound_pack_fixed.bin':'white_imgu.win32.bin'}
        old=wpd.write([(b'old',b'ykd',b'old UI')]);new=wpd.write([(b'old',b'ykd',b'old UI'),(b'new',b'ykd',b'new UI')])
        S=Index(paths,{'gui/scene/s00131.xgr':new})
        X=Index({k:v for k,v in paths.items() if k!='chr/mon/m999/m999.trb'},{'gui/scene/s00131.xgr':old})
        doc=manifest.build(S,X)
        files={f[2] for pack in doc['packs'].values() for group in pack['lists'] for f in group['files']}
        self.assertEqual(files,{'chr/mon/m999/m999.x360.trb','scene/lay/scene00131/scene00131.xwp','gui/scene/s00131.x360.xgr'})
        self.assertFalse(doc['provenance']['xbox_dlc_used'])
        for ident in ('0000021','0000023'):
            self.assertFalse(any(group['files'] for group in doc['packs'][ident]['lists']))

    def test_authored_headers_roundtrip_without_reference_lists(self):
        doc=manifest.build(Index({'chr/mon/m999/m999.trb':'white_imgu.win32.bin'}),Index({}))
        for pack in doc['packs'].values():
            for group in pack['lists']:
                entries=[(code,kind,0,3,3,path) for code,kind,path in group['files']]
                raw=archive.encrypt(archive.build_plain(bytes.fromhex(group['header']),entries))
                self.assertEqual(archive.read_list(raw)[1],entries)
        with tempfile.TemporaryDirectory() as work:
            manifest.save(doc,work)
            self.assertEqual(manifest.load(work),doc['packs'])

    def test_updated_ui_keeps_its_matching_steam_texture_even_when_base_has_one(self):
        gui='gui/scene/s00131.xgr';img='gui/scene/s00131.imgb'
        old=wpd.write([(b'old',b'ykd',b'old UI')])
        new=wpd.write([(b'old',b'ykd',b'old UI'),(b'new',b'ykd',b'new UI')])
        paths={gui:'white_scru.win32.bin',img:'white_imgu.win32.bin'}
        doc=manifest.build(Index(paths,{gui:new}),Index(paths,{gui:old}))
        groups=doc['packs']['0000071']['lists']
        selected={f[2]:group['kind'] for group in groups for f in group['files']}
        self.assertEqual(selected,{gui.replace('.xgr','.win32.xgr').replace('.win32.','.x360.'):'scr',img:'scr'})
        self.assertEqual(doc['provenance']['reasons']['matching Steam texture/header dependency'],1)

    def test_new_texture_brings_native_owner_from_steam_and_shared_event_companion(self):
        for owner,image in [('chr/mon/m001/m001.trb','chr/mon/m001/m001.imgb'),
                            ('event/episode/b00_us.white.bin','event/episode/b00.white.imgb')]:
            with self.subTest(owner=owner):
                paths={owner:'white_imgu.win32.bin',image:'white_imgu.win32.bin'}
                doc=manifest.build(Index(paths),Index({owner:'base.bin'}))
                selected={manifest.gameindex.norm(f[2]) for p in doc['packs'].values() for g in p['lists'] for f in g['files']}
                self.assertEqual(selected,{owner,image})

    def test_incompatible_database_schema_keeps_native_base_data(self):
        def table(schema):return wpd.write([(b'!structitem',bytes(8),schema),(b'!!version',bytes(8),bytes(4))])
        path='db/resident/zonelist.wdb'
        doc=manifest.build(Index({path:'white_imgu.win32.bin'},{path:table(b'new')}),
                           Index({path:'base.bin'},{path:table(b'old')}))
        self.assertIn(path,doc['provenance']['native_database_schemas_kept'])
        self.assertEqual(doc['provenance']['selected'],0)

    def test_complete_steam_casino_schema_replaces_disc_placeholder(self):
        path='db/resident/csn_ch_spec.wdb'
        def table(schema,rows):
            return wpd.write([(b'!!sheetname',bytes(8),b'CasinoCharaSpec\0'),
                              (b'!structitem',bytes(8),schema),
                              (b'!!version',bytes(8),schema[:4])]+rows)
        pc=table(b'expanded NPC and dialogue fields',[(b'cb_npc000',bytes(8),b'full casino NPC')])
        base=table(b'old fields',[(b'dlc_pk_npc1',bytes(8),b'placeholder')])
        doc=manifest.build(Index({path:'white_imgu.win32.bin'},{path:pc}),Index({path:'base.bin'},{path:base}))
        actual={f[2]for pack in doc['packs'].values()for group in pack['lists']for f in group['files']}
        self.assertEqual(actual,{path})
        self.assertEqual(doc['provenance']['reasons']['Steam casino subsystem tables'],1)
        self.assertEqual(doc['provenance']['native_database_schemas_kept'],[])


if __name__=='__main__':unittest.main()
