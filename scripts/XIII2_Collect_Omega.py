"""Read-only collection of the motion bundles used by the reported Omega crash."""
import argparse,hashlib,json,sys,tarfile,zipfile,zlib
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--out',required=True)
p.add_argument('--game-output',default=r'C:\FFXIII-2 Test');p.add_argument('--steam',default=r'C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY XIII-2')
a=p.parse_args();sys.path.insert(0,str(Path(a.source)/'converters'))
from x2dlc import archive,gameindex
root=Path(a.game_output);out=Path(a.out);stage=out/'motion-data';objects=stage/'objects';objects.mkdir(parents=True,exist_ok=True)
wanted={f'mot/mon/sk_m952_omega/{n}.white.bin' for n in ('s1','x1')};records=[];notes=[];stored=set();total=0

def save(origin,path,data):
    global total
    if data is None:return
    if len(data)>64<<20:notes.append('Oversize '+origin+' '+path);return
    h=hashlib.sha256(data).hexdigest()
    if h not in stored:
        if total+len(data)>128<<20:raise ValueError('Collection size limit')
        (objects/(h+'.bin')).write_bytes(data);stored.add(h);total+=len(data)
    records.append(dict(origin=origin,path=path,sha256=h,bytes=len(data)))
    print(origin+' '+path+' '+str(len(data))+' bytes',flush=True)

for origin,path,platform,update in [('steam',a.steam,'win32',None),('base-game',r'C:\FFXIII-2\360\game','x360',str(root/'run/update_tu'))]:
    if not (Path(path)/'alba_data').is_dir():continue
    print('Indexing '+origin,flush=True);idx=None
    try:
        idx=gameindex.GameIndex(path,platform,update=update,log=print)
        for key in sorted(wanted):save(origin,key,idx.read(key))
    except Exception as ex:notes.append(origin+': '+repr(ex))
    finally:
        if idx:idx.close()
for parent,origin in [(root/'userdata/0000000000000000/53510806/00000002','installed'),(root/'run/jpvoice_dlc','Japanese-active')]:
    for fl in sorted(parent.glob('*/filelist_p*_u.x360.bin')):
        white=fl.with_name(fl.name.replace('filelist_','white_',1))
        if not white.is_file():continue
        try:
            _,entries=archive.read_list(fl)
            with white.open('rb')as stream:
                for code,kind,sector,un,cm,path in entries:
                    key=gameindex.norm(path)
                    if key not in wanted:continue
                    if un>64<<20 or sector*2048+cm>white.stat().st_size:raise ValueError('Archive entry bounds')
                    stream.seek(sector*2048);data=stream.read(cm);data=zlib.decompress(data)if cm!=un else data
                    if len(data)!=un:raise ValueError('Archive entry size')
                    save(origin,key,data)
        except Exception as ex:notes.append(str(fl)+': '+repr(ex))
for path in (root/'logs').glob('run*.log'):(stage/path.name).write_bytes(path.read_bytes())
(stage/'assets.json').write_text(json.dumps(records,indent=2),encoding='utf-8');(stage/'notes.txt').write_text('\n'.join(notes),encoding='utf-8')
print('Compressing motion evidence',flush=True);bundle=out/'motion.tar.gz'
with tarfile.open(bundle,'w:gz')as t:
    for path in sorted(stage.rglob('*')):
        if path.is_file():t.add(path,arcname=path.relative_to(stage).as_posix())
upload=out/'Upload-these-zips';upload.mkdir(exist_ok=True);pieces=[];offset=0
with bundle.open('rb')as stream:
    while True:
        data=stream.read(6<<20)
        if not data:break
        name='XIII-2_Omega_Motion_%02d.zip'%(len(pieces)+1);entry='motion.tar.gz.offset-'+str(offset)
        with zipfile.ZipFile(upload/name,'w',zipfile.ZIP_STORED)as z:z.writestr(entry,data)
        pieces.append(dict(zip=name,entry=entry,original=bundle.name,offset=offset,size=len(data),sha256=hashlib.sha256(data).hexdigest()));offset+=len(data)
with zipfile.ZipFile(upload/'XIII-2_Omega_Motion_Index.zip','w',zipfile.ZIP_DEFLATED)as z:z.writestr('manifest.json',json.dumps(pieces,indent=2))
print('Upload every ZIP from '+str(upload),flush=True)
if not records:raise SystemExit('No motion files collected; upload the ZIPs for the collection notes.')
