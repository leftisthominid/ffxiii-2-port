"""Build a Steam asset overlay; never read Xbox DLC lists or archives.

Steam integrates the episodes into its ordinary sys/udp/zone archives. Instead
of reconstructing Microsoft's original package ownership, place additional
Steam assets and applicable updates in one shared content overlay. The Xbox
disc/title-update index is only the boundary of assets already available.
"""
import collections
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import time
from . import convert, gameindex, wpd
from .movies import NAMES

VERSION = 2
REGIONS = {'z0070', 'z0130', 'z0160', 'z0161'}


def target_path(path):
    return path.replace('.win32.', '.x360.').replace('movie_win/', 'movie/', 1)


def internal(path):
    # These lists describe the PC's archives, which are not copied into the
    # port. The new content package has its own freshly generated lists.
    return ('filelist' in path.rsplit('/', 1)[-1].lower() or
            path.startswith('zone/white_') or path.startswith('sys/white_'))


def portable_database(pc, base):
    if pc[:4] != b'WPD\0' or base[:4] != b'WPD\0': return False
    a={n.rstrip(b'\0'):d for n,_,d in wpd.read(pc)}
    b={n.rstrip(b'\0'):d for n,_,d in wpd.read(base)}
    return b'!structitem' in a and a.get(b'!structitem') == b.get(b'!structitem') and a.get(b'!!version') == b.get(b'!!version')


def build(S, X, log=print):
    selected, reasons, metadata_kept = {}, collections.Counter(), []
    last=time.monotonic()
    for n,(key,entry) in enumerate(sorted(S.files.items())):
        if time.monotonic()-last>=15:
            log(f'  Steam inventory: {n}/{len(S.files)} paths, {len(selected)} overlay assets')
            last=time.monotonic()
        path=entry[4]
        if internal(path): continue
        if key.startswith('movie_win/'):
            # Base movies already use the PS3/360 movie installation. These
            # five episode movies are prepared from the indexed Steam data.
            if Path(key).stem not in NAMES: continue
            reason='Steam episode movie'
        elif key not in X.files:
            reason='additional Steam asset'
        elif key.endswith('.cap'):
            # These caption tables are already supplied by the disc/title
            # update. Leave the native tables in place rather than applying
            # an unproved endian conversion to a PC caption file.
            continue
        elif key.startswith('btscene/pack/clb/') and Path(key).stem in REGIONS:
            reason='Steam episode battle-scene definitions'
        elif any('white_'+zone+'u_img.' in ntpath.basename(entry[0]) for zone in REGIONS):
            reason='Steam episode region overlay'
        elif key.startswith('gui/') and key.endswith('.xgr'):
            pc,base=S.read(key),X.read(key)
            if not pc or not base or pc[:4]!=b'WPD\0' or base[:4]!=b'WPD\0': continue
            a={(name,ext) for name,ext,_ in wpd.read(pc)}
            b={(name,ext) for name,ext,_ in wpd.read(base)}
            if not a-b: continue
            reason='additional Steam UI definitions'
        elif key.startswith('db/') and key.endswith('.wdb'):
            pc,base=S.read(key),X.read(key)
            if not pc or not base or pc==base: continue
            # The disc's casino tables are pre-DLC placeholders. The Steam
            # casino supplies the complete schemas, NPCs, AI and dialogue;
            # comparing them to those placeholders would discard required
            # episode data. These WDBs are platform-neutral self-describing
            # tables, just like additional Steam databases absent on disc.
            if key.startswith('db/resident/csn_'):
                tables={n.rstrip(b'\0'):d for n,_,d in wpd.read(pc)}
                if not tables.get(b'!!sheetname',b'').startswith(b'Casino') or b'!structitem' not in tables:
                    raise ValueError('invalid Steam casino database: '+key)
                reason='Steam casino subsystem tables'
            elif not portable_database(pc,base):
                metadata_kept.append(key)
                continue
            else: reason='Steam database with matching native schema'
        else: continue
        target=target_path(path)
        if convert.kind(target)=='unknown':
            raise ValueError('unsupported additional Steam asset: '+target)
        group='scr' if 'scr' in ntpath.basename(entry[0]).lower() else 'img'
        selected[target]=(entry[5],entry[6],target,group)
        reasons[reason]+=1
    # A bank with additional Steam effects also needs its updated existing
    # members and aggregate WPD. The disc's bank may be an earlier subset.
    # Restrict this closure to numeric effect banks, not base voice folders.
    banks={gameindex.norm(p).rsplit('/',1)[0] for p in selected
           if re.match(r'^sound/pack/[0-9]+/',p)}
    for key,entry in sorted(S.files.items()):
        target=target_path(entry[4])
        if key.rsplit('/',1)[0] not in banks or target in selected: continue
        group='scr' if 'scr' in ntpath.basename(entry[0]).lower() else 'img'
        selected[target]=(entry[5],entry[6],target,group)
        reasons['matching Steam sound-bank files']+=1
    # Converted headers describe newly tiled/repacked texels. Even when a
    # companion exists on the disc, it must travel with the Steam header (and
    # vice versa): native offsets are not offsets into the converted image.
    selected_keys={gameindex.norm(p):p for p in selected}
    pending=list(selected_keys)
    while pending:
        key=pending.pop()
        if key not in S.files: continue  # movie_win/ is installed as movie/
        candidates=[]
        if key.endswith('.imgb'):
            stem=key[:-5]
            candidates=[stem+ext for ext in ('.trb','.xfv','.xgr','.xwb','.bin','.xwp')]
            if stem.endswith('.white'): candidates.append(stem[:-6]+'_us.white.bin')
            owner=next((p for p in candidates if p in selected_keys),None)
            if owner is None: owner=next((p for p in candidates if p in S.files),None)
            candidates=[owner] if owner else []
        elif convert.kind(target_path(S.files[key][4])) in ('trb','bundle'):
            stem=key.rsplit('.',1)[0]
            candidates=[stem+'.imgb']
            if stem.endswith('_us.white'): candidates.append(stem[:-9]+'.white.imgb')
            candidates=[next((p for p in candidates if p in S.files),None)]
        for dependency in candidates:
            if dependency is None or dependency in selected_keys or dependency not in S.files: continue
            entry=S.files[dependency]
            target=target_path(entry[4])
            group=selected[selected_keys[key]][3]
            selected[target]=(entry[5],entry[6],target,group)
            selected_keys[dependency]=target
            pending.append(dependency)
            reasons['matching Steam texture/header dependency']+=1
    # Headers and package membership are authored here, not copied from Xbox
    # DLC. Public product IDs 21 and 23 remain entitlement markers; their
    # additional Steam assets share the bundle overlay and normal file lookup.
    header=(hashlib.sha256(b'leftisthominid XIII-2 Steam overlay v1').digest()[:16]+bytes(16)).hex()
    packs={}
    for ident,name in (('0000021',"Serah's Weapon: Seraphic Wing"),('0000023',"Serah's Weapon: Azrael"),
                       ('0000071','Final Fantasy XIII-2 Steam content overlay')):
        packs[ident]=dict(name=name,lists=[dict(kind=kind,header=header,
            files=[list(row[:3]) for row in selected.values() if ident=='0000071' and row[3]==kind]) for kind in ('img','scr')])
    return dict(version=VERSION,packs=packs,provenance=dict(
        membership='Steam effective index minus disc/title-update assets, plus episode regions, UI textures, casino databases, battle definitions and affected sound banks',
        xbox_dlc_used=False,steam_sources=S.sources,base_sources=X.sources,
        selected=len(selected),reasons=dict(reasons),native_database_schemas_kept=metadata_kept))


def save(document,work):
    path=Path(work)/'steam_manifest.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.json.new')
    temp.write_text(json.dumps(document,indent=1),encoding='utf-8')
    os.replace(temp,path)


def load(work):
    with (Path(work)/'steam_manifest.json').open(encoding='utf-8') as stream: doc=json.load(stream)
    if doc.get('version')!=VERSION or doc.get('provenance',{}).get('xbox_dlc_used') is not False:
        raise ValueError('a current Steam-derived content inventory is required; run dlcknown')
    return doc['packs']
