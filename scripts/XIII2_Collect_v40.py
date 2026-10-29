"""Collect bounded v40 test results; all source media is opened read-only."""
import csv, hashlib, json, re, shutil, sys, tarfile, zipfile
from pathlib import Path

def collect(root, work, target):
    stage=work/'result-data';stage.mkdir(exist_ok=True)
    def copy(path,name):
        if path.is_file():
            dest=stage/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,dest)
    for pattern in ('*.log','jpvoice_dlc_*.csv'):
        for p in (root/'logs').glob(pattern):copy(p,'logs/'+p.name)
    for name in ('build_report.json','steam_manifest.json','compare.txt','learn_version.txt','dlc_known_version.txt'):
        copy(root/'work/steamdlc'/name,'reports/'+name)
    for name in ('steps.txt','failure.txt','regression-tests.log','source-version.json'):
        copy(work/name,name)
    for p in (root/'run/movies_steam').glob('*.json'):copy(p,'movies/'+p.name)
    copy(root/'run/movies_steam/index.txt','movies/index.txt')
    copy(root/'work/steamdlc/movies/base-keyframe.bik','movies/base-keyframe.bik')
    copy(root/'userdata/x2_missing_movies.txt','settings/x2_missing_movies.txt')
    for name in ('story_recaps.txt','voice.txt','fps.txt'):
        copy(root/'userdata'/name,'settings/'+name)
    # Include a few error-associated effect inputs/outputs, bounded and deduplicated.
    # Japanese archive folders intentionally do not require an identifier file.
    notes=[];assets=[];budget=0;stored=set()
    try:
        sys.path.insert(0,str(target/'converters'))
        from x2dlc import gameindex,archive
        text=(root/'logs/run.log').read_text(encoding='utf-8',errors='replace') if (root/'logs/run.log').is_file() else ''
        recent=[];wanted=[];scene_files=[]
        for line in text.splitlines():
            m=re.search(r'\[File\] get info <(vfx/[^>]+\.xfv)>',line)
            if m:
                key=gameindex.norm(m.group(1));recent=[x for x in recent if x!=key]+[key];recent=recent[-4:]
            if 'vleaf(vleafins) version error' in line or 'XIII-2 crash:' in line:
                for key in recent:
                    if key not in wanted:wanted.append(key)
            scene=re.search(r'\[File\] get info <(event/schedule/[^>]+\.(?:bin|imgb|xwb))>',line)
            if scene:
                key=gameindex.norm(scene.group(1));scene_files=[p for p in scene_files if p!=key]+[key]
        wanted=scene_files[-4:]+wanted[-6:]
        wanted.insert(0,'scene/lay/scene00131/scene00131_lys/VFX/VLeafIns/bin/VLeafInstance.vinsbin')
        # One bounded voice sample per actual failure reason; shared bilingual
        # recordings are deliberately excluded. No source archive is changed.
        voice_reasons=set()
        for report in sorted((root/'logs').glob('jpvoice_dlc_*.csv')):
            with report.open(encoding='utf-8-sig',errors='replace',newline='') as stream:
                for row in csv.DictReader(stream):
                    detail=row.get('detail','')
                    key=gameindex.norm(row.get('path',''))
                    if row.get('result')!='kept English' or 'same in both languages' in detail or not key:continue
                    if detail in voice_reasons or len(voice_reasons)>=6:continue
                    voice_reasons.add(detail)
                    if key not in wanted:wanted.append(key)
        if wanted:
            def save(label,key,data,source_path=None):
                nonlocal budget
                if data is None or len(data)>8<<20:return
                digest=hashlib.sha256(data).hexdigest()
                if digest not in stored:
                    if budget+len(data)>24<<20:return
                    dest=stage/'effect-objects'/(digest+'.bin');dest.parent.mkdir(exist_ok=True);dest.write_bytes(data)
                    stored.add(digest);budget+=len(data)
                assets.append(dict(origin=label,path=key,source_path=source_path or key,sha256=digest,bytes=len(data)))
            for origin,source,platform,update in [('steam',Path(r'C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY XIII-2'),'win32',None),
                ('base-game',Path(r'C:\FFXIII-2\360\game'),'x360',str(root/'run/update_tu'))]:
                if (source/'alba_data').is_dir():
                    idx=gameindex.GameIndex(str(source),platform,update=update,log=lambda *a:None)
                    try:
                        for key in wanted:save(origin,key,idx.read(key))
                        if origin=='steam' and (source/'alba_data/sys/filelistc.win32.bin').is_file():
                            japanese=gameindex.GameIndex(str(source),'win32',language_set='c',log=lambda *a:None)
                            try:
                                by_code={e[5:7]:p for p,e in reversed(list(japanese.files.items()))}
                                for key in wanted:
                                    match=key.replace('/usa/','/',1)
                                    if match not in japanese.files and key in idx.files:
                                        match=by_code.get(idx.files[key][5:7])
                                    if match in japanese.files:save('steam-Japanese',key,japanese.read(match),match)
                            finally:japanese.close()
                    finally:idx.close()
            folders=[]
            installed=root/'userdata/0000000000000000/53510806/00000002'
            jp=root/'run/jpvoice_dlc'
            for parent,label in ((installed,'installed'),(jp,'Japanese-active')):
                if parent.is_dir():folders.extend((p,label) for p in parent.iterdir() if p.is_dir())
            import zlib
            for folder,label in folders:
                for fl in folder.glob('filelist_p*_u.x360.bin'):
                    white=fl.with_name(fl.name.replace('filelist_','white_',1))
                    if not white.is_file():continue
                    try: _,entries=archive.read_list(fl)
                    except ValueError as ex:
                        from x2dlc import wfilelist
                        try:
                            raw,valid=wfilelist.decrypt(fl.read_bytes())
                            _,entries=archive.parse_plain(raw)
                            notes.append('Diagnostic filelist read despite checksum mismatch: '+str(fl))
                        except Exception as parse_error:
                            notes.append('Skipped '+str(fl)+': '+repr(parse_error));continue
                    except Exception as ex:
                        notes.append('Skipped '+str(fl)+': '+repr(ex));continue
                    with white.open('rb') as stream:
                        for code,kind,sector,un,cm,path in entries:
                            key=gameindex.norm(path)
                            if key not in wanted or un>8<<20:continue
                            stream.seek(sector*2048);data=stream.read(cm)
                            save(label,key,zlib.decompress(data) if cm!=un else data)
    except Exception as ex:notes.append('Optional effect capture: '+repr(ex))
    (stage/'effect-assets.json').write_text(json.dumps(assets,indent=2),encoding='utf-8')
    (stage/'collection-notes.txt').write_text('\n'.join(notes),encoding='utf-8')
    print('Compressing complete result logs and bounded effect evidence.',flush=True)
    bundle=work/'result.tar.gz'
    with tarfile.open(bundle,'w:gz',compresslevel=6) as archive:
        for p in sorted(stage.rglob('*')):
            if p.is_file():archive.add(p,arcname=p.relative_to(stage).as_posix())
    output=work/'Upload-these-zips';output.mkdir(exist_ok=True)
    manifest=[]
    with bundle.open('rb') as stream:
        offset=0;part=0
        while True:
            data=stream.read(6<<20)
            if not data:break
            part+=1;entry='result.tar.gz.offset-'+str(offset)
            name='XIII-2_v40_Result_%02d.zip'%part
            with zipfile.ZipFile(output/name,'w',zipfile.ZIP_STORED) as archive:archive.writestr(entry,data)
            manifest.append(dict(zip=name,entry=entry,original='result.tar.gz',offset=offset,size=len(data),sha256=hashlib.sha256(data).hexdigest()))
            offset+=len(data)
    with zipfile.ZipFile(output/'XIII-2_v40_Result_Index.zip','w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json',json.dumps(manifest,indent=2))
    print('Result archives: '+str(output),flush=True)

if __name__ == '__main__':
    if len(sys.argv) != 4: raise SystemExit('usage: collector game-output session-output source-folder')
    collect(*map(Path, sys.argv[1:]))
