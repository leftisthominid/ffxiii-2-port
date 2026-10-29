"""Read-only collection of the Colosseum scene's source and installed assets."""
import argparse, hashlib, json, tarfile, time, zipfile, zlib, sys
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('--source',required=True)
p.add_argument('--out',required=True)
p.add_argument('--steam',default=r'C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY XIII-2')
p.add_argument('--game-output',default=r'C:\FFXIII-2 Test')
a=p.parse_args()
sys.path.insert(0,str(Path(a.source)/'converters'))
from x2dlc import archive,gameindex,wfilelist
out=Path(a.out);stage=out/'scene-data';stage.mkdir(parents=True,exist_ok=True)
assets=[];notes=[];stored=set();total=0;last=time.monotonic()
def wanted(path):return 'ev_clzb_025' in path.lower()
def save(origin,path,data):
    global total,last
    if len(data)>16<<20 or total+len(data)>64<<20:
        notes.append('Size limit: '+origin+' '+path);return
    digest=hashlib.sha256(data).hexdigest()
    if digest not in stored:
        objects=stage/'objects';objects.mkdir(exist_ok=True)
        (objects/(digest+'.bin')).write_bytes(data);stored.add(digest);total+=len(data)
    assets.append(dict(origin=origin,path=path,sha256=digest,bytes=len(data)))
    print('Collected '+origin+' '+path+' ('+str(len(data))+' bytes)',flush=True)
for language,label in [('u','steam-English'),('c','steam-Japanese')]:
    index=None
    try:
        print('Indexing '+label,flush=True)
        index=gameindex.GameIndex(a.steam,'win32',language_set=language,log=print)
        for key in sorted(index.files):
            if wanted(key):
                data=index.read(key)
                if data is not None:save(label,key,data)
    except Exception as ex:notes.append(label+': '+repr(ex))
    finally:
        if index is not None:index.close()
root=Path(a.game_output)
for parent,label in [(root/'userdata/0000000000000000/53510806/00000002','installed'),(root/'run/jpvoice_dlc','Japanese-active')]:
    for fl in sorted(parent.glob('*/filelist_p*_u.x360.bin')):
        white=fl.with_name(fl.name.replace('filelist_','white_',1))
        if not white.is_file():continue
        try:
            raw,valid=wfilelist.decrypt(fl.read_bytes())
            if not valid:notes.append('Diagnostic read despite checksum mismatch: '+str(fl))
            _,entries=archive.parse_plain(raw)
            with white.open('rb') as stream:
                for code,kind,sector,un,cm,path in entries:
                    if not wanted(path) or un>16<<20:continue
                    if sector*2048+cm>white.stat().st_size:raise ValueError('entry outside archive')
                    stream.seek(sector*2048);data=stream.read(cm)
                    data=zlib.decompress(data)if cm!=un else data
                    if len(data)!=un:raise ValueError('entry size mismatch')
                    save(label,path,data)
        except Exception as ex:notes.append(str(fl)+': '+repr(ex))
(stage/'assets.json').write_text(json.dumps(assets,indent=2),encoding='utf-8')
(stage/'notes.txt').write_text('\n'.join(notes),encoding='utf-8')
for name in ('run.log','steamdlc_build.log'):
    path=root/'logs'/name
    if path.is_file():(stage/name).write_bytes(path.read_bytes())
bundle=out/'colosseum.tar.gz'
print('Compressing '+str(len(assets))+' asset copies',flush=True)
with tarfile.open(bundle,'w:gz')as t:
    for path in sorted(stage.rglob('*')):
        if path.is_file():t.add(path,arcname=path.relative_to(stage).as_posix())
upload=out/'Upload-these-zips';upload.mkdir(exist_ok=True);manifest=[];offset=0
with bundle.open('rb')as stream:
    while True:
        data=stream.read(6<<20)
        if not data:break
        name='XIII-2_Colosseum_025_%02d.zip'%(len(manifest)+1);entry='colosseum.tar.gz.offset-'+str(offset)
        with zipfile.ZipFile(upload/name,'w',zipfile.ZIP_STORED)as z:z.writestr(entry,data)
        manifest.append(dict(zip=name,entry=entry,original=bundle.name,offset=offset,size=len(data),sha256=hashlib.sha256(data).hexdigest()));offset+=len(data)
with zipfile.ZipFile(upload/'XIII-2_Colosseum_025_Index.zip','w',zipfile.ZIP_DEFLATED)as z:z.writestr('manifest.json',json.dumps(manifest,indent=2))
print('Ready: '+str(upload),flush=True)
if not assets:raise SystemExit('No scene assets found; upload the result ZIP so the collection notes can be checked.')
