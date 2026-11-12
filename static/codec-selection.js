"use strict";
// The actual WebRTC offer is authoritative, including browser-specific limits.
const FastVideoCodec = {
  select(available, sdp, preferred=null) {
    const offered=new Set();let video=false;
    for(const raw of sdp.split(/\r?\n/)) {
      const line=raw.trim();
      if(line.startsWith("m="))video=line.startsWith("m=video ");
      if(video&&line.startsWith("a=rtpmap:")) {
        const match=line.match(/^a=rtpmap:\d+\s+([^/]+)\//i);
        if(match)offered.add(match[1].toLowerCase());
      }
    }
    const priority=preferred&&available.includes(preferred)?[preferred,...available.filter(c=>c!==preferred)]:available;
    return priority.find(codec=>offered.has(codec))||null;
  }
};
