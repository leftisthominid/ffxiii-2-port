#include "../../project/src/x2_module_bss.h"
#include <cassert>
#include <iostream>
#include <vector>
static void w16(std::vector<uint8_t>&b,size_t p,uint16_t v){b[p]=v;b[p+1]=v>>8;}
static void w32(std::vector<uint8_t>&b,size_t p,uint32_t v){for(unsigned i=0;i<4;i++)b[p+i]=v>>(i*8);}
int main(){
 std::vector<uint8_t> image(4096,0xa5);image[0]='M';image[1]='Z';w32(image,60,64);w32(image,64,0x4550);w16(image,70,2);w16(image,84,0);
 // Writable initialized data followed by uninitialized data.
 w32(image,96,256);w32(image,100,512);w32(image,104,128);w32(image,124,0xc0000040);
 // Executable sections must never be zeroed, even if they have a virtual tail.
 w32(image,136,256);w32(image,140,1024);w32(image,144,128);w32(image,164,0xe0000020);
 const auto original=image;assert(X2ClearModuleBss(image)==128);
 for(size_t i=0;i<image.size();i++)assert(image[i]==(i>=640&&i<768?0:original[i]));
 assert(X2ClearModuleBss(image)==128);
 for(unsigned bad=0;bad<5;bad++){
  auto damaged=original;
  if(bad==0)w32(damaged,60,0xfffffffe);
  if(bad==1)w16(damaged,70,97);
  if(bad==2)w32(damaged,100,4090);
  if(bad==3)w32(damaged,100,0);
  if(bad==4)damaged[0]=0;
  const auto before=damaged;assert(X2ClearModuleBss(damaged)==0);assert(damaged==before);
 }
 assert(X2ClearModuleBss({})==0);
 std::cout<<"MODULE BSS: initialized bytes/code preserved, exact tail cleared, malformed inputs unchanged PASS\n";
}
