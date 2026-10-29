"""Read-only shader audit collection for every Steam-derived DLC model."""
import argparse, hashlib, json, sys, tarfile, time, zipfile, zlib
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--source',required=True)
p.add_argument('--out',required=True)
p.add_argument('--steam',default=r'C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY XIII-2')
p.add_argument('--game-output',default=r'C:\FFXIII-2 Test')
p.add_argument('--base-game',default=r'C:\FFXIII-2\360\game')
a=p.parse_args();sys.path.insert(0,str(Path(a.source)/'converters'))
from x2dlc import archive,gameindex,trb
root=Path(a.game_output);out=Path(a.out);stage=out/'shader-data';objects=stage/'objects';objects.mkdir(parents=True,exist_ok=True)
manifest=root/'work/steamdlc/steam_manifest.json'
d=json.loads(manifest.read_text(encoding='utf-8-sig'))
wanted={gameindex.norm(e[-1])for pack in d['packs'].values()for group in pack['lists']for e in group['files']if e[-1].endswith(('.trb','.mpk'))}
assets=[];notes=[];stored=set();total=0;last=time.monotonic();processed=0

def save(data):
    global total
    digest=hashlib.sha256(data).hexdigest()
    if digest not in stored:
        if len(data)>16<<20 or total+len(data)>256<<20:raise ValueError('shader evidence size limit')
        (objects/(digest+'.bin')).write_bytes(data);stored.add(digest);total+=len(data)
    return digest

def capture(origin,path,data):
    global last,processed
    processed+=1
    try:
        t=trb.Trb(data);names=t.name_map();full=hashlib.sha256(data).hexdigest()
        if '/f574/'in path:
            assets.append(dict(origin=origin,path=path,kind='complete-model',sha256=save(data),bytes=len(data)))
        for i,b in enumerate(t.res):
            if b[:7]!=b'SEDBshd':continue
            assets.append(dict(origin=origin,path=path,kind='shader',resource_index=i,resource_name=names.get(i,''),
                               model_sha256=full,sha256=save(b),bytes=len(b)))
    except Exception as ex:notes.append(origin+' '+path+': '+repr(ex))
    if processed%25==0 or time.monotonic()-last>=15:
        print('Read '+str(processed)+' model containers; '+str(len(stored))+' distinct shader/model objects.',flush=True);last=time.monotonic()

for origin,path,platform,update in [('steam',a.steam,'win32',None),('base-game',a.base_game,'x360',str(root/'run/update_tu'))]:
    if not (Path(path)/'alba_data').is_dir():notes.append('Source unavailable: '+origin);continue
    print('Indexing '+origin,flush=True);index=None
    try:
        index=gameindex.GameIndex(path,platform,update=update,log=print)
        for key in sorted(wanted):
            if key not in index.files:continue
            try:
                data=index.read(key)
                if data is not None:capture(origin,key,data)
            except Exception as ex:notes.append(origin+' '+key+': '+repr(ex))
    finally:
        if index is not None:index.close()

for parent,origin in [(root/'userdata/0000000000000000/53510806/00000002','installed'),(root/'run/jpvoice_dlc','Japanese-active')]:
    print('Reading '+origin+' packages',flush=True)
    for fl in sorted(parent.glob('*/filelist_p*_u.x360.bin')):
        white=fl.with_name(fl.name.replace('filelist_','white_',1))
        if not white.is_file():continue
        try:
            _,entries=archive.read_list(fl)
            with white.open('rb')as stream:
                length=white.stat().st_size
                for code,kind,sector,un,cm,path in entries:
                    key=gameindex.norm(path)
                    if key not in wanted:continue
                    if un>32<<20 or sector*2048+cm>length:notes.append('Invalid entry: '+origin+' '+key);continue
                    stream.seek(sector*2048);data=stream.read(cm)
                    data=zlib.decompress(data)if cm!=un else data
                    if len(data)!=un:raise ValueError('entry size mismatch')
                    capture(origin,key,data)
        except Exception as ex:notes.append(str(fl)+': '+repr(ex))

(stage/'assets.json').write_text(json.dumps(assets,indent=2),encoding='utf-8')
(stage/'notes.txt').write_text('\n'.join(notes),encoding='utf-8')
(stage/'inventory.json').write_text(json.dumps(sorted(wanted),indent=2),encoding='utf-8')
for name in ['run.log','steamdlc_build.log']:
    path=root/'logs'/name
    if path.is_file():(stage/name).write_bytes(path.read_bytes())
bundle=out/'shaders.tar.gz';print('Compressing '+str(len(assets))+' shader/model records.',flush=True)
with tarfile.open(bundle,'w:gz')as t:
    for file in sorted(stage.rglob('*')):
        if file.is_file():t.add(file,arcname=file.relative_to(stage).as_posix())
upload=out/'Upload-these-zips';upload.mkdir(exist_ok=True);pieces=[];offset=0
with bundle.open('rb')as f:
    while True:
        data=f.read(6<<20)
        if not data:break
        name='XIII-2_ShaderAudit_%02d.zip'%(len(pieces)+1);entry='shaders.tar.gz.offset-'+str(offset)
        with zipfile.ZipFile(upload/name,'w',zipfile.ZIP_STORED)as z:z.writestr(entry,data)
        pieces.append(dict(zip=name,entry=entry,original=bundle.name,offset=offset,size=len(data),sha256=hashlib.sha256(data).hexdigest()));offset+=len(data)
with zipfile.ZipFile(upload/'XIII-2_ShaderAudit_Index.zip','w',zipfile.ZIP_DEFLATED)as z:z.writestr('manifest.json',json.dumps(pieces,indent=2))
print('Ready: '+str(upload),flush=True)
if not assets:raise SystemExit('No shader assets found. Upload the result ZIP for its collection notes.')
