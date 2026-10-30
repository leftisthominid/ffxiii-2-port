"""Stream comprehensive DLC evidence into small upload ZIPs; input files are read-only."""
import argparse,hashlib,io,json,os,re,sys,tarfile,threading,time,zipfile,zlib
from pathlib import Path

def open_upload_folder(folder):
    if sys.platform == 'win32':
        try: os.startfile(str(folder))
        except OSError as ex: print('Open the upload folder manually: '+str(folder)+' ('+str(ex)+')',flush=True)

def collect_generated(folder, emit, note, progress=lambda path:None, limit=1<<20):
    """Only selected generated function definitions; never arbitrary user documents."""
    import re
    targets = {'sub_828F0CD0', 'sub_829E8E88', 'sub_829C51E0', 'sub_82B04830', 'sub_82B04E48',
               'sub_828D1010', 'sub_828E3640', 'sub_828CDE18'}
    found = set()
    marker = re.compile(rb'^DEFINE_REX_FUNC\((sub_[0-9A-Fa-f]{8})\)')
    for path in sorted(Path(folder).rglob('*.cpp')):
        progress(path.name)
        name, chunks, size, oversized = None, [], 0, False
        def finish():
            if name and not oversized:
                emit('generated-evidence/'+name+'.cpp', b''.join(chunks))
                found.add(name)
        with path.open('rb') as stream:
            for line in stream:
                match = marker.match(line)
                if match:
                    finish()
                    candidate = match.group(1).decode('ascii')
                    name = candidate if candidate in targets and candidate not in found else None
                    chunks, size, oversized = [], 0, False
                if name:
                    size += len(line)
                    if size > limit:
                        if not oversized: note('Generated function exceeds evidence limit: '+name)
                        oversized = True; chunks.clear()
                    elif not oversized: chunks.append(line)
        finish()
    missing = sorted(targets - found)
    if missing: note('Generated function evidence unavailable: '+', '.join(missing))
    return found

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--out',required=True)
    p.add_argument('--game-output',default=r'C:\FFXIII-2 Test');p.add_argument('--steam',default=r'C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY XIII-2');p.add_argument('--base-game',default=r'C:\FFXIII-2\360\game')
    p.add_argument('--session');p.add_argument('--no-explorer',action='store_true')
    a=p.parse_args(argv);sys.path.insert(0,str(Path(a.source)/'converters'))
    from x2dlc import archive,gameindex
    root=Path(a.game_output);out=Path(a.out);upload=out/'Upload-these-zips';upload.mkdir(parents=True,exist_ok=True)
    if list(upload.glob('*.zip')):raise SystemExit('Use a new output directory; existing ZIPs are retained.')
    manifest_path=root/'work/steamdlc/steam_manifest.json';manifest_problem=None
    try:
        manifest=json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        wanted={gameindex.norm(e[-1])for pack in manifest['packs'].values()for group in pack['lists']for e in group['files']}
    except (OSError,ValueError,KeyError,TypeError) as ex:
        manifest_problem='Steam inventory unavailable: '+repr(ex);wanted=set()
    regions=('white_z0070','white_z0130','white_z0160','white_z0161')
    records=[];notes=[manifest_problem] if manifest_problem else [];stored=set();state={'phase':'Indexing','assets':0,'unique':0,'raw_bytes':0,'zip_bytes':0};stop=threading.Event()
    def heartbeat():
        while not stop.wait(15):print('Collecting: '+state['phase']+'; '+str(state['assets'])+' assets, '+str(state['unique'])+' unique; '+str(state['zip_bytes']//(1<<20))+' MB in upload ZIPs',flush=True)
    threading.Thread(target=heartbeat,daemon=True).start()
    class Pieces:
        def __init__(self):self.buffer=bytearray();self.parts=[];self.offset=0
        def write(self,data):
            original=len(data)
            while data:
                n=min((6<<20)-len(self.buffer),len(data));self.buffer.extend(data[:n]);data=data[n:]
                if len(self.buffer)==6<<20:self.flush_part()
            return original
        def flush(self):pass
        def flush_part(self):
            if not self.buffer:return
            data=bytes(self.buffer);self.buffer.clear();name='XIII-2_DLC_Complete_%03d.zip'%(len(self.parts)+1);entry='complete.tar.gz.offset-'+str(self.offset)
            with zipfile.ZipFile(upload/name,'w',zipfile.ZIP_STORED)as z:z.writestr(entry,data)
            self.parts.append(dict(zip=name,entry=entry,original='complete.tar.gz',offset=self.offset,size=len(data),sha256=hashlib.sha256(data).hexdigest()));self.offset+=len(data);state['zip_bytes']=self.offset
            temp=upload/'index.new'
            with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED)as z:z.writestr('manifest.json',json.dumps(self.parts,indent=2))
            os.replace(temp,upload/'XIII-2_DLC_Complete_Index.zip')
        def finish(self):self.flush_part()
    def json_bytes(value):return json.dumps(value,indent=1).encode('utf-8')
    def add_bytes(t,name,data):
        info=tarfile.TarInfo(name);info.size=len(data);t.addfile(info,io.BytesIO(data))
    def save(t,origin,key,data,source_path=None,code=None,ftype=None,package_name=None,list_name=None):
        h=hashlib.sha256(data).hexdigest()
        if h not in stored:
            add_bytes(t,'objects/'+h+'.bin',data);stored.add(h);state['unique']+=1;state['raw_bytes']+=len(data)
        records.append(dict(origin=origin,path=key,source_path=source_path or key,code=code,ftype=ftype,package_name=package_name,list_name=list_name,sha256=h,bytes=len(data)));state['assets']+=1

    def read_entry(arc,sector,un,cm):
        with Path(arc).open('rb')as f:
            if sector*2048+cm>f.seek(0,2):raise ValueError('entry outside archive')
            f.seek(sector*2048);data=f.read(cm)
        data=zlib.decompress(data)if cm!=un else data
        if len(data)!=un:raise ValueError('entry size mismatch')
        return data

    def read_index(t,label,idx,selected):
        add_bytes(t,'source-indexes/'+label+'.json',json_bytes(dict(sources=idx.sources,files=idx.files)))
        count=0
        for key in sorted(selected):
            e=idx.files.get(key)
            if e is None:continue
            state['phase']=label+' '+key
            try:save(t,label,key,read_entry(*e[:4]),e[4],e[5],e[6]);count+=1
            except Exception as ex:notes.append(label+' '+key+': '+repr(ex))
        print(label+': '+str(count)+' assets collected',flush=True)

    def extra_sources(idx):
        return {k for k,e in idx.files.items()if '/udp/'in str(e[0]).replace('\\','/').lower()or any(z in str(e[0]).lower()for z in regions)}

    print('Collecting all selected DLC assets, complete Steam patch/episode assets, both languages, installed and Japanese-active packages, base matches and learning data.',flush=True)
    print('This can produce several GB and hundreds of 6 MB ZIP parts. Identical contents are stored once; there is no full temporary archive.',flush=True)
    pieces=Pieces();fatal=None;indexes=[]
    try:
        with tarfile.open(fileobj=pieces,mode='w|gz',compresslevel=6)as t:
            if a.session:
                for name in ('steps.txt','failure.txt','source-version.json'):
                    f=Path(a.session)/name
                    if f.is_file():t.add(f,arcname='session/'+name,recursive=False)
            for f in sorted((root/'logs/run-history').rglob('*.log')):
                if f.is_file():t.add(f,arcname='logs/run-history/'+f.relative_to(root/'logs/run-history').as_posix(),recursive=False)
            for parent,label in [(root/'logs','logs'),(root/'work/steamdlc','learning'),(root/'run/jpvoice_dlc','voice-reports')]:
                if not parent.is_dir():continue
                for f in sorted(parent.glob('*')):
                    allowed = (label == 'logs' and (f.suffix.lower() == '.log' or re.fullmatch(r'jpvoice_dlc_.*\.csv',f.name))) or (label == 'learning' and f.name in ('base_data.pkl','dlc_known.pkl','build_report.json','steam_manifest.json','learn_version.txt','dlc_known_version.txt','compare.txt','moved_360_packages.txt')) or (label == 'voice-reports' and re.fullmatch(r'\d{7}(?:_.*\.csv|\.txt)',f.name))
                    if f.is_file() and allowed:
                        state['phase']=label+' '+f.name;t.add(f,arcname=label+'/'+f.name,recursive=False)
            english=gameindex.GameIndex(a.steam,'win32',log=print);indexes.append(english)
            en_wanted=wanted|{k.replace('movie/','movie_win/',1)for k in wanted}|extra_sources(english)
            for key in sorted(wanted):
                if key not in english.files and key.replace('movie/','movie_win/',1)not in english.files:notes.append('Steam inventory asset missing: '+key)
            en_wanted&=english.files.keys();read_index(t,'steam-English',english,en_wanted)
            jp=gameindex.GameIndex(a.steam,'win32',language_set='c',log=print);indexes.append(jp)
            ids={english.files[k][5:7]for k in en_wanted}
            jp_wanted=extra_sources(jp)|{k for k,e in jp.files.items()if k in en_wanted or e[5:7]in ids or k.replace('/usa/','/')in en_wanted}
            read_index(t,'steam-Japanese',jp,jp_wanted)
            if (Path(a.base_game)/'alba_data').is_dir():
                base=gameindex.GameIndex(a.base_game,'x360',update=str(root/'run/update_tu'),log=print);indexes.append(base)
                read_index(t,'base-game',base,(wanted|en_wanted|jp_wanted)&base.files.keys())
            for parent,label in [(root/'userdata/0000000000000000/53510806/00000002','installed'),(root/'run/jpvoice_dlc','Japanese-active')]:
                for folder in sorted(parent.glob('*')):
                    if not folder.is_dir():continue
                    for fl in sorted(folder.glob('filelist_p*_u.x360.bin')):
                        state['phase']=label+' '+fl.name;white=fl.with_name(fl.name.replace('filelist_','white_',1))
                        t.add(fl,arcname='package-indexes/'+label+'/'+folder.name+'/'+fl.name,recursive=False)
                        try:
                            _,entries=archive.read_list(fl)
                            for code,kind,sector,un,cm,path in entries:
                                key=gameindex.norm(path);state['phase']=label+' '+key
                                try:save(t,label,key,read_entry(white,sector,un,cm),path,code,kind,folder.name,fl.name)
                                except Exception as ex:notes.append(label+' '+key+': '+repr(ex))
                        except Exception as ex:notes.append(str(fl)+': '+repr(ex))
                    for f in folder.glob('*'):
                        if f.is_file() and (f.name == 'identifier' or re.fullmatch(r'(?:source_|verified_)filelist_p\d{7}(?:img|scr)_u\.x360\.bin',f.name) or f.name in ('x2dlc_keys_img.json','x2dlc_keys_scr.json')):t.add(f,arcname='package-metadata/'+label+'/'+folder.name+'/'+f.name,recursive=False)
            for f in sorted((root/'userdata/0000000000000000/53510806/Headers/00000002').glob('*.header')):t.add(f,arcname='content-headers/'+f.name,recursive=False)
            for name in ('aa.txt','confirm.txt','fps.txt','fps_trial.txt','story_recaps.txt','text.txt','ui_style.txt','voice.txt','keyboard.txt','x2_missing_movies.txt'):
                f=root/'userdata'/name
                if f.is_file():t.add(f,arcname='settings/'+f.name,recursive=False)
            for part in ('sys','udp'):
                for f in sorted((Path(a.steam)/'alba_data'/part).glob('filelist*.bin')):t.add(f,arcname='steam-filelists/'+part+'/'+f.name,recursive=False)
            collect_generated(root/'project/generated/default', lambda name,data:add_bytes(t,name,data), notes.append, lambda path:state.update(phase='Generated code '+path))
            add_bytes(t,'assets.json',json_bytes(records));add_bytes(t,'notes.json',json_bytes(notes));add_bytes(t,'collection-summary.json',json_bytes(dict(state,notes=len(notes),scope='Steam inventory plus complete effective patch/episode assets; all installed/active packages; base matches; learning caches')))
    except Exception as ex:fatal=repr(ex);print('Collection stopped: '+fatal,flush=True)
    finally:
        for idx in indexes:idx.close()
        pieces.finish();stop.set()
    (out/'collection-status.json').write_bytes(json_bytes(dict(state,notes=notes,fatal=fatal)))
    print('Ready: '+str(upload)+'; '+str(len(pieces.parts))+' data ZIPs plus Index. Collection errors: '+str(len(notes)),flush=True)
    if not a.no_explorer:open_upload_folder(upload)
    if fatal or notes:raise SystemExit('Upload all ZIPs and collection-status.json; collection errors are recorded.')

if __name__ == '__main__':
    main()
