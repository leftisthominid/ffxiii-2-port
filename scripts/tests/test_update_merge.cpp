#define main x2_jpvoice_cli_main
#include "../../converters/x2_jpvoice.cpp"
#undef main
#include <cassert>
int main() {
 const auto root=fs::temp_directory_path()/("x2-merge-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
 struct Clean {fs::path p;~Clean(){std::error_code e;fs::remove_all(p,e);}} clean{root};
 auto tu=root/"tu",jp=root/"jp",out=root/"merged";
 fs::create_directories(tu);fs::create_directories(jp);fs::create_directories(out);
 write_file(tu/"x2_title_update.txt",Bytes{'T','U'});
 write_file(out/"preserved",Bytes{'o','l','d'});
 const auto list="filelist_patch_u.x360.bin",arc="white_patch_u.x360.bin";
 auto fails=[&]{bool threw=false;try{merge_update(tu,jp,out);}catch(const std::exception&){threw=true;}assert(threw);assert(read_file(out/"preserved")==Bytes({'o','l','d'}));};
 fails(); // marker without any title-update data
 std::vector<PatchFile> title{{1,16,"sys/script/dlc.clb",0,3},{2,160,"sound/pack/usa/a.x360.scd",1,3}};
 write_filelist(tu/list,title);fails(); // missing title archive
 Bytes data(4096);data[0]='T';data[2048]='E';write_file(tu/arc,data);
 std::vector<PatchFile> voices{{2,160,"sound/pack/usa/a.x360.scd",0,3}};
 write_filelist(jp/list,voices);fails(); // missing voice archive
 Bytes audio(2048);audio[0]='J';write_file(jp/arc,audio);
 auto bad=voices;bad[0].sector=99;write_filelist(jp/list,bad);fails();
 bad=voices;bad[0].path="sys/script/unrelated.clb";write_filelist(jp/list,bad);fails();
 bad=voices;bad[0].path="sound/pack/usa/wrong.x360.scd";write_filelist(jp/list,bad);fails(); // same code, other path
 auto caption=voices;caption[0]={3,161,"sound/pack/caption/line.bin",0,3};
 write_filelist(jp/list,caption);fails(); // nonvoice changes require the converter's exact declaration
 const std::string declaration="a100000003 sound/pack/caption/line.bin\n";
 write_file(jp/"jp_lipsync.txt",Bytes(declaration.begin(),declaration.end()));
 assert(merge_update(tu,jp,out)==0); // supported caption path is retained
 write_file(out/"preserved",Bytes{'o','l','d'});
 caption[0].path="sound/pack/caption/other.bin";write_filelist(jp/list,caption);fails();
 write_filelist(jp/list,voices);assert(merge_update(tu,jp,out)==0);
 const auto entries=parse_filelist(read_file(out/list));assert(entries.size()==2);
 Archive merged(out/arc);assert(merged.read(entries[0])[0]=='T');assert(merged.read(entries[1])[0]=='J');
 assert(read_file(tu/arc)==data);assert(read_file(jp/arc)==audio);
 assert(fs::exists(out/"x2_merged_v2.txt"));
 // Model the observed TU shape: same identity/path, separate archive sectors.
 auto repeated=title;auto duplicate=title[0];duplicate.sector=2;repeated.push_back(duplicate);
 data.resize(6144);data[4096]='T';write_file(tu/arc,data);write_filelist(tu/list,repeated);
 assert(merge_update(tu,jp,out)==0);
 auto repeat_entries=parse_filelist(read_file(out/list));assert(repeat_entries.size()==3);
 assert(repeat_entries[0].sector==0&&repeat_entries[1].sector==2);
 {Archive archive(out/arc);assert(archive.read(repeat_entries[0])==archive.read(repeat_entries[1]));}
 write_file(out/"preserved",Bytes{'o','l','d'});
 data[4096]='X';write_file(tu/arc,data);fails(); // different payload remains an error
 data[4096]='T';write_file(tu/arc,data);
 repeated.back().path="sys/script/other.clb";write_filelist(tu/list,repeated);fails();
 write_filelist(tu/list,title);auto duplicate_voices=voices;duplicate_voices.push_back(voices[0]);
 write_filelist(jp/list,duplicate_voices);fails(); // voice output still requires unique identities
 puts("PASS update merge: missing pairs, missing TU data, out-of-bounds entries, unrelated content and identity collision rejected; title data and inputs preserved");
}
