// 32-bit bridge to the user's Steam Bink DLL. Built with the Windows .NET
// Framework compiler; the game/DLL and all input media are opened read-only.
// stdout is BGRA video. PCM tracks and metadata go to a temporary output folder.
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

public static class X2BinkDecode {
  const CallingConvention ABI = CallingConvention.StdCall;
  [DllImport("bink2w32.dll", EntryPoint="_BinkOpen@8", ExactSpelling=true, CallingConvention=ABI, CharSet=CharSet.Ansi)]
  static extern IntPtr Open(string name, uint flags);
  [DllImport("bink2w32.dll", EntryPoint="_BinkClose@4", ExactSpelling=true, CallingConvention=ABI)]
  static extern void Close(IntPtr bink);
  [DllImport("bink2w32.dll", EntryPoint="_BinkSetSoundSystem@8", ExactSpelling=true, CallingConvention=ABI)]
  static extern int SoundSystem(IntPtr provider, uint parameter);
  [DllImport("bink2w32.dll", EntryPoint="_BinkGetError@0", ExactSpelling=true, CallingConvention=ABI)]
  static extern IntPtr Error();
  [DllImport("bink2w32.dll", EntryPoint="_BinkDoFrame@4", ExactSpelling=true, CallingConvention=ABI)]
  static extern int DoFrame(IntPtr bink);
  [DllImport("bink2w32.dll", EntryPoint="_BinkNextFrame@4", ExactSpelling=true, CallingConvention=ABI)]
  static extern void NextFrame(IntPtr bink);
  [DllImport("bink2w32.dll", EntryPoint="_BinkCopyToBuffer@28", ExactSpelling=true, CallingConvention=ABI)]
  static extern int Copy(IntPtr bink, IntPtr pixels, int pitch, uint height, uint x, uint y, uint flags);
  [DllImport("bink2w32.dll", EntryPoint="_BinkGetTrackID@8", ExactSpelling=true, CallingConvention=ABI)]
  static extern uint TrackId(IntPtr bink, uint index);
  [DllImport("bink2w32.dll", EntryPoint="_BinkOpenTrack@8", ExactSpelling=true, CallingConvention=ABI)]
  static extern IntPtr OpenTrack(IntPtr bink, uint id);
  [DllImport("bink2w32.dll", EntryPoint="_BinkCloseTrack@4", ExactSpelling=true, CallingConvention=ABI)]
  static extern void CloseTrack(IntPtr track);
  [DllImport("bink2w32.dll", EntryPoint="_BinkGetTrackData@8", ExactSpelling=true, CallingConvention=ABI)]
  static extern uint TrackData(IntPtr track, IntPtr buffer);
  [DllImport("kernel32.dll", SetLastError=true)]
  static extern IntPtr VirtualAlloc(IntPtr address, UIntPtr size, uint type, uint protection);
  [DllImport("kernel32.dll")]
  static extern bool VirtualFree(IntPtr address, UIntPtr size, uint type);
  static uint U32(IntPtr p, int word) { return unchecked((uint)Marshal.ReadInt32(p, word * 4)); }

  public static int Main(string[] args) {
    if (args.Length < 2 || args.Length > 3 || (args.Length == 3 && args[2] != "--audio-only")) {
      Console.Error.WriteLine("input.bik output-folder [--audio-only] (or input.bik --verify)"); return 2;
    }
    bool verify = args[1] == "--verify";
    bool audioOnly = args.Length == 3;
    IntPtr bink = IntPtr.Zero, pixels = IntPtr.Zero, pcm = IntPtr.Zero;
    IntPtr[] tracks = null;
    FileStream[] audio = null;
    try {
      SoundSystem(IntPtr.Zero, 0); // Extract audio; never play it during conversion.
      bink = Open(args[0], 0);
      if (bink == IntPtr.Zero) throw new IOException(Marshal.PtrToStringAnsi(Error()));
      uint width=U32(bink,0), height=U32(bink,1), frames=U32(bink,2);
      uint rate=U32(bink,5), divisor=U32(bink,6), count=verify ? 0 : U32(bink,14);
      if (width==0 || width>8192 || height==0 || height>8192 || frames==0 || rate==0 || divisor==0 || count>16)
        throw new InvalidDataException("Invalid movie dimensions, timing, or track count");
      int bytes=checked((int)(width*height*4));
      if (!audioOnly) {
        pixels=VirtualAlloc(IntPtr.Zero, new UIntPtr((uint)bytes), 0x3000, 4);
        if (pixels==IntPtr.Zero) throw new OutOfMemoryException();
      }
      byte[] managedPixels=verify || audioOnly ? null : new byte[bytes];
      tracks=new IntPtr[count]; audio=new FileStream[count];
      uint maxPcm=0;
      if (!verify) {
        Directory.CreateDirectory(args[1]);
        using (BinaryWriter meta=new BinaryWriter(File.Create(Path.Combine(args[1],"movie-info.bin")))) {
          foreach (uint value in new uint[]{width,height,frames,rate,divisor,count}) meta.Write(value);
          for (uint i=0;i<count;++i) {
            tracks[i]=OpenTrack(bink,TrackId(bink,i));
            if (tracks[i]==IntPtr.Zero) throw new InvalidDataException("Cannot open movie audio track");
            uint frequency=U32(tracks[i],0), bits=U32(tracks[i],1), channels=U32(tracks[i],2), max=U32(tracks[i],3);
            if (frequency<8000 || frequency>192000 || bits!=16 || channels==0 || channels>8 || max>16*1024*1024)
              throw new InvalidDataException("Unsupported movie audio track");
            meta.Write(frequency);meta.Write(bits);meta.Write(channels);meta.Write(max);
            maxPcm=Math.Max(maxPcm,max);
            audio[i]=File.Create(Path.Combine(args[1],"audio-"+i+".pcm"));
          }
        }
      }
      if (maxPcm>0) {
        pcm=VirtualAlloc(IntPtr.Zero,new UIntPtr(maxPcm),0x3000,4);
        if (pcm==IntPtr.Zero) throw new OutOfMemoryException();
      }
      byte[] managedPcm=new byte[maxPcm];
      Stream video=verify || audioOnly ? Stream.Null : Console.OpenStandardOutput();
      Stopwatch watch=Stopwatch.StartNew();long next=15000;
      uint limit=verify ? 1 : frames;
      for (uint frame=0;frame<limit;++frame) {
        DoFrame(bink);
        if (U32(bink,7)!=0) throw new IOException("Bink movie read error");
        for (uint i=0;i<count;++i) {
          uint n=TrackData(tracks[i],pcm);
          if (n>maxPcm) throw new InvalidDataException("Movie audio block exceeds declared buffer");
          if (n!=0) { Marshal.Copy(pcm,managedPcm,0,(int)n);audio[i].Write(managedPcm,0,(int)n); }
        }
        if (!audioOnly) Copy(bink,pixels,checked((int)width*4),height,0,0,3); // BINKSURFACE32: BGRA.
        if (!verify && !audioOnly) { Marshal.Copy(pixels,managedPixels,0,bytes);video.Write(managedPixels,0,bytes); }
        if (watch.ElapsedMilliseconds>=next) { Console.Error.WriteLine("Decoded "+(frame+1)+"/"+frames+" movie frames");next=watch.ElapsedMilliseconds+15000; }
        if (frame+1<limit) NextFrame(bink);
      }
      video.Flush();
      return 0;
    } catch (Exception ex) { Console.Error.WriteLine(ex);return 1; }
    finally {
      if (audio!=null) foreach (FileStream stream in audio) if (stream!=null) stream.Dispose();
      if (tracks!=null) foreach (IntPtr track in tracks) if (track!=IntPtr.Zero) CloseTrack(track);
      if (bink!=IntPtr.Zero) Close(bink);
      if (pixels!=IntPtr.Zero) VirtualFree(pixels,UIntPtr.Zero,0x8000);
      if (pcm!=IntPtr.Zero) VirtualFree(pcm,UIntPtr.Zero,0x8000);
    }
  }
}
